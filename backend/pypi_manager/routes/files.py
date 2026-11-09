# -*- coding: utf-8 -*-
"""文件下载与单文件操作路由。"""
from __future__ import annotations

import json
import os

from ..config import CFG
from ..constants import CHUNK, MAX_FIELD
from ..services import auth_service, package_service
from ..utils.http import content_disposition, http_date


class FileRoutes:
    def _id_from_path(self, path):
        try:
            return int(path.rstrip("/").rsplit("/", 1)[-1])
        except ValueError:
            return None

    def serve_file(self, filename):
        if not CFG["open_read"] and not auth_service.auth_user(self.headers, self._client_ip()):
            return self._err(401, "需要登录")
        found = package_service.lookup_file(filename)
        if found.get("error") == "invalid":
            return self._err(400, "非法文件名")
        if found.get("error"):
            return self._err(404, "文件不存在")
        path = found["path"]

        st = os.stat(path)
        size = st.st_size
        etag = f'"{found["sha256"]}"' if found["sha256"] else f'"{size:x}-{int(st.st_mtime):x}"'
        last_mod = http_date(st.st_mtime)
        cache = {"ETag": etag, "Last-Modified": last_mod,
                 "Cache-Control": "public, max-age=3600", "Accept-Ranges": "bytes"}

        if (self.headers.get("If-None-Match") or "") == etag:
            return self._send(304, b"", "application/octet-stream", cache)

        rng = self.headers.get("Range", "")
        parsed = self._parse_range(rng, size)
        if parsed is None:
            return self._err(416, "请求范围无效",
                             {"Content-Range": f"bytes */{size}"})
        start, end = parsed

        length = end - start + 1
        status = 206 if rng else 200
        disp = content_disposition(found["name"])

        self._resp_bytes = length    # 必须在 send_response 之前，访问日志要用
        self.send_response(status)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(length))
        self.send_header("Content-Disposition", disp)
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        for k, v in {**cache, **self._security_headers()}.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            with open(path, "rb") as f:
                f.seek(start)
                remain = length
                while remain > 0:
                    chunk = f.read(min(CHUNK, remain))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remain -= len(chunk)

    def file_delete(self, path):
        user = self._need_auth()
        if not user:
            return
        status, payload = package_service.delete_file(
            user, self._id_from_path(path), self.path, self._client_ip())
        return self._json(payload, status)

    def file_restore(self, path):
        user = self._need_auth()
        if not user:
            return
        status, payload = package_service.restore_file(
            user, self._id_from_path(path), self.path, self._client_ip())
        return self._json(payload, status)

    def file_purge(self, path):
        """管理员：从回收站彻底删除（不可恢复）"""
        user = self._need_auth({"admin"})
        if not user:
            return
        status, payload = package_service.purge_file(
            user, self._id_from_path(path.rstrip("/")[:-len("/purge")]),
            self.path, self._client_ip())
        return self._json(payload, status)

    def trash_empty(self):
        """管理员：清空回收站"""
        user = self._need_auth({"admin"})
        if not user:
            return
        status, payload = package_service.empty_trash(
            user, self.path, self._client_ip())
        return self._json(payload, status)

    def file_patch(self, path):
        user = self._need_auth()
        if not user:
            return
        try:
            data = json.loads(self._read_body(cap=MAX_FIELD) or b"{}")
        except Exception:
            return self._err(400, "请求体不是合法 JSON")
        status, payload = package_service.update_file_note(
            user, self._id_from_path(path), data, self.path, self._client_ip())
        return self._json(payload, status)
