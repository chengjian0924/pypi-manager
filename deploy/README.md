# Wheelhouse 部署目录

本目录包含 **两套相互独立、可单独选用** 的部署方案。请按实际环境二选一，不需要同时部署。

| | `deploy/vm/` | `deploy/docker/` |
|---|---|---|
| 适用场景 | 内网虚拟机 / 物理机，直接跑 Python 进程 | 已有 Docker 主机或容器平台 |
| 运行依赖 | Python ≥ 3.10、SQLite ≥ 3.24（仅标准库） | Docker ≥ 24、Compose v2；镜像基于 `python:3.12-slim` |
| 唯一配置文件 | `config/wheelhouse.env`（模板 `.example`） | `.env`（模板 `.env.example`） |
| 变量前缀 | `VM_WH_*` | 宿主/Compose `DK_*`，容器内 `WH_*` |
| 默认端口 | `8080` | 宿主 `8081` → 容器 `8080` |
| 默认数据存储 | `/var/lib/wheelhouse`、`/var/backups/wheelhouse` | 命名卷 `wheelhouse-data`、`wheelhouse-backup` |
| 启动 / 停止 | `bin/start.sh` / `bin/stop.sh` | `bin/start.sh` / `bin/stop.sh` |
| 初始化账号 | `bin/init-admin.sh` | `bin/bootstrap.sh` |
| 独立验证 | `bin/verify.sh` | `bin/verify.sh` |
| 进程托管 | nohup + PID 文件；生产用 `systemd/` 主单元 + 看门狗 timer | `restart: unless-stopped` + 内置 HEALTHCHECK + 宿主侧看门狗 timer |

## 独立性约定

- **不共享启动逻辑**：两套方案各自实现「配置 → `backend/app.py` 命令行参数」的映射（VM 在 `vm/bin/start.sh`，Docker 在 `docker/entrypoint.sh`），各自有独立的 `bin/_env.sh`。
- **不共享部署产物**：配置文件、脚本、systemd 单元、Compose 定义、Dockerfile、健康检查脚本、日志与 PID/卷全部各归各的；默认端口与数据目录也刻意错开，两套可以同时验证而不互相干扰。
- **不互相依赖**：`deploy/vm/` 中不出现 docker/compose 命令；`deploy/docker/` 中不出现 `deploy/vm/` 路径，Docker 构建时还会通过 `Dockerfile.dockerignore` 把 `deploy/vm/` 排除在构建上下文之外。
- **业务代码零改动**：两套方案都只是把各自的环境变量翻译成 `backend/app.py` 已有的命令行参数，没有修改 `backend/`、`frontend/` 里的任何业务功能。

> 两套方案共同引用的只有仓库自身的 `backend/` 与 `frontend/` 源码（即被部署的应用本体），这不属于任何一方的「部署内容」。

## 快速上手（各选一条）

```bash
# 方案 A：虚拟机直接部署
cp deploy/vm/config/wheelhouse.env.example deploy/vm/config/wheelhouse.env
vi  deploy/vm/config/wheelhouse.env
deploy/vm/bin/init-admin.sh      # 首次建号，交互式输入密码
deploy/vm/bin/start.sh
deploy/vm/bin/verify.sh

# 方案 B：Docker 部署
cp deploy/docker/.env.example deploy/docker/.env
vi  deploy/docker/.env
deploy/docker/bin/start.sh      # 首次会构建镜像
# 若需先建号：deploy/docker/bin/stop.sh && deploy/docker/bin/bootstrap.sh && deploy/docker/bin/start.sh
deploy/docker/bin/verify.sh
```

各方案的完整说明见：

- [`deploy/vm/README.md`](vm/README.md)
- [`deploy/docker/README.md`](docker/README.md)

## 分别维护与分别验证

| 动作 | 方案 A | 方案 B |
|---|---|---|
| 改配置 | 编辑 `vm/config/wheelhouse.env` | 编辑 `docker/.env` |
| 重启生效 | `vm/bin/stop.sh && vm/bin/start.sh` | `docker/bin/start.sh`（配置变更后 Compose 会重建容器） |
| 看状态 | `vm/bin/status.sh` | `docker/bin/status.sh` |
| 看日志 | `tail -f $VM_WH_LOG_DIR/wheelhouse.log` | `docker/bin/logs.sh` |
| 停机 | `vm/bin/stop.sh`（自动暂停看门狗） | `docker/bin/stop.sh`（保留卷 + 自动暂停看门狗） |
| 独立验证 | `vm/bin/verify.sh` | `docker/bin/verify.sh` |
| 备份盘点 | `vm/bin/restore.sh --list` | `docker/bin/restore.sh --list` |
| 回滚备份 | `vm/bin/restore.sh <备份名>`（自动归档 + 启停） | `docker/bin/restore.sh <备份名>`（自动归档 + 启停） |
| 卸载 | 停服后按需删除 `/var/lib/wheelhouse` 等目录 | `docker compose ... down -v`（会删除全部包，慎用） |

日志轮转两套方案各自负责，互不依赖：

