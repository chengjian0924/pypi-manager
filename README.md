# Wheelhouse · 内网 Python 包仓库

[![CI](https://github.com/chengjian0924/pypi-manager/actions/workflows/ci.yml/badge.svg)](https://github.com/chengjian0924/pypi-manager/actions/workflows/ci.yml)

一个可离线自托管的内网 PyPI 仓库：标准 `pip` / `uv` / `poetry` / `twine` 直接可用，
自带 Web 管理界面（包浏览、上传、回收站、账号、审计、备份回滚），**零第三方依赖**。

- **后端**：Python 3.10+ 标准库，无 pip 依赖，单进程即可运行
- **前端**：原生 ES Modules，无构建步骤、无 npm 依赖

---

## 功能

| 能力 | 说明 |
|---|---|
| 标准包索引 | PEP 503 简单索引 + PEP 691 JSON，响应带 `sha256` 片段，`pip install` 开箱可用 |
| 多客户端上传 | `twine`、`uv publish`、`poetry`（含 `/legacy/` 端点）、`curl` 原始字节流、批量脚本 |
| 包管理 | 按包名分组、版本列表、备注、CSV 导出；删除进回收站（默认 30 天 / 容量兜底），支持恢复与彻底删除 |
| 覆盖可回退 | 同名文件覆盖时旧版本自动进回收站，标为「已覆盖」，可恢复 |
| 账号与权限 | HTTP Basic 认证 + 三级角色：`admin` / `uploader` / `viewer`；登录失败限流（8 次 / 5 分钟） |
| 审计日志 | JSONL 落盘、按大小轮转；管理界面可筛选、看详情、导出 CSV，`request_id` 可串起一次请求全过程 |
| 备份与回滚 | 定时/手动备份、`--list-backups` 校验完整性、`--restore` 一键回滚（失败自动放回） |
| 日志 | 访问/运行/审计三类日志共用 `request_id`；`--log-json` 供 Loki/ES 采集；`--slow-ms` 标记慢请求 |
| 可定制接入文案 | 界面里的「客户端接入」三段命令可由管理员编辑，存服务端，`{base}`/`{host}` 自动替换 |

---

## 快速开始

```bash
# 1) 初始化数据目录与管理员账号（--data 必须是当前用户可写目录）
python3 backend/app.py --data ~/pypi-data --init-user admin:换成你的密码 --role admin

# 2) 启动（默认读取项目根的 frontend/ 作为静态资源；127.0.0.1 仅供本机访问）
python3 backend/app.py --data ~/pypi-data --host 127.0.0.1 --port 8080 \
  --backup-dir ~/pypi-backup --backup-interval-hours 24
```

打开 <http://127.0.0.1:8080/> 登录即可上传、浏览、管理。

> **macOS 注意**：系统根卷只读，`--data /data/pypi` 会报 `Read-only file system`，
> 请改用 `~/pypi-data` 或项目内路径；`/data/pypi` 仅适用于 Linux 服务器。
>
> 默认监听 `0.0.0.0` 时，`app.py` 会因「非回环 + 无 TLS」拒绝启动；
> 要么像上面只绑 `127.0.0.1`，要么配置 `--tls-cert/--tls-key`，
> 要么在确认前置网关已终止 TLS 后显式加 `--insecure-http`。

---

## 客户端接入

```bash
# pip：写入全局配置（内网源通常还要 --trusted-host）
pip config set global.index-url http://<服务器IP>:8080/simple/
pip config set global.trusted-host <服务器IP>

# uv
export UV_INDEX_URL=http://<服务器IP>:8080/simple/

# poetry（pyproject.toml）
[[tool.poetry.source]]
name = "internal"
url = "http://<服务器IP>:8080/simple/"
priority = "primary"
```

上传：

```bash
# twine（也支持把地址写成 /legacy/，兼容 poetry 习惯）
twine upload --repository-url http://<服务器IP>:8080/ -u <用户> -p <密码> dist/*

# uv publish
uv publish --publish-url http://<服务器IP>:8080/ --username <用户> --password <密码> dist/*

# curl（URL 以 / 结尾时自动取本地文件名）
curl -u <用户>:<密码> -T dist/pkg-1.0.0-py3-none-any.whl http://<服务器IP>:8080/api/upload/
```

说明：

- 默认 `/simple/` 与 `/files/` **免登录**，`pip install` 不需要凭证；
  加 `--require-auth-for-read` 后读操作也要 Basic Auth，客户端需配 `.netrc`。
- 内网源里没有外部依赖，装有 `Requires-Dist` 的包安装时会因缺上游包失败——
  用 `--no-deps`，或 `--extra-index-url` 指向可访问的上游/公网镜像。
- 上传上限默认 512MB（`--max-upload-mb`），并发请求上限 64（`--max-workers`）。

---

## 部署

仓库提供两套**相互独立**的部署内容，任选其一，二者不共享脚本与配置：

| 方案 | 适用 | 入口 |
|---|---|---|
| `deploy/vm/` | 内网虚拟机直接跑 Python 进程，可选 systemd + Nginx | [deploy/vm/README.md](deploy/vm/README.md) |
| `deploy/docker/` | Dockerfile + Compose + 健康检查 + 看门狗 | [deploy/docker/README.md](deploy/docker/README.md) |

两套方案的启动/停止/校验/回滚脚本只把各自的环境变量翻译成 `app.py` 的命令行参数，不改变业务功能。
总览与差异对照见 [deploy/README.md](deploy/README.md)。

**离线镜像搬运**（内网服务器不能出网）：

```bash
# 构建机（架构须与目标机一致：aarch64/x86_64）
deploy/docker/bin/export-image.sh            # 构建 → 校验架构 → docker save + SHA256SUMS + LOAD.md

# 目标机
sha256sum -c SHA256SUMS && docker load -i wheelhouse-intranet_1.1-arm64.tar
docker image inspect -f '{{.Os}}/{{.Architecture}}' wheelhouse-intranet:1.1   # 必须是 linux/arm64
```

镜像导入后把 `deploy/docker/.env` 的 `DK_BUILD_ON_START` 置 `0`，启动时不再尝试联网构建。

---

## 目录结构

```
backend/
├── app.py                 # 唯一入口：CLI、TLS、信号、优雅停机
└── pypi_manager/
    ├── routes/            # HTTP 适配层（simple / files / uploads / admin / users / pages）
    ├── services/          # 业务层（认证、上传入库、回收站、备份恢复、审计、接入文案）
    ├── data/              # SQLite + repositories（唯一出现 SQL 的目录）
    ├── middleware/        # 并发服务器、请求体读取、响应封装、安全头、CSRF
    ├── utils/             # 纯函数：命名规范化、HTTP 头、流式 multipart
    └── log.py             # 统一 logger 与 request_id 上下文
frontend/
├── index.html
├── assets/css/            # tokens / base / layout / components（双主题）
└── js/                    # api / store / pages / components / utils，原生 ES Module
docs/ARCHITECTURE.md       # 分层约束、端点表、日志设计、验证记录
deploy/{vm,docker}/        # 两套互不依赖的部署方案
```

依赖方向单向：`routes → services → data/repositories → data/database`，`middleware` 只处理 HTTP，不感知业务。

---

## 常用参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--data` | `/data/pypi` | 数据目录（SQLite、包文件、回收站、审计） |
| `--host` / `--port` | `0.0.0.0` / `8080` | 监听地址与端口 |
| `--init-user USER[:PASS]` | — | 创建/更新账号，省略密码时用内置初始密码（建议显式指定） |
| `--allow-anonymous-read` / `--require-auth-for-read` | 前者 | 读操作是否免登录，二者互斥，同时传会报错退出 |
| `--max-upload-mb` | 512 | 单文件上传上限 |
| `--max-workers` | 64 | 并发请求上限，超出直接 503 |
| `--min-free-mb` | 512 | 磁盘水位，低于该值时 `/health` 返回 503 |
| `--backup-dir` / `--backup-interval-hours` / `--backup-keep` | 空 / 0 / 7 | 备份目录、自动备份周期、保留份数 |
| `--list-backups` / `--restore DIR [--force]` | — | 列出备份（含完整性）与回滚（目标已有数据移入 `.rollback-<时间戳>`） |
| `--trash-days` / `--trash-max-mb` | 30 / 2048 | 回收站保留天数与容量兜底 |
| `--log-level` / `--log-json` / `--slow-ms` | INFO / 关 / 1000 | 日志级别、单行 JSON 输出、慢请求阈值 |
| `--frontend-dir` | 项目根 `frontend/` | 静态资源目录（前后端分开部署时用） |

---

## 测试

```bash
# 后端：unittest，零依赖
cd backend && python3 -m unittest discover -s tests -v

# 前端：ES Module 语法检查
find frontend/js -name '*.js' -print0 | xargs -0 -n1 node --check
```

CI（`.github/workflows/ci.yml`）在 Python 3.10 / 3.13 上跑后端测试与 `compileall`，并在 Node 20 上做前端语法检查。

---

## 安全说明

- 认证是 HTTP Basic。**明文 HTTP 下口令可被抓包解出**，公网/不可信网段务必走 TLS
  （`--tls-cert/--tls-key` 让容器或进程自己出 HTTPS，或在前面挂 Nginx 终止 TLS）。
- 写操作校验 `Origin` / `Sec-Fetch-Site`，所有响应带 CSP、`X-Frame-Options` 等安全头。
- 审计日志记录操作人、IP、动作与结果，`server_error` 带错误编号，便于事后追溯。
- 备份建议放在**不同磁盘**，并定期演练恢复——同盘备份在磁盘故障时一并丢失。

更多设计细节见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。
