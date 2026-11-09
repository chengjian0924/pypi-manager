# 方案 B：Docker 部署（不依赖虚拟机部署内容）

用 `Dockerfile` + `compose.yaml` 把同一份 `backend/` 与 `frontend/` 打成镜像运行。
本方案与 [`../vm/`](../vm/README.md) **没有任何共享配置文件、共享脚本或相互引用**：
Docker 构建时通过 `Dockerfile.dockerignore` 将 `deploy/vm/` 排除在构建上下文之外，
`Dockerfile` 的 `COPY` 清单也只允许 `backend/`、`frontend/` 和本目录下的两个运行时文件进入镜像。

## 1. 运行依赖

| 依赖 | 版本 / 说明 |
|---|---|
| Docker Engine | **≥ 24**（需要 BuildKit 与 `Dockerfile.dockerignore` 支持） |
| Docker Compose | **v2**（`docker compose version`） |
| 基础镜像 | `python:3.12-slim`，可用 `DK_BASE_IMAGE` 换成内网镜像仓库地址 |
| pip 包 | **无**，`Dockerfile` 不执行 `pip install`，镜像内只有 Python 标准库（`sqlite3`/`ssl`/`http.server`） |
| 宿主工具 | 仅 `docker`；`bin/verify.sh` 的容器内检查不需要宿主 Python/curl |

```bash
docker -v
docker compose version
docker info >/dev/null && echo "守护进程可用"
```

**隔离网 / 离线环境**：在构建机打包镜像，再拷到内网主机导入。

镜像架构**必须与内网主机一致**：服务器是 `aarch64` 就构建 `linux/arm64`，否则 `docker load` 进去也是 `exec format error`。
本镜像只有 Python 标准库、没有编译产物，所以同架构下可以任意机器搬运。

```bash
# 构建侧（默认输出到 deploy/docker/dist/，架构跟随本机）
deploy/docker/bin/export-image.sh
deploy/docker/bin/export-image.sh --platform linux/arm64   # 显式指定（在 x86 机器上构建 arm64 需要 QEMU）

# 产物
#   wheelhouse-intranet_1.1-arm64.tar   业务镜像
#   python_3.12-slim-arm64.tar          基础镜像（内网还要重新构建时用）
#   SHA256SUMS / LOAD.md                校验值与内网操作步骤

# 内网侧
cd dist && sha256sum -c SHA256SUMS
docker load -i wheelhouse-intranet_1.1-arm64.tar
docker load -i python_3.12-slim-arm64.tar
docker image inspect -f '{{.Os}}/{{.Architecture}}' wheelhouse-intranet:1.1   # 必须 linux/arm64

# .env 里 DK_BUILD_ON_START=0 后直接启动，不再构建
deploy/docker/bin/start.sh
```

## 2. 路径、卷与端口

| 用途 | 容器内路径 | 宿主机 / 配置项 |
|---|---|---|
| 应用代码 | `/app/backend/`、`/app/frontend/` | 构建时 `COPY` 进镜像（只读运行） |
| 启动逻辑 | `/app/entrypoint.sh` | 来自 `deploy/docker/entrypoint.sh` |
| 健康检查 | `/app/healthcheck.py` | 来自 `deploy/docker/healthcheck.py` |
| 数据目录（`pypi.db`、`packages/`、`.trash/`、`.tmp/`、`audit.jsonl`） | `/data` | `DK_DATA_SOURCE`，默认命名卷 `wheelhouse-data` |
| 备份目录 | `/backup` | `DK_BACKUP_SOURCE`，默认命名卷 `wheelhouse-backup` |
| TLS 证书（只读） | `/tls` | `DK_TLS_DIR`，默认 `deploy/docker/runtime/tls` |
| 配置文件 | 不进镜像 | `deploy/docker/.env` |

| 端口 | 默认值 | 说明 |
|---|---|---|
| 宿主发布端口 | `DK_HOST_BIND:DK_HOST_PORT` = `127.0.0.1:8081` | 与 VM 方案的 8080 刻意错开，可同时验证 |
| 容器监听端口 | `DK_CONTAINER_PORT` = `8080`（容器内 `0.0.0.0`） | 由 `WH_LISTEN_PORT` 传给 `app.py` |

