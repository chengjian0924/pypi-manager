# -*- coding: utf-8 -*-
"""页面与静态资源路由：首页、前端资源、公开配置、健康检查。"""
from __future__ import annotations

import mimetypes
import os

from ..config import CFG
from ..constants import APP_NAME, VERSION
from ..services import storage_service
from ..utils.http import http_date

# 明确覆盖常见前端资源类型，避免依赖系统 MIME 表
MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".txt": "text/plain; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


class PageRoutes:
    def api_config(self):
        """公开配置：供前端读取上传上限等只读信息。"""
        self._json({"ok": True, "max_upload": int(CFG["max_upload"]),
                    "version": VERSION, "open_read": bool(CFG["open_read"])})

    def health(self):
        snap = storage_service.health_snapshot()
        status = snap["status"]
        return self._json({"ok": status == "ok", "status": status, "app": APP_NAME,
                           "version": VERSION, "checks": snap["checks"]},
                          200 if status == "ok" else 503)

    def page_index(self):
        root = os.path.abspath(CFG["frontend_dir"])
        path = os.path.join(root, "index.html")
        if not os.path.isfile(path):
            body = ("<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
                    "<title>前端资源缺失</title></head><body><h1>前端资源缺失</h1>"
                    "<p>请将前端文件放置到 <code>frontend/</code> 目录，"
                    "或通过 <code>--frontend-dir</code> 指定正确路径。</p></body></html>")
            return self._send(503, body.encode("utf-8"), "text/html; charset=utf-8",
                              {"Cache-Control": "no-cache"})
        if not self._serve_static(path, "text/html; charset=utf-8", "no-cache"):
            return self._err(500, "首页读取失败")

    def static_asset(self, path: str):
        root = os.path.abspath(CFG["frontend_dir"])
        if path.startswith("/assets/"):
            rel, base = path[len("/assets/"):], os.path.join(root, "assets")
        else:
            rel, base = path[len("/js/"):], os.path.join(root, "js")
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel or any(part in ("", ".", "..") for part in rel.split("/")):
            return self._err(404, "文件不存在")
        full = os.path.abspath(os.path.join(base, rel))
        try:
            if os.path.commonpath([full, os.path.abspath(base)]) != os.path.abspath(base):
                return self._err(404, "文件不存在")
        except ValueError:
            return self._err(404, "文件不存在")
        if not os.path.isfile(full):
            return self._err(404, "文件不存在")
        ext = os.path.splitext(full)[1].lower()
        ctype = MIME.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
        # 代码/样式必须每次用 ETag 重新校验，避免发布后浏览器继续用旧文件
        revalidate = ext in (".html", ".js", ".mjs", ".css", ".map")
        cache = "no-cache" if revalidate else "public, max-age=3600"
        if not self._serve_static(full, ctype, cache):
            return self._err(500, "静态文件读取失败")

    def _serve_static(self, path: str, ctype: str, cache_control: str) -> bool:
        try:
            st = os.stat(path)
            with open(path, "rb") as f:
                body = f.read()
        except OSError:
            return False
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        headers = {"ETag": etag, "Last-Modified": http_date(st.st_mtime),
                   "Cache-Control": cache_control}
        if (self.headers.get("If-None-Match") or "") == etag:
            self._send(304, b"", ctype, headers)
            return True
        self._send(200, body, ctype, headers)
        return True
