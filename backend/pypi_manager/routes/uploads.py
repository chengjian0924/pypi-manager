# -*- coding: utf-8 -*-
"""上传路由：PUT 原始体与 POST multipart（twine）。"""
from __future__ import annotations

import errno
import os
import re
import threading
from urllib.parse import parse_qs, urlparse

from ..config import CFG, paths
from ..constants import UPLOAD_SEM
from ..errors import ServiceError
from ..services import package_service
from ..utils.multipart import save_multipart_stream
from ..utils.naming import human_size


class UploadRoutes:
    def _reject_chunked(self) -> bool:
        """分块传输没有 Content-Length，无法预估大小与上限，直接说明用法。

        不拦的话会走到「文件内容为空（0 字节）」，让用户误以为是文件问题。
        """
        if "chunked" not in (self.headers.get("Transfer-Encoding") or "").lower():
            return False
        self.close_connection = True   # 请求体不读，必须断开，避免残留字节错位
        self._err(411, "不支持分块传输（chunked）上传：请改用带 Content-Length 的方式，"
                       "例如 curl -T 文件、twine upload、uv publish",
                  {"Connection": "close"})
        return True

    def upload_raw(self, path: str = ""):
        """PUT /api/upload/<文件名> 或 /api/upload?filename=xxx，请求体就是文件本体。

        前者让命令行更短：curl -u u:p -T "dist/*.whl" http://host/api/upload/
        （URL 以斜杠结尾时 curl 会自动把本地文件名拼到后面）
        """
        user = self._need_auth({"uploader"})
        if not user:
            return
        if self._reject_chunked():
            return
        q = parse_qs(urlparse(self.path).query)
        filename = ""
        rest = path.rstrip("/")[len("/api/upload/"):] if path.startswith("/api/upload/") else ""
        if rest:
            filename = os.path.basename(rest)
        if not filename:
            filename = (q.get("filename", [""])[0] or
                        self.headers.get("X-Filename", "")).strip()
        note = q.get("note", [""])[0] if q.get("note") else ""
        if not filename:
            return self._err(400, "缺少 filename 参数")
        if not self._disk_ok():
            return self._err(507, "磁盘剩余空间不足，已暂停上传，请联系管理员清理空间")
        if not self._acquire_upload():
            return
        tmp = os.path.join(paths()["tmp"], f"up-{os.getpid()}-{threading.get_ident()}")
        try:
            try:
                size = self._save_body_to(tmp)
            except ValueError as e:
                msg = str(e)
                return self._err(413 if "上限" in msg else 400, msg)
            except OSError as e:
                if e.errno == errno.ENOSPC:
                    return self._err(507, "服务器存储空间不足：磁盘已写满，请联系管理员清理空间后重试")
                raise
            if not size:
                return self._err(400, "文件内容为空（0 字节）")
            if self._body_left:
                return self._err(400, "网络中断或请求体不完整，文件未完整接收，请重试")
            try:
                status, payload = package_service.store(
                    filename, tmp, user, note, self._client_ip())
            except OSError as e:
                if e.errno == errno.ENOSPC:
                    return self._err(507, "服务器存储空间不足：磁盘已写满，请联系管理员清理空间后重试")
                raise
            except ServiceError as e:
                return self._err(e.status, e.message)
            return self._json(payload, status)
        finally:
            UPLOAD_SEM.release()
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    def _upload_client_note(self) -> str:
        """按 User-Agent 标注来源，审计里能分清是 twine 还是 uv/poetry 传的"""
        ua = (self.headers.get("User-Agent") or "").lower()
        if "twine" in ua:
            return "twine upload"
        if "poetry" in ua:
            return "poetry publish"
        if "uv/" in ua:
            return "uv publish"
        return "命令行 multipart 上传"

    def upload_multipart(self):
        """POST / 兼容 twine upload，流式解析，不把整个请求体读进内存"""
        user = self._need_auth({"uploader"})
        if not user:
            return
        if self._reject_chunked():
            return
        ctype = self.headers.get("Content-Type", "")
        m = re.search(r'boundary="?([^";]+)"?', ctype)
        if not m:
            return self._err(400, "需要 multipart/form-data")
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return self._err(400, "文件内容为空（0 字节）")
        # multipart 有边界和表单字段开销，允许 1MB 额外空间
        if n > int(CFG["max_upload"]) + 1024 * 1024:
            return self._err(413, f"文件大小超出上限（上限 {human_size(int(CFG['max_upload']))}）")
        if not self._disk_ok():
            return self._err(507, "磁盘剩余空间不足，已暂停上传，请联系管理员清理空间")
        if not self._acquire_upload():
            return
        tmp = os.path.join(paths()["tmp"], f"up-{os.getpid()}-{threading.get_ident()}")
        try:
            try:
                fname, fields, size = save_multipart_stream(
                    self.rfile, n, m.group(1).encode(), tmp, int(CFG["max_upload"]))
            except ValueError as e:
                return self._err(413 if "上限" in str(e) else 400, str(e))
            except OSError as e:
                if e.errno == errno.ENOSPC:
                    return self._err(507, "服务器存储空间不足：磁盘已写满，请联系管理员清理空间后重试")
                raise
            if not size:
                return self._err(400, "没有收到文件内容（字段 content）")
            if fields.get(":action", "") not in ("file_upload", ""):
                return self._err(400, "仅支持 file_upload")
            status, payload = package_service.store(
                fname or "upload.bin", tmp, user, self._upload_client_note(), self._client_ip())
            if status != 200:
                return self._json(payload, status)
            self._send(200, b"", "text/plain; charset=utf-8")
        finally:
            UPLOAD_SEM.release()
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