`app.py` 对非回环地址会强制要求 TLS，而容器必须监听 `0.0.0.0`。
因此默认 `DK_ALLOW_INSECURE_HTTP=1` 表示「TLS 已在容器外的网关/反代终止」，`entrypoint.sh` 会显式追加 `--insecure-http` 并打印警告。
若希望容器自己提供 HTTPS，请挂载证书并填 `DK_TLS_CERT` / `DK_TLS_KEY`（见第 6 节）。

> **默认只有宿主机能访问。** `DK_HOST_BIND=127.0.0.1` 是给「前面挂 Nginx/网关」准备的安全默认值，
> 同事的 pip 直接连 `http://<服务器IP>:8081` 会连不上——这不是故障。
> 要让局域网内其他机器直接访问，把 `.env` 的 `DK_HOST_BIND` 改成 `0.0.0.0` 后重新执行 `bin/start.sh`：
>
> ```bash
> # 方案一（推荐）：本机 Nginx 监听 443，反代到 127.0.0.1:8081，TLS 在 Nginx 终止
> #                 此时 DK_HOST_BIND 保持 127.0.0.1，同事访问 https://pypi.intra.example.com/simple/
> # 方案二：容器直接对外
> DK_HOST_BIND=0.0.0.0
> DK_HOST_PORT=8080
> # 明文 HTTP + Basic 认证 = 口令可被抓包解出；内网也建议填 DK_TLS_CERT/DK_TLS_KEY 走 HTTPS
> ```
>
> 改完记得放行宿主防火墙（`firewall-cmd --add-port=8080/tcp` 或 `ufw allow 8080/tcp`），
> 并用另一台机器 `curl http://<服务器IP>:8080/health` 实测，不要只看本机是否通。

> 命名卷是推荐默认值：数据不落在项目目录里，容器重建/升级不丢包。
> 若改成宿主机绝对路径（例如 `/srv/wheelhouse/data`），需先 `chown -R 10001:10001` 该目录，或把 `DK_UID/DK_GID` 改成目录属主。

## 3. 环境变量

### 3.1 宿主机 / Compose 层（`deploy/docker/.env`，前缀 `DK_*`）

| 变量 | 默认值 | 用途 |
|---|---|---|
| `DK_BASE_IMAGE` | `python:3.12-slim` | 构建参数 `BASE_IMAGE` |
| `DK_IMAGE_NAME` / `DK_IMAGE_TAG` | `wheelhouse-intranet` / `1.1` | 镜像名与标签 |
| `DK_CONTAINER_NAME` | `wheelhouse` | 容器名；Compose 项目名固定 `wheelhouse-docker` |
| `DK_RESTART_POLICY` | `unless-stopped` | 重启策略 |
| `DK_BUILD_ON_START` | `1` | `start.sh` 是否每次重建镜像；离线导入镜像后置 `0` |
| `DK_UID` / `DK_GID` | `10001` / `10001` | 容器进程身份，与镜像内文件属主一致 |
| `DK_TZ` | `Asia/Shanghai` | 容器内时区 |
| `DK_HOST_BIND` / `DK_HOST_PORT` / `DK_CONTAINER_PORT` | `127.0.0.1` / `8081` / `8080` | 端口映射 |
| `DK_DATA_SOURCE` / `DK_BACKUP_SOURCE` | `wheelhouse-data` / `wheelhouse-backup` | 命名卷名，或宿主机绝对路径 |
| `DK_TLS_DIR` | `./runtime/tls` | 只读挂载到容器 `/tls` |
| `DK_TLS_CERT` / `DK_TLS_KEY` | 空 | 容器内证书路径（如 `/tls/fullchain.pem`） |
| `DK_REQUIRE_AUTH_FOR_READ` | `1` | 映射到 `WH_REQUIRE_AUTH_FOR_READ` |
| `DK_ALLOW_INSECURE_HTTP` | `1` | 映射到 `WH_ALLOW_INSECURE_HTTP` |
| `DK_MAX_UPLOAD_MB` / `DK_MAX_WORKERS` / `DK_MIN_FREE_MB` | `512` / `64` / `2048` | 容量与并发 |
| `DK_BACKUP_INTERVAL_HOURS` / `DK_BACKUP_KEEP` | `24` / `14` | 自动备份 |
| `DK_TRASH_DAYS` / `DK_TRASH_MAX_MB` | `30` / `2048` | 回收站策略 |
| `DK_AUDIT_MAX_MB` / `DK_AUDIT_KEEP` | `10` / `5` | 审计日志轮转 |
| `DK_LOG_LEVEL` | `INFO` | 运行日志级别 |
| `DK_LOG_JSON` | `0` | `1` = 单行 JSON，便于 Loki/ES 采集 |
| `DK_SLOW_MS` | `1000` | 超过该耗时的请求记 WARNING |
| `DK_LOG_MAX_SIZE` / `DK_LOG_MAX_FILE` | `20m` / `5` | 容器日志单文件上限与份数（json-file driver） |
| `DK_INIT_USER` / `DK_INIT_PASSWORD` / `DK_INIT_ROLE` / `DK_INIT_SCOPE` | `admin` / 空 / `admin` / `*` | `bootstrap.sh` 首次建号 |
| `DK_VERIFY_USER` / `DK_VERIFY_PASSWORD` | 空 | `verify.sh` 登录态检查（可留空跳过） |
| `DK_ENV_FILE` | `deploy/docker/.env` | 环境变量覆盖：指定其他 `.env` 路径 |

