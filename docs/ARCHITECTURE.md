# Wheelhouse · 内网 Python 包仓库 — 架构说明

本项目已从单文件实现重构为**前后端物理分离**的两套工程：

- `backend/`：Python 3.10+ 标准库实现的 REST API + PEP 503/691 包索引服务，零第三方依赖。
- `frontend/`：原生 ES Modules 单页应用，无构建步骤、无 npm 依赖，仅通过 REST API 与后端交互。

两者不共享任何代码，可分别部署（例如 Nginx 托管 `frontend/`，反向代理 `/api`、`/simple`、`/files` 到后端）。

---

## 一、根目录结构

```
pypi-manager-project/
├── backend/                     # 后端工程
│   ├── app.py                   # 唯一可执行入口：解析参数、装配、启动 HTTP 服务
│   ├── pypi_manager/            # 后端应用包
│   └── tests/                   # 后端自动化测试（unittest，零依赖）
├── frontend/                    # 前端工程（可独立部署）
│   ├── index.html               # 唯一 HTML 入口（仅挂载点 + 主题预置脚本）
│   ├── assets/css/              # 样式资源
│   └── js/                      # ES Module 业务代码
├── docs/
│   └── ARCHITECTURE.md          # 本文件
└── .github/workflows/ci.yml     # 后端测试 + 前端语法检查
```

---

## 二、后端目录与职责

```
backend/
├── app.py                       # 组合根：CLI、TLS、信号、优雅停机、启动服务器
└── pypi_manager/
    ├── __init__.py              # 包版本号
    ├── constants.py             # 应用常量：版本、限额、角色、锁、信号事件
    ├── config.py                # CFG 配置字典、数据路径布局 paths()、argparse
    ├── errors.py                # 业务异常类型（ServiceError 等）
    ├── log.py                   # 统一日志：级别、request_id/用户上下文、JSON 输出
    ├── utils/                   # 工具函数层（纯函数，无业务状态）
    │   ├── naming.py            # 时间、文件名清洗、PEP 503 名称规范化、wheel 解析
    │   ├── http.py              # HTTP 日期、Content-Disposition、字节格式化
    │   └── multipart.py         # 流式 multipart 解析器（不整包进内存）
    ├── data/                    # 数据访问层
    │   ├── database.py          # SQLite 连接、SCHEMA、迁移、初始化、完整性检查
    │   └── repositories/
    │       ├── file_repo.py     # 包文件表全部 SQL
    │       ├── user_repo.py     # 用户表全部 SQL
    │       └── settings_repo.py # 站点配置键值表全部 SQL
    ├── services/                # 业务服务层
    │   ├── auth_service.py      # 认证、RBAC、scope、登录限流、认证缓存
    │   ├── audit_service.py     # 审计写入、按大小轮转、尾部读取
    │   ├── package_service.py   # 列表统计、上传入库、删除/恢复/彻底删除/清空、CSV 导出
    │   ├── settings_service.py  # 「客户端接入」文案：默认模板、自定义校验、恢复默认
    │   └── storage_service.py   # 启动对账、回收站超期/超容量清理、备份/恢复、健康快照
    ├── middleware/              # 公共中间件层
    │   ├── server.py            # 有界并发线程服务器、超限 503、优雅关闭
    │   ├── base.py              # 请求体读取、响应封装、gzip、安全头、Range
    │   └── security.py          # CSRF 跨站判定、安全响应头集合
    └── routes/                  # 路由层（HTTP 协议适配）
        ├── handler.py           # 组合根：拼装各路由 mixin、统一 dispatch/500 脱敏
        ├── auth.py              # /api/whoami
        ├── packages.py          # /api/packages、/api/export.csv
        ├── uploads.py           # PUT /api/upload/<name>、PUT /api/upload?filename=、POST /|/legacy/（twine）
        ├── files.py             # /files/*、/api/files/*（删除/恢复/备注）
        ├── admin.py             # /api/audit、/api/backup、/api/client-config、项目备注
        ├── users.py             # /api/users、/api/users/batch、/api/password（账号管理）
        ├── simple.py            # /simple/、/simple/<project>/（PEP 503/691）
        └── pages.py             # /、/assets/*、/js/*、/api/config、/health
```

### 依赖方向（禁止反向依赖）

```
routes  →  services  →  data/repositories  →  data/database
   │           │
   └───────────┴────────→  utils / constants / config（叶子层）
middleware  ──→  utils / constants / config
```

