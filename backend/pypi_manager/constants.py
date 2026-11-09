# -*- coding: utf-8 -*-
"""全局常量与进程内共享状态（叶子层，不依赖其他业务模块）。"""
from __future__ import annotations

import threading

APP_NAME = "wheelhouse"
VERSION = "1.1"
CHUNK = 1 << 20
MAX_UPLOAD = 512 * 1024 * 1024       # 512MB 单文件默认上限，可由 --max-upload-mb 调整
MAX_FIELD = 64 * 1024                # multipart 普通字段上限
REQUEST_TIMEOUT = 30                 # 单次 socket 读超时（秒），防慢速连接占满线程
MAX_WORKERS = 64                     # 并发请求上限，超出直接 503
MAX_PARALLEL_UPLOADS = 4             # 同时进行的上传数上限
TRASH_MAX_MB = 2048                  # 回收站容量兜底（MB），超期清理之外的第二道保险

ROLES = {"admin", "uploader", "viewer"}

# 新建账号（含批量建号、管理员重置）统一使用的初始密码
DEFAULT_PASSWORD = "12345678"
# 单次批量建号的账号数上限，避免一次请求写入过多记录
BATCH_MAX_USERS = 200
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128

# 登录失败限流：同一 IP + 账号在窗口内失败过多则临时封禁
LOGIN_WINDOW = 300
LOGIN_MAX_FAILS = 8
LOGIN_BLOCK_SECONDS = 300

# 进程内共享状态：写锁、上传并发、停止信号
WRITE_LOCK = threading.Lock()
UPLOAD_SEM = threading.BoundedSemaphore(MAX_PARALLEL_UPLOADS)
STOP = threading.Event()