### 3.2 容器内（`compose.yaml` 注入，前缀 `WH_*`）

由 `entrypoint.sh` 翻译成 `app.py` 参数，`Dockerfile` 里固定了 `WH_DATA_DIR=/data`、`WH_FRONTEND_DIR=/app/frontend`、`WH_BACKUP_DIR=/backup`：

`WH_LISTEN_HOST`（固定 `0.0.0.0`）、`WH_LISTEN_PORT`、`WH_DATA_DIR`、`WH_FRONTEND_DIR`、`WH_BACKUP_DIR`、`WH_TLS_CERT`、`WH_TLS_KEY`、`WH_ALLOW_INSECURE_HTTP`、`WH_REQUIRE_AUTH_FOR_READ`、`WH_MAX_UPLOAD_MB`、`WH_MAX_WORKERS`、`WH_MIN_FREE_MB`、`WH_BACKUP_INTERVAL_HOURS`、`WH_BACKUP_KEEP`、`WH_TRASH_DAYS`、`WH_TRASH_MAX_MB`、`WH_AUDIT_MAX_MB`、`WH_AUDIT_KEEP`、`WH_LOG_LEVEL`、`WH_LOG_JSON`、`WH_SLOW_MS`。

另有 `PYTHONDONTWRITEBYTECODE=1`、`PYTHONUNBUFFERED=1`、`TZ`。

> VM 方案的 `VM_WH_*` 变量与本方案完全不通，改其中一套不会影响另一套。

### 3.3 日志

| 日志 | 内容 | 位置 |
|---|---|---|
| 访问日志 | IP、用户、请求行、状态码、字节数、耗时；慢请求 `[slow]`，4xx/5xx 升级级别 | 容器 stdout/stderr → `docker logs` |
| 运行日志 | 启动参数、对账、自动备份、回收站清理、异常栈 | 同上 |
| 审计日志 | 结构化 JSONL（登录/上传/删除/恢复/彻底删除/改密/建号/备份/`server_error`） | 命名卷 `wheelhouse-data` 内 `audit.jsonl`，自带轮转 |

- **容器日志默认无限增长，必须限容**：`compose.yaml` 已固定 `json-file` + `max-size/max-file`（`DK_LOG_MAX_SIZE` / `DK_LOG_MAX_FILE`）。改这两项后要 `docker compose up -d` 重建容器才生效，`restart` 不会更新 logging 配置。
- 需要采集时设 `DK_LOG_JSON=1`，每行一个 JSON（含 `request_id`/`user`/`status`/`duration_ms`），无需正则解析；`request_id` 同时出现在 500 响应体的「错误编号」里，便于用户报障后定位。
- 查看：`deploy/docker/bin/logs.sh`（本质是 `docker compose logs -f --tail=100`）。若需要长期留存，建议在宿主机加采集器，而不是调大 `DK_LOG_MAX_FILE`。