- 只有 `data/repositories` 允许出现 SELECT/INSERT/UPDATE/DELETE。
- `services` 可以开启事务（`BEGIN IMMEDIATE`），但必须通过 repository 读写数据。
- `routes` 不直接接触 SQLite，也不包含业务规则。
- `middleware` 只关心 HTTP 协议与服务器能力，不感知业务。
- `routes/handler.py` 与 `app.py` 是仅有的组合根，负责把各层拼装起来。

---

## 三、前端目录与职责

```
frontend/
├── index.html                   # 挂载点 #app、主题防闪烁脚本、CSS/JS 引用
├── assets/css/
│   ├── tokens.css               # 双主题设计变量（浅色/深色）
│   ├── base.css                 # reset、排版、焦点、动效基础
│   ├── layout.css               # 页面骨架、顶栏、列表、登录/上传布局
│   └── components.css           # 按钮、卡片、表格、Toast、弹窗、响应式
└── js/
    ├── main.js                  # 启动：拉取 /api/config、构建壳层、注册 401、启动路由
    ├── router.js                # hash 路由 #/login、#/、#/upload、#/users、#/users/new、#/audit + 登录/角色守卫
    ├── api/                     # API 请求封装层（唯一允许使用 fetch 的目录）
    │   ├── client.js            # Basic 认证、超时、统一错误、401 回调
    │   ├── auth.js              # 登录 / 会话校验 / 退出
    │   ├── packages.js          # 列表、删除、恢复、备注
    │   ├── upload.js            # 单文件上传（进度、取消）
    │   ├── users.js             # 账号列表、建号、改角色、重置密码
    │   ├── audit.js             # 审计日志读取（limit 上限 2000）
    │   ├── settings.js          # 「客户端接入」文案读取 / 保存 / 恢复默认
    │   └── admin.js             # 配置读取、CSV 导出、备份
    ├── store/                   # 状态管理层（极简发布订阅）
    │   ├── index.js             # createStore 实现
    │   ├── auth.js              # 登录态、角色、初始密码标记
    │   ├── packages.js          # 列表、筛选、置顶、上传结果、列表状态恢复
    │   └── ui.js                # 主题、配置、上传队列、视图状态
    ├── pages/                   # 页面视图层
    │   ├── login.js             # 登录页
    │   ├── package-list.js      # 主列表页（筛选、详情、导出、加载更多、客户端接入弹窗）
    │   ├── users.js             # 用户管理页（仅 admin：账号列表、搜索筛选、分页、改角色、重置密码）
    │   ├── user-create.js       # 注册用户页（仅 admin：批量建号，初始密码统一）
    │   ├── audit.js             # 审计日志页（仅 admin：筛选、分页、详情、CSV 导出、自动刷新）
    │   └── upload.js            # 批量上传页（进度、取消、结果汇总）
    ├── components/              # 公共组件层
    │   ├── header.js            # 顶栏、账号信息、修改密码 / 注册用户 / 审计日志入口、主题切换
    │   ├── filter-bar.js        # 多维筛选栏
    │   ├── package-card.js      # 按包名分组的卡片与详情弹层
    │   ├── file-row.js          # 单个上传任务行
    │   ├── toast.js             # 全局提示
    │   └── theme-toggle.js      # 三态主题按钮
    ├── utils/                   # 工具函数层
    │   ├── dom.js               # 转义、事件委托、滚动
    │   ├── format.js            # 字节/日期/版本匹配
    │   └── storage.js           # sessionStorage / localStorage 封装
    └── types/
        └── models.js            # JSDoc 类型定义（User/Package/FileEntry/Filters 等）
```

分层约束：

- 页面与组件**不得直接调用 `fetch`**，必须经由 `js/api/*`。
- 业务数据统一放入 `js/store/*`，页面只订阅状态并渲染。
- `js/types/models.js` 通过 JSDoc 提供类型提示，无需 TypeScript 构建。
- 样式变量集中在 `tokens.css`，组件不得硬编码主题色。

---

## 四、API 交互规范

### 认证

- HTTP Basic Auth（用户名 + 密码，PBKDF2-HMAC-SHA256 校验）。
- 角色：`admin`（全部权限）、`uploader`（上传 + 管理自己上传的文件）、`viewer`（只读）。
- 不再区分项目范围：所有账号都能查看、下载、上传全部包；历史账号的 `scope` 在启动迁移时统一放开为 `*`。
- 术语：**包名**（project，如 `requests`）= 一个可安装的库；**分发包**（distribution）= 一个具体文件
  （`requests-2.31.0-py3-none-any.whl`）；**版本**（version）= 文件名里的版本号。统计卡按此口径展示。
