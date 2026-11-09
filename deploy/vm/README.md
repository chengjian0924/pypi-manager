# 方案 A：内网虚拟机直接部署（不依赖 Docker）

直接在 Linux 虚拟机/物理机上以 Python 进程运行 `backend/app.py`，前端静态资源由同一进程提供。
本方案与 [`../docker/`](../docker/README.md) **没有任何共享文件、共享脚本或相互引用**。

## 1. 运行依赖

| 依赖 | 版本 / 说明 |
|---|---|
| 操作系统 | Linux（Ubuntu 22.04+ / Rocky 9 / Debian 12+ 均可）、x86_64 或 arm64 |
| Python | **≥ 3.10**（CI 覆盖 3.10 / 3.13）；只用标准库：`http.server`、`sqlite3`、`ssl`、`hashlib` |
| SQLite | **≥ 3.24**（用到部分索引、`ON CONFLICT DO UPDATE`），随 Python 自带 |
| pip 包 | **无**，不需要 `requirements.txt`，不创建 venv，隔离网可离线运行 |
| 可选工具 | `curl`（客户端配置/排障）、`openssl`（生成测试证书）、`nginx`（推荐，终止 TLS） |

```bash
# Ubuntu / Debian
sudo apt-get update && sudo apt-get install -y python3 python3-venv ca-certificates curl nginx
# Rocky / Alma / RHEL
sudo dnf install -y python3 ca-certificates curl nginx
python3 -V && python3 -c 'import sqlite3; print(sqlite3.sqlite_version)'
```

建议创建专用系统账号并准备目录（路径与 `wheelhouse.env` 默认值一致）：

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin wheelhouse
sudo install -d -o wheelhouse -g wheelhouse -m 0750 \
  /var/lib/wheelhouse /var/backups/wheelhouse /var/log/wheelhouse /etc/wheelhouse