## 4. 快速开始

```bash
cd <仓库根目录>

# 1) 生成本方案唯一配置文件（含密码，.gitignore 已排除入库）
cp deploy/docker/.env.example deploy/docker/.env
chmod 600 deploy/docker/.env
vi deploy/docker/.env

# 2) 启动：首次自动构建镜像并 up -d，等待 HEALTHCHECK=healthy
deploy/docker/bin/start.sh

# 3) 首次建号（可选；要求主容器已停止、镜像已构建；密码交互式输入）
deploy/docker/bin/stop.sh
deploy/docker/bin/bootstrap.sh
deploy/docker/bin/start.sh

# 4) 独立验证 / 状态 / 日志
deploy/docker/bin/verify.sh
deploy/docker/bin/status.sh
deploy/docker/bin/logs.sh          # Ctrl+C 退出，不影响容器

# 5) 停止（保留镜像与数据卷；同时暂停看门狗，见第 8 节）
deploy/docker/bin/stop.sh

# 6) 备份盘点 / 回滚（详见第 5 节）
deploy/docker/bin/restore.sh --list
deploy/docker/bin/restore.sh backup-20261109-090000
```

所有脚本都等价于一条显式命令：
`docker compose --env-file deploy/docker/.env -f deploy/docker/compose.yaml <子命令>`，
可直接手工调用，不依赖 VM 方案的任何脚本。

## 5. 数据持久化、备份与恢复

- 数据与备份分别在 `wheelhouse-data`、`wheelhouse-backup` 两个命名卷；`stop.sh` 只停容器，**不会删除卷**。
- 每日自动备份写入 `/backup`（`DK_BACKUP_INTERVAL_HOURS=24`），保留 14 份；备份卷应落在与数据卷不同的物理磁盘。
- 彻底删除（含全部包）：`docker compose --env-file deploy/docker/.env -f deploy/docker/compose.yaml down -v` — **不可逆，慎用**。
- 导出数据卷：`docker run --rm -v wheelhouse-data:/src -v "$PWD":/out busybox tar czf /out/wheelhouse-data.tgz -C /src .`

**备份盘点与回滚**（备份点就在 `wheelhouse-backup` 卷里，容器内路径 `/backup`）：

```bash
# 列出备份点：创建时间、包文件数、大小、完整性（只读，容器运行中也能跑）
deploy/docker/bin/restore.sh --list

# 回滚：停容器 → 容器内归档现有数据 → 恢复 → 启动 → 检查
deploy/docker/bin/restore.sh backup-20261109-090000
# 无人值守加 --yes；备份缺包仍要恢复再加 --force
```

- 现有数据会先被移进容器内 `/data/.rollback-<时间戳>`（卷里），确认无误后可清理；
  `restore.sh` 结束时会打印清理命令。
- 备份缺包时脚本默认**拒绝**并打印缺哪些文件；加 `--force` 才会恢复残缺数据，
  缺的包在启动后会被标记为丢失。
- 恢复失败会自动把归档放回原位，服务不会停在半恢复状态。
- `restore.sh` 在一次性容器里执行 `--restore`，**不经过 `entrypoint.sh`**，因此不受监听地址/TLS 检查影响；需要镜像已包含新版 `backend/`（旧镜像请先 `bootstrap.sh` 重建）。

## 6. TLS

**方式一：容器外终止（默认）**。保持 `DK_ALLOW_INSECURE_HTTP=1`，用宿主机 Nginx / 网关把 443 反代到 `127.0.0.1:8081`。Nginx 需设置 `client_max_body_size 512m;` 与 `proxy_request_buffering off;`。

**方式二：容器内 HTTPS**。把证书放进 `DK_TLS_DIR` 指向的目录（容器内为 `/tls`），然后：