- 登录失败 8 次 / 5 分钟后返回 `429` 并带 `Retry-After`。

### 响应格式

- 成功：`{"ok": true, ...业务字段}`。
- 失败：`{"ok": false, "error": "可直接展示的中文原因"}`，配合标准 HTTP 状态码。
- 列表接口：`{"ok": true, "projects": [...], "stats": {"files": n, "projects": n, "wheel": n, "sdist": n, "size_h": "...", "trash": {"count": n, "size_h": "...", "oldest_left_days": n}}}`。
  `stats`（分发包 / 包名 / 占用）与 `stats.trash` 概览恒为**全库口径**，不受 `deleted` 参数或查询关键字影响，
  因此只拉在用包时顶部看板和回收站提示条依然准确。
- 上传接口：`{"ok": true, "filename": "...", "project": "...", "version": "..."}`。

**列表按需加载**：`deleted=0`（默认）只返回在用行，管理界面首屏、搜索、筛选、删除/上传后的刷新都走这一档；
只有用户勾选「含已删除」或点回收站「查看」时才请求 `deleted=all` 连带历史行，点「返回列表」又回到 `deleted=0`。
名称 / 类型 / 日期 / 版本的筛选仍在浏览器内完成，不产生额外请求。历史行随删除与覆盖上传只增不减，
按需加载避免了每次刷新都传输这部分数据；超过几千包时可进一步做服务端分页或 ETag 缓存。