- 方案 A 用 systemd（journald 自带容量上限）时无需额外配置；用 `start.sh` 后台模式时须安装
  [`vm/config/wheelhouse.logrotate.example`](vm/config/wheelhouse.logrotate.example) 到 `/etc/logrotate.d/wheelhouse`。
- 方案 B 的限容写在 `compose.yaml` 的 `logging` 段（`DK_LOG_MAX_SIZE` / `DK_LOG_MAX_FILE`），不需要宿主机 logrotate。
- 两套都通过 `--log-level` / `--log-json` / `--slow-ms` 控制级别、结构化输出与慢请求阈值。

## 24×7 无人值守运行

「服务不停」要按故障类型分层解决，两套方案**各自独立实现、各自独立安装**：

| 故障场景 | 方案 A（`deploy/vm/`） | 方案 B（`deploy/docker/`） |
|---|---|---|
| 进程崩溃 / 被 OOM 杀掉 | `wheelhouse.service` 的 `Restart=always`，5s 内拉起 | `compose.yaml` 的 `restart: unless-stopped` |
| 宿主机重启 | `systemctl enable --now wheelhouse` | `systemctl enable docker` + 上面的重启策略 |
| 进程还在但 `/health` 不响应（假死） | `wheelhouse-watchdog.timer` → `systemctl restart` | `wheelhouse-docker-watchdog.timer` → `compose restart` |
| 进程/容器被误删 | 看门狗 → `start.sh` 或 `systemctl restart` | 看门狗 → `compose start` / `compose up -d` |
| 反复重启也修不好 | `StartLimitBurst` + 看门狗熔断，停止重启并告警 | 看门狗熔断，停止重启并告警 |
| 计划内停机不被拉起 | 停 timer 或放 `watchdog.pause`；`stop.sh` 会自动放标记 | `stop.sh` 自动放 `watchdog.pause`，`start.sh` 自动清除 |

每套方案都有自己的 `bin/watchdog.sh`、systemd 单元与 `*_WATCHDOG_*` 配置项，**互不引用、互不影响**：

- 看门狗默认每 30s 探测一次，连续 3 次失败才动作；两次恢复间隔 ≥120s；连续 3 次恢复仍不健康则熔断（避免磁盘满、证书过期这类「重启也修不好」的故障造成重启风暴）。健康恢复后计数自动清零，也可 `--reset` 人工复位。
- 支持 `--once`（timer/cron）、`--loop`（无 systemd 时常驻）、`--status`、`--reset` 四种模式；告警通过可选的 `*_ALERT_CMD` 钩子发出，消息在 `WATCHDOG_MESSAGE` 环境变量里。
- 详细安装步骤、参数表与计划内停机流程见 [`vm/README.md` 第 5 节](vm/README.md) 与 [`docker/README.md` 第 8 节](docker/README.md)。

> **单机自愈的边界**：以上解决的是「进程/容器级」故障，恢复时间通常在秒级到 2 分钟内。整机宕机、磁盘损坏、机房断电无法靠自愈解决，只能靠 `--backup-dir` 的备份恢复（RPO = 备份间隔，RTO = 恢复耗时）。本应用是单进程 + 单 SQLite + 本地包文件，**不支持多实例同时写**；若需要真正的零停机，应做主备 + 共享存储 + 前置反代/VIP，并保证同一时刻只有一个写实例。

### 自愈能力分别验证

两套各自验证，互不依赖：

```bash
# 方案 A（systemd 环境）
sudo systemctl restart wheelhouse && sudo systemctl status wheelhouse
journalctl -u wheelhouse-watchdog.service -n 20
deploy/vm/bin/watchdog.sh --status

# 方案 B
docker kill wheelhouse && sleep 10 && docker ps --filter name=wheelhouse
journalctl -u wheelhouse-docker-watchdog.service -n 20
deploy/docker/bin/watchdog.sh --status
```

上线前务必处理的共性事项（各自在自己的配置文件里完成）：

1. **TLS**：Basic Auth 是 base64 明文。默认两套都监听回环/由外层终止 TLS；务必配置证书或前置网关，不要把明文端口直接暴露到内网。
2. **初始密码**：`--init-user` 省略密码时会使用 `12345678`，两套方案的初始化脚本都强制交互式输入至少 8 位密码。
3. **备份**：`VM_WH_BACKUP_*` / `DK_BACKUP_*` 默认已开启每日备份，请把备份卷放到与数据卷不同的磁盘；每次改动前用 `bin/restore.sh --list` 确认最近一份是「完整」，并定期做一次真实回滚演练（现有数据会归档到 `.rollback-*`，可随时取回）。
4. **读权限**：两套部署默认 `REQUIRE_AUTH_FOR_READ=1`（`/simple/` 与 `/files/` 也需登录），pip 客户端需配 `.netrc` / keyring。注意这与 `app.py` 的默认值相反——程序单独运行时默认免登录，部署脚本为安全起见显式传 `--require-auth-for-read`；内网想开箱即用地 `pip install`，把该变量改成 `0`（脚本会传 `--allow-anonymous-read`）。两个开关互斥，同时传会启动失败。
