# -*- coding: utf-8 -*-
"""统一日志入口：级别控制、请求上下文（request_id / user）、可选 JSON 输出。

设计取向：
- 单一 logger 树（``wheelhouse`` 及其子 logger），禁止业务代码直接 ``print``。
- ``request_id`` 与当前登录用户放在 contextvars 里，由访问日志与异常日志共享，
  这样一个请求的 200/500 行、traceback 与审计记录可以用同一个 id 串起来。
- 文本模式给人看（``docker logs`` / ``journalctl``），JSON 模式给采集器看（Loki、ES）。
- 日志永远写 stderr：stdout 留给 CLI 一次性命令（--init-user、--backup-now）的正常输出。
"""
from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime

LOGGER_ROOT = "wheelhouse"

# 被当作库导入时（unittest、脚本）不往 stderr 乱写；configure() 会替换掉这个 handler
logging.getLogger(LOGGER_ROOT).addHandler(logging.NullHandler())

# 请求级上下文：默认值让 CLI / 后台线程直接使用 logger 也不会报错
_REQUEST_ID: ContextVar[str] = ContextVar("request_id", default="-")
_USER: ContextVar[str] = ContextVar("user", default="-")

LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

# LogRecord 自带字段，JSON 输出时不应重复展开
_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename", "module",
    "exc_info", "exc_text", "stack_info", "lineno", "funcName", "created", "msecs",
    "relativeCreated", "thread", "threadName", "processName", "process", "taskName",
    "message", "asctime",
}


class TextFormatter(logging.Formatter):
    """文本：2026-11-09 11:09:17 INFO  [a1b2c3d4] 127.0.0.1 admin "GET / HTTP/1.1" 200 1024 3.2ms"""

    def format(self, record):
        record.request_id = _REQUEST_ID.get()
        return super().format(record)


class JsonFormatter(logging.Formatter):
    """JSON 一行一条，字段稳定，便于 Loki/ES 直接按 key 查询"""

    def format(self, record):
        payload = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": _REQUEST_ID.get(),
            "user": _USER.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload.setdefault(key, value)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure(level: str = "INFO", json_mode: bool = False, stream=None) -> logging.Logger:
    """初始化根 logger；重复调用安全（测试与多次启动都用它）。"""
    logger = logging.getLogger(LOGGER_ROOT)
    logger.setLevel(LEVELS.get(str(level or "INFO").upper(), logging.INFO))
    handler = logging.StreamHandler(stream or sys.stderr)
    if json_mode:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(TextFormatter(
            "%(asctime)s %(levelname)-5s [%(request_id)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"))
    logger.handlers[:] = [handler]
    # 不向 root 传播，避免调用方（如 unittest / gunicorn）再挂 handler 导致重复输出
    logger.propagate = False
    return logger


def get_logger(name: str = "") -> logging.Logger:
    """子 logger：get_logger("access") -> wheelhouse.access"""
    return logging.getLogger(f"{LOGGER_ROOT}.{name}" if name else LOGGER_ROOT)


def set_request(request_id: str | None = None) -> str:
    """进入一个请求：生成/写入 request_id，返回实际使用的值。"""
    rid = request_id or uuid.uuid4().hex[:8]
    _REQUEST_ID.set(rid)
    _USER.set("-")
    return rid


def clear_request():
    _REQUEST_ID.set("-")
    _USER.set("-")


def set_user(name: str | None):
    """认证成功后回填用户名，供访问日志与异常日志使用。"""
    _USER.set(str(name) if name else "-")


def current_request_id() -> str:
    return _REQUEST_ID.get()


def current_user() -> str:
    return _USER.get()