### 端点

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/` | 公开 | 前端单页入口（静态文件） |
| GET | `/assets/*`、`/js/*` | 公开 | 前端静态资源，ETag 协商缓存 |
| GET | `/api/config` | 公开 | 上传上限、版本、是否允许匿名读 |
| GET | `/health` | 公开 | DB/磁盘深度健康检查，异常 503 |
| GET | `/api/whoami` | 登录 | 返回用户名、角色、是否仍在使用初始密码 |
| GET | `/api/packages?deleted=0\|1\|all` | 登录 | 包列表与统计。`0`（默认）= 只在用包；`1` = 只回收站历史行；`all` = 两者。管理界面默认只取 `0`，进入回收站视图才取 `all`。统计与回收站概览始终是全库口径（见上节） |
| GET | `/api/export.csv` | admin | 导出包清单 CSV |
| GET | `/api/audit?limit=N` | admin | 审计日志（含轮转文件，尾部读取，按时间倒序；`N` 上限 2000。管理界面「审计日志」页一次取 1000 条，筛选/分页在浏览器内完成） |
| POST | `/api/backup` | admin | 立即创建备份 |
| GET | `/api/client-config` | 登录 | 「客户端接入」三段文案：`sections` 生效内容、`defaults` 内置模板、`custom`/`updated_by` 标记。模板里的 `{base}` / `{host}` 由前端替换成当前访问地址，换域名或加反代前缀无需改文案 |
| PUT | `/api/client-config` | admin | 只更新提交的段落（`{"sections":{"pip":"…"}}`），单段上限 4000 字符；传空串等于该段恢复默认，与默认完全相同则不落库（便于将来升级默认模板）。审计记 `edit_client_config` |
| DELETE | `/api/client-config` | admin | 删除全部自定义，恢复内置默认 |
| GET | `/api/users` | admin | 账号列表（name/role/created/pwd_default，不含口令） |
| POST | `/api/users` | admin | 建号 / 改角色 / 重置密码（`reset_password:true` 恢复初始密码；同名且不带 `password` 只改角色） |
| POST | `/api/users/batch` | admin | 批量建号：`text` 换行/逗号/空格分隔，初始密码统一，已存在的账号跳过 |
| POST | `/api/password` | 登录 | 用户自助改密（校验原密码，改完立即清认证缓存） |
| DELETE | `/api/users/<name>` | admin | 删除账号（不能删自己、不能删最后一个 admin） |
| PUT | `/api/upload/<filename>` | uploader | 原始字节流上传；URL 以 `/` 结尾时 curl 自动补文件名 |
| PUT | `/api/upload?filename=X` | uploader | 同上（旧写法，兼容保留） |
| POST | `/` | uploader | twine / uv publish multipart 上传（`:action=file_upload`） |
| POST | `/legacy`、`/legacy/` | uploader | 同上，兼容 poetry 习惯把上传地址写成 PyPI 传统端点 `/legacy/` |
| DELETE | `/api/files/<id>` | 上传者/admin | 移入回收站（30 天或超容量后自动清理） |
| POST | `/api/files/<id>` | 上传者/admin | 从回收站恢复（同名文件在用中时 409） |
| DELETE | `/api/files/<id>/purge` | admin | 从回收站彻底删除，不可恢复 |
| POST | `/api/trash/empty` | admin | 清空回收站 |
| PATCH | `/api/files/<id>` | 上传者/admin | 修改备注 |
| PATCH | `/api/projects/<name>` | admin | 修改项目备注 |
| GET | `/simple/` | 公开/可配 | PEP 503 索引；`Accept: application/vnd.pypi.simple.v1+json` 返回 PEP 691 |
| GET | `/simple/<project>/` | 公开/可配 | 项目文件列表，带 sha256 片段 |
| GET | `/files/<filename>` | 公开/可配 | 下载包文件，支持 ETag/304/Range/206 |

### 通用约定

- 写操作校验 `Origin` / `Sec-Fetch-Site`，拒绝跨站请求（CSRF 防护）。
- 所有响应带安全头：`X-Content-Type-Options`、`X-Frame-Options`、CSP、Referrer-Policy 等。
- JSON/HTML/CSV 超过 1KB 且客户端支持时自动 gzip。
- 上传默认上限 512MB、并发请求上限 64、磁盘水位 512MB，均可通过 CLI 调整。
- 删除一律先进回收站（`<data>/.trash/`，文件名加时间戳前缀），保留 `--trash-days`（默认 30 天），
  另有 `--trash-max-mb`（默认 2048）容量兜底，超出时从最旧开始清理并同步删除数据库记录。
  同名文件被覆盖时旧版本同样进回收站，状态显示「已覆盖」，仍可恢复或由管理员彻底删除。

---

## 五、运行与测试

```bash
# 初始化管理员（--data 必须是当前用户可写的目录）
# Linux 服务器常用 /data/pypi；macOS 根目录只读，请使用 ./data/pypi 或 ~/pypi-data
# 省略 ":密码" 时使用初始密码 12345678
python3 backend/app.py --data ./data/pypi --init-user admin:strong-password --role admin

# 启动（默认读取项目根/frontend 作为静态资源目录）
python3 backend/app.py --data ./data/pypi --host 127.0.0.1 --port 8080 \
  --backup-dir ./data/pypi-backup --backup-interval-hours 24

# 读权限开关（互斥，默认免登录；两者同时传会直接报错退出，不会静默取其一）
#   --allow-anonymous-read                  /simple/ 与 /files/ 免登录，pip 开箱即用（默认）
#   --require-auth-for-read                 读操作也要 Basic Auth，pip 需配 .netrc
#   生效范围：CFG["open_read"] → simple.py / files.py 的入口判定；/api/* 始终要登录

# 日志相关参数（均有默认值，一般无需显式指定）
#   --log-level INFO|DEBUG|WARNING|ERROR   运行日志级别
#   --log-json                             单行 JSON，便于 Loki/ES 采集
#   --slow-ms 1000                         超过该耗时的请求记 WARNING 并标 [slow]

# 备份与回滚（回滚须停服）
python3 backend/app.py --data ./data/pypi --backup-dir ./data/pypi-backup --backup-now
python3 backend/app.py --backup-dir ./data/pypi-backup --list-backups
python3 backend/app.py --data ./data/pypi \
  --restore ./data/pypi-backup/backup-20261109-090000 --force
#   --list-backups  列出每份备份的时间、包文件数、大小、完整性（只读，服务运行中也能跑）
#   --restore       先核对备份库里"在用"的记录与 packages/ 实际文件，缺包默认拒绝
#   --force         目标已有数据时先移入 <data>/.rollback-<时间戳>；恢复中途失败自动回滚
#   两套部署的封装：deploy/{vm,docker}/bin/restore.sh --list | <备份名>（含停服/启动）

# 前端可独立部署：Nginx 托管 frontend/，将 /api、/simple、/files 反代到后端
# 若前端目录不在默认位置，使用 --frontend-dir 指定

# 后端测试
cd backend && python3 -m unittest discover -s tests -v

# 前端语法检查
find frontend/js -name '*.js' -print0 | xargs -0 -n1 node --check
```

> **macOS 注意**：系统根卷（`/`）是只读的，无法创建 `/data`，直接使用
> `--data /data/pypi` 会报 `OSError: [Errno 30] Read-only file system: '/data'`。
> 请把 `--data` 指向用户目录或项目内路径（如 `~/pypi-data`、`./data/pypi`）；
> 仅在 Linux 服务器上可以使用 `/data/pypi`。

### 部署方案

仓库提供两套**相互独立、可单独选用**的部署内容，见 [`deploy/README.md`](../deploy/README.md)：

- [`deploy/vm/`](../deploy/vm/README.md)：内网虚拟机直接部署（Python 进程 + 可选 systemd/Nginx，不依赖 Docker）。
- [`deploy/docker/`](../deploy/docker/README.md)：Docker 部署（Dockerfile + Compose + 健康检查，不依赖虚拟机方案）。

两套方案各有独立配置文件、启动/停止脚本、环境变量前缀、默认端口与数据存储，均只把各自配置翻译成 `app.py` 的命令行参数，不改变任何业务功能。

---

## 六、日志与审计

三类日志职责不同，且都带同一个 `request_id`，可串起一次请求的全过程：

| 类型 | 实现 | 输出 | 保留策略 |
|---|---|---|---|
| 访问日志 | `middleware/base.py` 的 `log_request`（每请求一行：IP、用户、请求行、状态、字节、耗时） | stderr | 由部署侧负责（journald 容量 / logrotate / docker json-file 限容） |
| 运行日志 | `pypi_manager/log.py` 统一 logger，`app.py`、服务层、异常栈均走它 | stderr | 同上 |
| 审计日志 | `services/audit_service.py`，JSONL 逐行追加 | `<data>/audit.jsonl` | 自带按大小轮转（`--audit-max-mb` × `--audit-keep`） |

要点：

- `log.py` 用 contextvar 保存 `request_id` 与当前用户：访问日志、`_log.exception` 的栈、
  500 响应体里的「错误编号」三者一致，用户报一个编号即可定位。
- 级别由 `--log-level` 控制；`--log-json` 输出单行 JSON（字段含 `status`/`bytes`/`duration_ms`/`slow`），
  供 Loki/ES 直接采集；`--slow-ms`（默认 1000）让慢请求升为 WARNING 并带 `[slow]` 标记。
- 4xx 记 WARNING、5xx 记 ERROR，`http.server` 的协议层错误（坏请求行、超时）与并发打满的 503
  也都走同一 logger，不再有多条互不相通的输出通道。
- `server_error` 会写进审计（含 `error_id`），事后可按错误编号反查是谁、在什么路径触发的。
- 审计日志不落 stderr，避免与运行日志混在一起被采集器当作普通日志丢弃。
- 管理界面「审计日志」页（`#/audit`，仅 admin）把 `/api/audit` 变成可读列表：动作中文标签 +
  风险着色、按动作/关键词/「仅风险操作」筛选、分页、单条详情（字段视图 + 原始 JSON）、
  导出 CSV、15 秒自动刷新（页面在后台或详情弹窗打开时自动暂停）。筛选完全在前端完成，
  因此服务端只按 `limit` 取尾部，不额外增加接口参数。

---

## 七、验证记录

- 后端 70 个 unittest 用例全部通过（认证/RBAC/XSS/限流/上传上限/备份恢复完整性/归档回滚/对账/优雅停机/回收站/列表按需加载/日志格式与请求上下文/500 错误编号与审计联动/读权限开关生效与互斥/审计接口权限与倒序等）。
- 备份回滚端到端验证：备份 2 包 → 新增 1 包 → `restore.sh` 回滚后精确回到 2 包、下载 200、`verify.sh` 全 PASS；
  缺包备份被默认拒绝（目标目录零改动），加 `--force` 才恢复并告警；恢复中途失败自动把归档放回原位。
- 浏览器端到端验证通过：登录、项目列表、筛选、主题切换、详情、备注保存、删除/恢复/彻底删除/清空回收站、CSV 导出、批量上传、上传后返回置顶高亮；并确认首屏只请求 `deleted=0`，进入回收站视图才请求 `deleted=all`。
- 审计页端到端验证通过（72 条真实审计记录）：入口可见性、`#/audit` 路由、20/50 条分页、动作筛选、仅风险操作、关键词搜索、详情弹窗（Esc 关闭）、CSV 导出（BOM + 全部记录）、刷新取到新记录、1440px 与 390px 无横向溢出、暗色主题、只读账号看不到入口且直接访问被弹回主界面、控制台零报错。
- `pip install --index-url http://127.0.0.1:8100/simple/ pmsmoke==1.0` 安装成功。
- 前端全部 ES Module 文件 `node --check` 通过。
