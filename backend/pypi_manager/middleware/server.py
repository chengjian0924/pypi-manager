# -*- coding: utf-8 -*-
"""有并发上限的线程服务器。"""
from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer

from ..constants import MAX_WORKERS
from ..log import get_logger

_log = get_logger("http")


class LimitedThreadingHTTPServer(ThreadingHTTPServer):
    """有并发上限的线程服务器：超出上限直接 503，避免线程无限增长"""

    daemon_threads = False
    block_on_close = True
    request_queue_size = 128
    allow_reuse_address = True

    def __init__(self, addr, handler, max_workers=MAX_WORKERS):
        super().__init__(addr, handler)
        self._sem = threading.BoundedSemaphore(max(1, max_workers))
        self._active = 0
        self._active_lock = threading.Lock()

    def _release(self):
        with self._active_lock:
            self._active = max(0, self._active - 1)
        self._sem.release()

    def process_request(self, request, client_address):
        if not self._sem.acquire(blocking=False):
            # 直接回 503 的路径不经过 Handler，必须在这里单独留痕，否则并发打满时日志是空的
            _log.warning("并发已达上限 %s，拒绝连接 %s", max(1, max_workers), client_address[0])
            try:
                request.sendall(b"HTTP/1.1 503 Service Unavailable\r\n"
                                b"Content-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            finally:
                self.shutdown_request(request)
            return
        with self._active_lock:
            self._active += 1
        try:
            super().process_request(request, client_address)
        except Exception:
            self._release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._release()

    def active(self) -> int:
        with self._active_lock:
            return self._active
