# -*- coding: utf-8 -*-
"""路由组合根：拼装所有路由 mixin，并保持原 dispatch 顺序。"""
from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

from ..log import current_request_id, current_user, get_logger
from ..middleware.base import BaseHandler
from ..middleware.security import is_cross_site
from ..services.audit_service import audit
from .admin import AdminRoutes
from .auth import AuthRoutes
from .files import FileRoutes
from .packages import PackageRoutes
from .pages import PageRoutes
from .simple import SimpleRoutes
from .uploads import UploadRoutes
from .users import UserRoutes

_log = get_logger("error")


class Handler(BaseHandler, AuthRoutes, PackageRoutes, UploadRoutes, FileRoutes,
              AdminRoutes, UserRoutes, SimpleRoutes, PageRoutes):
    # ---------- 路由 ----------
    def do_GET(self):
        self._route("GET")

    def do_HEAD(self):
        self._route("GET")

    def do_PUT(self):
        self._route("PUT")

    def do_POST(self):
        self._route("POST")

    def do_DELETE(self):
        self._route("DELETE")

    def do_PATCH(self):
        self._route("PATCH")

    def _route(self, method):
        # 先按 Content-Length 记账：若某个分支提前返回、请求体没读完，
        # _send 会据此断开连接，避免残留字节被当成下一个请求（keep-alive 错位）
        self._body_left = int(self.headers.get("Content-Length") or 0)
        u = urlparse(self.path)
        path = unquote(u.path)
        q = parse_qs(u.query)
        try:
            if method in ("PUT", "POST", "DELETE", "PATCH") and is_cross_site(self.headers):
                self._body_left = int(self.headers.get("Content-Length") or 0)
                return self._err(403, "拒绝跨站请求")
            # POST / 是 twine/uv 的上传入口，必须先于首页判断；
            # /legacy/ 是 PyPI 传统上传端点，poetry 自定义源常照抄这个路径
            if method == "POST" and path in ("/", "/legacy", "/legacy/"):
                return self.upload_multipart()
            if method == "GET" and path in ("/", "/index.html", "/upload", "/upload/", "/login"):
                return self.page_index()
            if path == "/health":
                return self.health()
            if path == "/api/config":
                return self.api_config()
            if path == "/api/client-config":
                return self.api_client_config(method)
            if path == "/api/whoami":
                return self.api_whoami()
            if path == "/api/packages":
                return self.api_packages(q)
            if path == "/api/export.csv":
                return self.api_export()
            if path == "/api/audit":
                return self.api_audit(q)
            if path == "/api/backup" and method == "POST":
                return self.api_backup()
            if path == "/api/password" and method == "POST":
                return self.api_password(method)
            if path == "/api/users":
                return self.api_users(method)
            if path == "/api/users/batch" and method == "POST":
                return self.api_users_batch(method)
            if path.startswith("/api/users/") and method == "DELETE":
                return self.api_user_delete(path)
            if path == "/simple" or path == "/simple/":
                return self.simple_root()
            if path.startswith("/simple/"):
                return self.simple_project(path[len("/simple/"):].strip("/"))
            if path.startswith("/files/"):
                return self.serve_file(path[len("/files/"):])
            if path == "/api/upload" or (method == "PUT" and path.startswith("/api/upload/")):
                return self.upload_raw(path)
            if path == "/api/trash/empty" and method == "POST":
                return self.trash_empty()
            if path.startswith("/api/files/") and path.rstrip("/").endswith("/purge") \
                    and method == "DELETE":
                return self.file_purge(path)
            if path.startswith("/api/files/") and method == "DELETE":
                return self.file_delete(path)
            if path.startswith("/api/files/") and method == "POST":
                return self.file_restore(path)
            if path.startswith("/api/files/") and method == "PATCH":
                return self.file_patch(path)
            if path.startswith("/api/projects/") and method == "PATCH":
                return self.project_patch(path)
            if path.startswith("/assets/") or path.startswith("/js/"):
                return self.static_asset(path)
            return self._err(404, "未知路径")
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as e:
            # request_id 与访问日志、审计记录共用，用户报障时报这一个编号即可定位
            rid = current_request_id()
            _log.exception("未处理异常 %s %s", method, self.path)
            audit(current_user() if current_user() != "-" else "anonymous", "server_error",
                  self.path, {"error_id": rid, "method": method,
                              "exception": type(e).__name__}, self._client_ip())
            try:
                self._err(500, f"服务器内部错误（错误编号 {rid}），请联系管理员查看服务日志")
            except Exception:
                self.close_connection = True
