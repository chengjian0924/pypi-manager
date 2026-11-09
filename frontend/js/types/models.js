/**
 * @typedef {'admin'|'uploader'|'viewer'|string} Role
 */

/**
 * @typedef {Object} User
 * @property {string} user 用户名
 * @property {Role} role 角色
 */

/**
 * @typedef {Object} Credentials
 * @property {string} value Base64 编码的 Basic 认证凭据
 */

/**
 * @typedef {Object} FileEntry
 * @property {number} id 文件 ID
 * @property {string} filename 文件名
 * @property {string} version 版本号
 * @property {'wheel'|'sdist'|string} kind 包类型
 * @property {string} [py_tag] Python 标签
 * @property {string} [abi_tag] ABI 标签
 * @property {string} [plat_tag] 平台标签
 * @property {number} size 字节数
 * @property {string} size_h 可读大小
 * @property {string} sha256 摘要
 * @property {string} [summary] 摘要说明
 * @property {string} [requires_python] Python 版本要求
 * @property {number} [dep_count] 依赖数量
 * @property {string} [note] 备注
 * @property {string} uploaded_at 上传时间
 * @property {string} uploaded_by 上传人
 * @property {boolean} deleted 是否在回收站
 */

/**
 * @typedef {Object} Package
 * @property {string} name 规范化项目名
 * @property {string} [display] 展示名
 * @property {string} [description] 项目描述
 * @property {FileEntry[]} files 文件列表
 */

/**
 * @typedef {Object} Stats
 * @property {number} files 文件数量
 * @property {number} projects 项目数量
 * @property {number} [size] 总字节数
 * @property {string} size_h 可读总大小
 */

/**
 * @typedef {Object} Filters
 * @property {string} name 包名 / 文件名关键字
 * @property {''|'wheel'|'sdist'} type 包类型
 * @property {string} from 起始日期（YYYY-MM-DD）
 * @property {string} to 结束日期（YYYY-MM-DD）
 * @property {string} version 版本表达式
 * @property {boolean} showDel 是否包含已删除
 */

/**
 * @typedef {Object} Config
 * @property {number} max_upload 单文件上限（字节）
 * @property {string} version 服务版本
 * @property {boolean} open_read 读操作是否免登录
 */

/**
 * @typedef {Object} WhoamiResult
 * @property {boolean} [ok]
 * @property {string} user
 * @property {Role} role
 */

/**
 * @typedef {Object} UploadResult
 * @property {boolean} ok
 * @property {boolean} [skipped]
 * @property {string} [filename]
 * @property {string} [project]
 * @property {string} [version]
 * @property {string} [sha256]
 * @property {boolean} [replaced]
 */

/**
 * @typedef {Object} ApiResult
 * @property {boolean} [ok]
 * @property {string} [error]
 * @property {string} [message]
 */

/**
 * @typedef {Object} UploadFailure
 * @property {string} title
 * @property {string} detail
 * @property {string} hint
 */

/**
 * @typedef {Object} UploadItem
 * @property {number} id 队列内自增 ID
 * @property {File} file 待上传文件
 * @property {'pending'|'running'|'ok'|'err'|'canceled'} status 状态
 * @property {string} err 错误说明
 * @property {boolean} clientInvalid 是否前端校验未通过
 * @property {number} pct 上传进度
 * @property {string} message 成功提示
 * @property {UploadResult} [result] 服务端返回
 * @property {UploadFailure} [failure] 失败详情
 * @property {boolean} [canceled] 是否请求取消
 */

/**
 * 审计记录（`/api/audit` 返回的条目）。除固定字段外，不同类型的动作会带不同扩展字段，
 * 例如 upload 带 project/version/sha256/size，server_error 带 error_id/method/exception。
 * @typedef {Object} AuditEntry
 * @property {string} ts 记录时间（ISO 8601，带时区）
 * @property {string} user 操作人；anonymous 表示未登录，system 表示后台任务
 * @property {string} action 动作标识，如 login / upload / delete / purge
 * @property {string} target 操作对象（文件名、账号名或请求路径）
 * @property {string} [ip] 来源 IP
 */

export {};
