# -*- coding: utf-8 -*-
"""HTTP 请求/响应基础能力：请求体、响应、gzip、安全头、Range。"""
from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import time
from http.server import BaseHTTPRequestHandler

from ..config import CFG, paths
from ..constants import (APP_NAME, CHUNK, MAX_FIELD, REQUEST_TIMEOUT,
                         UPLOAD_SEM, VERSION)
from ..log import clear_request, current_user, get_logger, set_request
from ..utils.http import parse_range_header
from ..utils.naming import human_size
from .security import security_headers

_access_log = get_logger("access")
_server_log = get_logger("http")


class BaseHandler(BaseHTTPRequestHandler):
    server_version = f"{APP_NAME}/{VERSION}"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = REQUEST_TIMEOUT
    _body_left = 0
    _resp_bytes = 0
    _started_at = 0.0

    def version_string(self):
        return self.server_version

    # ---------- 请求上下文与访问日志 ----------
    def handle_one_request(self):
        """每个请求独立 request_id，保证并发下的日志不会串行错乱。"""
        self._resp_bytes = 0
        self._started_at = time.monotonic()
        set_request()
        try:
            super().handle_one_request()
        finally:
            clear_request()

    def log_request(self, code="-", size="-"):
        """访问日志：IP、用户、请求行、状态、字节、耗时；慢请求与 4xx/5xx 升级级别。"""
        dur_ms = (time.monotonic() - self._started_at) * 1000 if self._started_at else 0.0
        try:
            status = int(code)
        except (TypeError, ValueError):
            status = 0
        sent = size if isinstance(size, int) and size >= 0 else self._resp_bytes
        slow_ms = int(CFG.get("slow_ms") or 0)
        if status >= 500:
            level = logging.ERROR
        elif status >= 400 or (slow_ms and dur_ms >= slow_ms):
            level = logging.WARNING
        else:
            level = logging.INFO
        suffix = " [slow]" if slow_ms and dur_ms >= slow_ms else ""
        _access_log.log(
            level, '%s %s "%s" %s %s %.1fms%s',
            self._client_ip() or "-", current_user(),
            getattr(self, "requestline", "-"), code, sent, dur_ms, suffix,
            extra={"ip": self._client_ip(), "method": getattr(self, "command", "-"),
                   "path": getattr(self, "path", "-"), "status": status, "bytes": sent,
                   "duration_ms": round(dur_ms, 1), "slow": dur_ms >= slow_ms > 0})

    def log_message(self, format, *args):
        """协议层错误（坏请求行、超时等）走同一 logger，别绕过日志系统。"""
        _server_log.warning("%s %s", self._client_ip() or "-", format % args)

    def _security_headers(self, extra=None) -> dict:
        return security_headers(extra)

    def _send(self, status, body: bytes, ctype="application/json; charset=utf-8", extra=None):
        extra = dict(extra or {})
        # 请求体没被读完时必须断开连接，否则残留字节会被当成下一个请求解析
        if self._body_left > 0:
            self.close_connection = True
            extra.setdefault("Connection", "close")
        compressible = ctype.split(";")[0] in (
            "application/json", "text/html", "text/csv", "application/vnd.pypi.simple.v1+json",
            "text/css", "text/plain", "application/javascript", "image/svg+xml")
        accept = self.headers.get("Accept-Encoding", "") if self.headers else ""
        if (compressible and len(body) >= 1024 and "gzip" in accept
                and "Content-Encoding" not in extra):
            body = gzip.compress(body, 6)
            extra["Content-Encoding"] = "gzip"
            extra.setdefault("Vary", "Accept-Encoding")
        self._resp_bytes = len(body)   # 必须在 send_response 之前，访问日志要用
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in self._security_headers(extra).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status=200, extra=None):
        extra = dict(extra or {})
        extra.setdefault("Cache-Control", "no-store")
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), extra=extra)

    def _err(self, status, msg, extra=None):
        self._json({"ok": False, "error": msg}, status, extra)

    def _client_ip(self) -> str:
        return self.client_address[0] if self.client_address else ""

    # ---------- 请求体 ----------
    def _read_body(self, cap=MAX_FIELD) -> bytes:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return b""
        if n > cap:
            self._body_left = n
            raise ValueError(f"请求体超出上限（上限 {human_size(cap)}）")
        buf = bytearray()
        remain = n
        while remain > 0:
            chunk = self.rfile.read(min(CHUNK, remain))
            if not chunk:
                break
            buf.extend(chunk)
            remain -= len(chunk)
        self._body_left = remain
        return bytes(buf)

    def _save_body_to(self, dest: str) -> int:
        """把请求体直接落盘（大 wheel 不占内存）"""
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            raise ValueError("文件内容为空（0 字节）")
        cap = int(CFG["max_upload"])
        if n > cap:
            raise ValueError(f"文件大小超出上限（{human_size(n)}，上限 {human_size(cap)}）")
        written = 0
        remain = n
        with open(dest, "wb") as f:
            while remain > 0:
                chunk = self.rfile.read(min(CHUNK, remain))
                if not chunk:
                    break
                f.write(chunk)
                written += len(chunk)
                remain -= len(chunk)
        self._body_left = remain
        return written

    # ---------- 通用资源检查 ----------
    def _disk_ok(self) -> bool:
        try:
            free_mb = shutil.disk_usage(paths()["root"]).free // (1024 * 1024)
            return free_mb >= int(CFG["min_free_mb"])
        except OSError:
            return False

    def _acquire_upload(self) -> bool:
        if not UPLOAD_SEM.acquire(blocking=False):
            self._err(503, "上传并发已满，请稍后重试")
            return False
        return True

    def _parse_range(self, rng: str, size: int):
        return parse_range_header(rng, size)