```bash
DK_TLS_CERT=/tls/fullchain.pem
DK_TLS_KEY=/tls/privkey.pem
DK_ALLOW_INSECURE_HTTP=0
DK_HOST_PORT=8443
```

`entrypoint.sh` 会加 `--tls-cert/--tls-key`，HEALTHCHECK 自动改用 `https://127.0.0.1:<port>/health`。
证书文件需对 UID `10001` 可读（`chmod 644`）。修改 `.env` 后重新执行 `start.sh`，Compose 会用新环境变量重建容器。

## 7. 独立验证与故障排查

`deploy/docker/bin/verify.sh` 只读检查：Docker/Compose 可用性、容器运行、镜像匹配、端口映射、HEALTHCHECK、宿主机经映射端口访问 `/health`，以及**在容器内部**用镜像自带 Python 检查 `/health`、`/api/config`、首页、ES Module、PEP 503 索引、鉴权策略、`/data`、`/backup` 与 `WH_*` 环境变量。设置 `DK_VERIFY_USER/PASSWORD` 后还会检查登录态。不上传、不删除任何业务数据。

| 现象 | 排查 |
|---|---|
| `Dockerfile.dockerignore` 不生效 / 构建上下文很大 | 新版 Docker 支持 Dockerfile 专属 ignore；旧版可在仓库根放一份同内容的 `.dockerignore`。无论哪种，`COPY` 清单都保证 `deploy/vm/` 不进镜像 |
| 容器反复重启 | `deploy/docker/bin/logs.sh`；常见原因是 `/data` 不可写（bind 挂载未 chown 到 `DK_UID:DK_GID`） |
| 健康检查 `unhealthy` | `docker inspect --format '{{json .State.Health}}' wheelhouse`；确认 `WH_LISTEN_PORT` 与 `DK_CONTAINER_PORT` 一致 |
| 端口冲突 | 改 `DK_HOST_PORT`；默认 `8081` 与 VM 方案 `8080` 不冲突 |
| 离线环境构建失败 | 先 `docker load -i python312-slim.tar`，并将 `DK_BASE_IMAGE` 指向已导入的镜像 |
| 修改 `.env` 后没生效 | 重新执行 `bin/start.sh`，让 Compose 用新环境变量重建容器 |
| 忘记管理员密码 | `stop.sh` → `bootstrap.sh`（`--init-user` 会重置该账号）→ `start.sh` |

## 8. 24×7 无人值守运行

| 层 | 组件 | 解决什么 |
|---|---|---|
| 1 | `compose.yaml` 的 `restart: unless-stopped` | 容器内主进程自行退出（未捕获异常、OOM）→ Docker 立刻重启容器 |
| 2 | 宿主机 `systemctl enable docker` | 宿主机重启 → Docker 守护进程先起，再按重启策略恢复容器 |
| 3 | `systemd/wheelhouse-docker-watchdog.{service,timer}.example` + `bin/watchdog.sh` | ① 容器仍 Running 但 `HEALTHCHECK=unhealthy`（假死）；② `docker kill` / `docker stop` 被 Docker 视为显式停止、`unless-stopped` 不再拉起；③ 容器被 `docker rm` 误删 |

> Docker 原生**不会因为 HEALTHCHECK 变 unhealthy 而重启容器**（只有 Swarm 会），这一层必须由看门狗补上。实测也确认：`docker kill` 之后容器停在 `exited`、`RestartCount=0`，只有看门狗能把它拉回。

### 安装

```bash
sudo systemctl enable docker          # 关键：守护进程开机自启
sudo install -d -m 0750 /etc/wheelhouse-docker
sudo install -m 0600 deploy/docker/.env /etc/wheelhouse-docker/.env
sudo cp deploy/docker/systemd/wheelhouse-docker-watchdog.service.example \
        /etc/systemd/system/wheelhouse-docker-watchdog.service
sudo cp deploy/docker/systemd/wheelhouse-docker-watchdog.timer.example \
        /etc/systemd/system/wheelhouse-docker-watchdog.timer
# 编辑单元里的 DK_ENV_FILE 与 ExecStart，使其与实际部署路径一致
sudo systemctl daemon-reload
sudo systemctl enable --now wheelhouse-docker-watchdog.timer

systemctl list-timers wheelhouse-docker-watchdog.timer
journalctl -u wheelhouse-docker-watchdog.service -n 50
deploy/docker/bin/verify.sh
```