sudo install -d -o wheelhouse -g wheelhouse -m 0755 /opt/wheelhouse
sudo cp -a backend frontend deploy /opt/wheelhouse/app/   # 仓库内容部署到此
sudo chown -R wheelhouse:wheelhouse /opt/wheelhouse/app
```

> 数据目录必须放在**本地盘**且开机即挂载。`app.py` 启动对账会把「物理文件缺失」的在用记录标记为已删除；若放在延迟挂载的 NFS 上，可能导致索引被误清空。若必须用网络存储，请用 systemd `RequiresMountsFor=` 保证挂载顺序。

## 2. 路径与端口

| 用途 | 默认路径 | 配置项 |
|---|---|---|
| 仓库根（`backend/`、`frontend/`） | `/opt/wheelhouse/app` | `VM_WH_APP_DIR` |
| 数据目录：`pypi.db`、`packages/`、`.trash/`、`.tmp/`、`.orphans/`、`audit.jsonl` | `/var/lib/wheelhouse` | `VM_WH_DATA_DIR` |
| 备份输出（`backup-YYYYmmdd-HHMMSS/`） | `/var/backups/wheelhouse` | `VM_WH_BACKUP_DIR` |
| 日志 `wheelhouse.log` | `/var/log/wheelhouse` | `VM_WH_LOG_DIR` |
| PID 文件 `wheelhouse.pid` | `/run/wheelhouse` | `VM_WH_RUN_DIR` |
| 前端静态资源 | `$VM_WH_APP_DIR/frontend` | `VM_WH_FRONTEND_DIR` |
| 配置文件 | `deploy/vm/config/wheelhouse.env` | 环境变量 `VM_WH_CONF_FILE` |

| 端口 | 默认值 | 说明 |
|---|---|---|
| HTTP 服务 | `VM_WH_HOST:VM_WH_PORT` = `127.0.0.1:8080` | 反代终止 TLS 时保持回环监听 |
| 对外 HTTPS | `443`（Nginx，见 `nginx/wheelhouse.conf.example`） | 与后端分离 |

`app.py` 内置安全校验：监听非回环地址且未配置 TLS 时拒绝启动；本方案的 `start.sh` 会提前做同样判断并给出可操作的报错。

## 3. 环境变量（全部来自 `config/wheelhouse.env`）

统一前缀 `VM_WH_*`，只被 `deploy/vm/bin/*.sh` 读取。

| 变量 | 默认值 | 映射到 `app.py` / 用途 |
|---|---|---|
| `VM_WH_CONF_FILE` | `deploy/vm/config/wheelhouse.env` | 指定配置文件位置（systemd 常用 `/etc/wheelhouse/wheelhouse.env`） |
| `VM_WH_PYTHON` | `/usr/bin/python3` | 解释器路径 |
| `VM_WH_APP_DIR` | 脚本所在仓库根 | 定位 `backend/app.py` |
| `VM_WH_DATA_DIR` | `/var/lib/wheelhouse` | `--data` |
| `VM_WH_BACKUP_DIR` | `/var/backups/wheelhouse` | `--backup-dir` |
| `VM_WH_LOG_DIR` | `/var/log/wheelhouse` | 日志重定向 |
| `VM_WH_RUN_DIR` | `/run/wheelhouse` | PID 文件 |
| `VM_WH_FRONTEND_DIR` | `$VM_WH_APP_DIR/frontend` | `--frontend-dir` |
| `VM_WH_TLS_CERT` / `VM_WH_TLS_KEY` | 空 | `--tls-cert` / `--tls-key`（直连 HTTPS） |
| `VM_WH_HOST` / `VM_WH_PORT` | `127.0.0.1` / `8080` | `--host` / `--port` |
| `VM_WH_ALLOW_INSECURE_HTTP` | `0` | 非回环明文监听时追加 `--insecure-http` |
| `VM_WH_REQUIRE_AUTH_FOR_READ` | `1` | `1`→`--require-auth-for-read`；`0`→`--allow-anonymous-read`（两者互斥，脚本永远只传其一） |
| `VM_WH_MAX_UPLOAD_MB` | `512` | `--max-upload-mb` |
| `VM_WH_MAX_WORKERS` | `64` | `--max-workers` |
| `VM_WH_MIN_FREE_MB` | `2048` | `--min-free-mb` |
| `VM_WH_BACKUP_INTERVAL_HOURS` | `24` | `--backup-interval-hours` |
| `VM_WH_BACKUP_KEEP` | `14` | `--backup-keep` |
| `VM_WH_TRASH_DAYS` / `VM_WH_TRASH_MAX_MB` | `30` / `2048` | `--trash-days` / `--trash-max-mb` |
| `VM_WH_AUDIT_MAX_MB` / `VM_WH_AUDIT_KEEP` | `10` / `5` | `--audit-max-mb` / `--audit-keep` |
| `VM_WH_LOG_LEVEL` | `INFO` | `--log-level`（DEBUG/INFO/WARNING/ERROR） |
| `VM_WH_LOG_JSON` | `0` | `1`→`--log-json`（单行 JSON，便于采集） |
| `VM_WH_SLOW_MS` | `1000` | `--slow-ms`（超过该耗时的请求记 WARNING） |
| `VM_WH_INIT_USER` / `VM_WH_INIT_PASSWORD` / `VM_WH_INIT_ROLE` / `VM_WH_INIT_SCOPE` | `admin` / 空 / `admin` / `*` | `init-admin.sh` → `--init-user` `--role` `--scope` |
| `VM_WH_VERIFY_USER` / `VM_WH_VERIFY_PASSWORD` | 空 | `verify.sh` 的登录态检查（可留空跳过） |
| `VM_WH_SERVICE_USER` / `VM_WH_STOP_TIMEOUT` | `wheelhouse` / `30` | systemd 单元运行用户；`stop.sh` 优雅停机超时（秒） |

## 4. 快速开始

```bash
cd /opt/wheelhouse/app

# 1) 生成本方案唯一配置文件（含密码，.gitignore 已排除入库）
cp deploy/vm/config/wheelhouse.env.example deploy/vm/config/wheelhouse.env
chmod 600 deploy/vm/config/wheelhouse.env
vi deploy/vm/config/wheelhouse.env        # 改路径、端口、读权限、TLS 等

# 2) 首次初始化管理员（服务运行中会拒绝执行；密码交互式输入）
sudo -u wheelhouse deploy/vm/bin/init-admin.sh

# 3) 启动（后台 + 等待 /health 就绪 + 写 PID 文件）
sudo -u wheelhouse deploy/vm/bin/start.sh

# 4) 独立验证 / 查看状态
deploy/vm/bin/verify.sh
deploy/vm/bin/status.sh

# 5) 停止（SIGTERM 优雅停机，超时才 SIGKILL；数据不动；同时暂停看门狗，见第 5 节）
sudo -u wheelhouse deploy/vm/bin/stop.sh

# 6) 备份盘点 / 回滚（详见第 7 节）
sudo -u wheelhouse deploy/vm/bin/restore.sh --list
sudo -u wheelhouse deploy/vm/bin/restore.sh backup-20261109-090000
```

`start.sh --foreground` 会前台 `exec` 后端进程，供 systemd 使用；两条路径共用同一份参数映射逻辑。

## 5. 24×7 无人值守运行（推荐生产使用）

只用 `bin/start.sh`（nohup + PID 文件）时**没有进程守护**：进程一退服务就没了。生产环境请用下面三层，各管一类故障、互不重叠：

| 层 | 组件 | 解决什么 |
|---|---|---|
| 1 | `systemd/wheelhouse.service.example` 的 `Restart=always` + `RestartSec=5` | 进程崩溃、被 OOM killer 杀掉、意外以 exit 0 退出 → 5s 内拉起 |
| 2 | `systemctl enable --now wheelhouse`（`WantedBy=multi-user.target`） | 宿主机重启后自动启动 |
| 3 | `systemd/wheelhouse-watchdog.{service,timer}.example` + `bin/watchdog.sh` | 进程还在但 `/health` 不响应（线程池耗尽、SQLite 长锁等假死）→ 主动 `systemctl restart` |

`StartLimitIntervalSec=300` + `StartLimitBurst=10` 限制 5 分钟内最多重启 10 次；看门狗另有自己的熔断，避免「重启也修不好」时无限重启。

### 安装

```bash
# 配置文件：属主 root、属组 wheelhouse、权限 0640，保证服务账号可读、其他用户不可读
sudo install -o root -g wheelhouse -m 0640 \
  deploy/vm/config/wheelhouse.env /etc/wheelhouse/wheelhouse.env

sudo cp deploy/vm/systemd/wheelhouse.service.example \
        /etc/systemd/system/wheelhouse.service
sudo cp deploy/vm/systemd/wheelhouse-watchdog.service.example \
        /etc/systemd/system/wheelhouse-watchdog.service
sudo cp deploy/vm/systemd/wheelhouse-watchdog.timer.example \
        /etc/systemd/system/wheelhouse-watchdog.timer
# 编辑三个单元里的 VM_WH_CONF_FILE 与 ExecStart 路径，使其与实际部署路径一致
sudo systemctl daemon-reload
sudo systemctl enable --now wheelhouse
sudo systemctl enable --now wheelhouse-watchdog.timer

sudo systemctl status wheelhouse
systemctl list-timers wheelhouse-watchdog.timer
journalctl -u wheelhouse -f
journalctl -u wheelhouse-watchdog.service -n 50
```

主单元用 `StateDirectory` / `LogsDirectory` / `RuntimeDirectory` 自动创建并授权三个目录，`RequiresMountsFor=` 保证数据盘先挂载。看门狗服务以 **root** 运行（默认恢复命令是 `systemctl restart wheelhouse`，普通用户无权执行），它只读配置、在 `/run/wheelhouse` 写状态文件，不碰业务数据。使用 systemd 后请用 `systemctl start/stop wheelhouse`，不要再混用 `bin/start.sh`（后台模式）。

### 看门狗配置（`config/wheelhouse.env`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `VM_WH_SERVICE_UNIT` | `wheelhouse` | 默认恢复命令据此生成 `systemctl restart <单元名>` |
| `VM_WH_WATCHDOG_RECOVER_CMD` | 空 | 覆盖恢复命令；**不用 systemd** 时填 `bin/start.sh` 绝对路径（含空格要加引号） |
| `VM_WH_WATCHDOG_FAILS` | `3` | 连续几次探测失败才恢复（timer 30s 一次，约 90s） |
| `VM_WH_WATCHDOG_COOLDOWN` | `120` | 两次恢复之间的最小间隔（秒） |
| `VM_WH_WATCHDOG_MAX_RECOVERIES` | `3` | 连续恢复这么多次仍不健康就熔断，停止自动重启并告警 |
| `VM_WH_WATCHDOG_WAIT_READY` | `45` | 恢复后等待 `/health` 重新就绪的秒数 |
| `VM_WH_WATCHDOG_INTERVAL` | `30` | 仅 `--loop` 模式的间隔；systemd 下由 timer 的 `OnUnitActiveSec` 控制 |
| `VM_WH_WATCHDOG_PROBE_CMD` | 空 | 自定义探测命令（留空 = 内置 `/health`，自动绕过全局代理），可从外部网关地址探 HTTPS |
| `VM_WH_WATCHDOG_ALERT_CMD` | 空 | 告警钩子，消息在 `WATCHDOG_MESSAGE` 环境变量里；含 `$` 请写单引号 |
| `VM_WH_WATCHDOG_PAUSE_FILE` | `/run/wheelhouse/watchdog.pause` | 存在即暂停自愈（计划内停机） |

```bash
deploy/vm/bin/watchdog.sh --status   # 查看失败/恢复/熔断计数
deploy/vm/bin/watchdog.sh --reset    # 人工处理根因后复位熔断计数
deploy/vm/bin/watchdog.sh            # 单次检查（timer/cron 调用）
deploy/vm/bin/watchdog.sh --loop     # 无 systemd 时常驻守护
```

**没有 systemd 的环境**：把 `VM_WH_WATCHDOG_RECOVER_CMD` 指向 `bin/start.sh`，另起一个常驻进程跑 `deploy/vm/bin/watchdog.sh --loop`（可用 supervisor / `nohup` 托管它自己）。这样进程被 kill 后看门狗会重新执行 `start.sh`。

### 计划内停机（升级 / 恢复备份 / 迁移）

看门狗无法区分「人工停服」和「意外退出」，所以**停机前必须先暂停看门狗**：

```bash
sudo systemctl stop wheelhouse-watchdog.timer     # 或：sudo touch /run/wheelhouse/watchdog.pause
sudo systemctl stop wheelhouse
# ... 覆盖新版 backend/ + frontend/、恢复备份 ...
sudo systemctl start wheelhouse
sudo systemctl start wheelhouse-watchdog.timer   # 或：sudo rm /run/wheelhouse/watchdog.pause
```

直接执行 `bin/stop.sh` 也会自动写暂停标记，下次 `bin/start.sh` 会自动清除。若只停主服务不停 timer，看门狗会在约 90s 后把服务重新拉起。

### 日志与轮转

三类日志分工不同，排障时按顺序看：

| 日志 | 内容 | 位置 |
|---|---|---|
| 访问日志 | 每请求一行：IP、用户、请求行、状态码、字节数、耗时；慢请求带 `[slow]`，4xx/5xx 自动升为 WARNING/ERROR | stderr（下文两种落点） |
| 运行日志 | 启动参数、对账结果、自动备份、回收站清理、异常栈 | 同上 |
| 审计日志 | 结构化 JSONL：登录、上传、删除、恢复、彻底删除、改密、建号、备份、`server_error`（含错误编号） | `$VM_WH_DATA_DIR/audit.jsonl`（自带 10MB×5 轮转，无需外部工具） |

运行日志的落点取决于启动方式：

- **systemd 托管（推荐）**：进程写 journald，用 `journalctl -u wheelhouse -f`；
  journald 的容量由 `/etc/systemd/journald.conf` 的 `SystemMaxUse=` 控制，**不要**再装 logrotate。
- **`bin/start.sh` 后台模式**：重定向到 `$VM_WH_LOG_DIR/wheelhouse.log`，
  **必须**安装轮转，否则长期运行会写满磁盘：

```bash
sudo cp deploy/vm/config/wheelhouse.logrotate.example /etc/logrotate.d/wheelhouse
sudo logrotate -d /etc/logrotate.d/wheelhouse    # dry-run 确认计划
sudo logrotate -f /etc/logrotate.d/wheelhouse    # 强制执行一次
```

需要对接日志平台时，把 `VM_WH_LOG_JSON` 设为 `1`，每行即一个 JSON 对象
（字段：`ts`/`level`/`logger`/`msg`/`request_id`/`user`，访问日志另含
`ip`/`method`/`path`/`status`/`bytes`/`duration_ms`/`slow`），可直接被
Promtail/Filebeat 采集，无需正则解析。

> `request_id` 会同时出现在该请求的访问日志、异常栈与 500 响应体的「错误编号」里，
> 用户报障时只需提供这一个 8 位编号。

### 监控与边界

- 把 `/health` 与 `journalctl -u wheelhouse-watchdog.service` 接入现有告警；熔断日志出现「已熔断」即表示自动恢复已放弃，必须人工介入。
- 这一整套解决的是**进程级**故障，秒级～2 分钟内恢复。整机宕机、磁盘损坏、断电只能靠 `--backup-dir` 的备份恢复；本应用单进程 + 单 SQLite + 本地包文件，**不支持多实例同时写**，需要真正零停机必须做主备 + 共享存储 + 前置反代，并保证同一时刻只有一个写实例。

## 6. TLS

- **推荐**：保持 `VM_WH_HOST=127.0.0.1`，用前置 Nginx 终止 TLS，参考 [`nginx/wheelhouse.conf.example`](nginx/wheelhouse.conf.example)。注意 `client_max_body_size` 要与 `VM_WH_MAX_UPLOAD_MB` 一致，`proxy_request_buffering off` 让上传流式转发。
- **直连 HTTPS**：把 `VM_WH_HOST` 改为 `0.0.0.0`，填 `VM_WH_TLS_CERT` / `VM_WH_TLS_KEY` 指向 PEM 文件，`start.sh` 会自动加 `--tls-cert/--tls-key`。
- **禁止**把 `VM_WH_ALLOW_INSECURE_HTTP=1` 用于生产：Basic Auth 是 base64 编码，等同明文。

## 7. 备份、恢复与升级

```bash
# 立即备份（在线，也可在管理界面触发）
sudo -u wheelhouse python3 backend/app.py --data /var/lib/wheelhouse \
  --backup-dir /var/backups/wheelhouse --backup-now

# 列出备份点：创建时间、包文件数、大小、完整性（只读，服务运行中也能跑）
sudo -u wheelhouse deploy/vm/bin/restore.sh --list
```

**回滚到某个备份点**（停服 → 归档 → 恢复 → 启动 → 检查，一条命令）：

```bash
sudo -u wheelhouse deploy/vm/bin/restore.sh backup-20261109-090000
# 无人值守：加 --yes；备份缺包仍要恢复：再加 --force
```

- 现有数据（`pypi.db`、`packages/`、`.trash/`、`audit.jsonl`）会先被移进
  `<VM_WH_DATA_DIR>/.rollback-<时间戳>`，**不直接覆盖**；确认无误后手动删除。
- 备份缺包文件时脚本默认**拒绝**回滚并打印缺哪些文件；确实要恢复残缺数据加 `--force`，
  缺的包在服务启动后会被标记为丢失。
- 恢复中途失败（磁盘满、权限）会自动把归档放回原位，不会留下半个数据目录。
- 服务若是 systemd 托管（不是本脚本 PID 文件启动的），脚本会拒绝执行并提示先
  `systemctl stop wheelhouse`，避免往正在写的目录里灌数据。

手工流程仍然可用（适合恢复到另一块盘、先并行核对）：

```bash
sudo -u wheelhouse deploy/vm/bin/stop.sh
sudo -u wheelhouse python3 backend/app.py \
  --data /var/lib/wheelhouse-restored \
  --restore /var/backups/wheelhouse/backup-20261107-090000
# 核对无误后把配置里的 VM_WH_DATA_DIR 指到新目录再启动
```

升级：停服 → 用新版 `backend/` + `frontend/` 覆盖 `/opt/wheelhouse/app` → `start.sh`。数据库迁移在 `init_storage()` 里自动完成；**改了 `backend/` 下的 Python 代码一定要重启进程**，可看 `/health` 的 `app` 字段是否为 `wheelhouse` 判断跑的是不是新代码。

## 8. 独立验证与故障排查

`deploy/vm/bin/verify.sh` 只读检查：进程、Python/SQLite 版本、前后端文件、`/health`、`/api/config`、首页与 ES Module、PEP 503 索引、鉴权策略、数据库与备份目录。设置 `VM_WH_VERIFY_USER/PASSWORD` 后还会检查登录态。它不上传、不删除任何业务数据。

| 现象 | 排查 |
|---|---|
| `Address already in use` / 端口预检失败 | `lsof -nP -i :8080`；不要同时起两个实例 |
| 启动超时、`/health` 非 200 | `tail -n 100 $VM_WH_LOG_DIR/wheelhouse.log`；用 `status.sh` 看 `checks` |
| 目录不可写 | 确认以 `wheelhouse` 用户运行，或 `chown -R wheelhouse:wheelhouse <目录>` |
| 前端 404 / 空白页 | 检查 `VM_WH_FRONTEND_DIR` 下是否有 `index.html`、`js/`、`assets/` |
| 安全启动检查未通过 | 配置 TLS 或把 `VM_WH_HOST` 改回 `127.0.0.1`，不要直接放行明文 |
| 登录不了 | `init-admin.sh` 可重置账号；密码至少 8 位 |
| 看门狗没有自动拉起服务 | `watchdog.sh --status` 看是否熔断或存在暂停标记；`--reset` 复位；确认 timer 已 `enable --now` |
| 计划内停机后服务被自动拉起 | 停机前先 `systemctl stop wheelhouse-watchdog.timer`，或 `touch /run/wheelhouse/watchdog.pause` |
| 反复重启刷屏 | 属预期保护：`StartLimitBurst` 与看门狗熔断已生效，先修根因（磁盘/证书/端口），再 `--reset` |