看门狗服务以 **root** 运行（需要访问 `/var/run/docker.sock`），只读 `.env`、在 `deploy/docker/runtime/` 写状态文件。它与 `deploy/vm/systemd/` 下的单元**同名不同物、互不引用**，只装自己这一份即可。

### 看门狗配置（`.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DK_RESTART_POLICY` | `unless-stopped` | 不要改成 `no`；`always` 会在手工 stop 后又被 daemon 拉起，一般不如 `unless-stopped` |
| `DK_WATCHDOG_FAILS` | `3` | 连续几次探测失败才恢复（timer 30s 一次，约 90s） |
| `DK_WATCHDOG_COOLDOWN` | `120` | 两次恢复之间的最小间隔（秒） |
| `DK_WATCHDOG_MAX_RECOVERIES` | `3` | 连续恢复仍不健康就熔断，停止自动重启并告警 |
| `DK_WATCHDOG_WAIT_READY` | `60` | 恢复后等待 `healthy` 的秒数（容器重建后 HEALTHCHECK 有 15s start-period + 30s 间隔） |
| `DK_WATCHDOG_INTERVAL` | `30` | 仅 `--loop` 模式的间隔 |
| `DK_WATCHDOG_PROBE_CMD` | 空 | 自定义探测（留空 = 容器 HEALTHCHECK 状态，只需 docker）；可从外部网关探 HTTPS |
| `DK_WATCHDOG_ALERT_CMD` | 空 | 告警钩子，消息在 `WATCHDOG_MESSAGE` 环境变量里 |
| `DK_WATCHDOG_STATE_FILE` / `DK_WATCHDOG_PAUSE_FILE` | `runtime/watchdog.state` / `runtime/watchdog.pause` | 状态与暂停标记（已被 `.gitignore` 排除） |

恢复动作按容器状态自动选择：容器不存在 → `compose up -d`（不构建镜像）；已停止 → `compose start`；Running 但不健康 → `compose restart`。

```bash
deploy/docker/bin/watchdog.sh --status   # 查看失败/恢复/熔断计数
deploy/docker/bin/watchdog.sh --reset    # 处理完根因后复位熔断
deploy/docker/bin/watchdog.sh            # 单次检查（timer/cron）
deploy/docker/bin/watchdog.sh --loop     # 无 systemd 时常驻守护
```

### 计划内停机

`bin/stop.sh` 会**自动写** `runtime/watchdog.pause`，`bin/start.sh` 启动时**自动清除**，所以走这两个脚本做维护不会被看门狗打断：

```bash
sudo systemctl stop wheelhouse-docker-watchdog.timer   # 可选，双保险
deploy/docker/bin/stop.sh
# ... 升级镜像 / 恢复备份 ...
deploy/docker/bin/start.sh
sudo systemctl start wheelhouse-docker-watchdog.timer
```

如果直接 `docker compose stop` 而不走 `bin/stop.sh`，没有暂停标记，看门狗会把它当作意外停止并在约 90s 后拉起 —— 这是「24×7 不停」预期行为，维护时请记得用 `bin/stop.sh` 或手动 `touch deploy/docker/runtime/watchdog.pause`。

### 监控与边界

- 把 `/health`、`docker inspect` 的 health 状态与 `journalctl -u wheelhouse-docker-watchdog.service` 接入告警；出现「熔断」日志即需人工介入。
- 这一整套解决的是**容器级**故障。宿主机宕机、磁盘损坏、Docker 守护进程无法启动只能靠备份恢复；本应用单进程 + 单 SQLite + 本地包文件，**不支持多实例同时写**（两个容器挂同一 `/data` 会写锁竞争），需要零停机必须做主备 + 共享存储 + 前置反代，并保证同一时刻只有一个写实例。
