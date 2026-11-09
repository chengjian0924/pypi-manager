# -*- coding: utf-8 -*-
"""认证相关路由：whoami 与请求级认证辅助。"""
from __future__ import annotations

import base64

from ..constants import LOGIN_BLOCK_SECONDS
from ..services import auth_service
from ..services.audit_service import audit


class AuthRoutes:
    def _auth_username(self) -> str:
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return ""
        try:
            return base64.b64decode(h[6:]).decode("utf-8").partition(":")[0]
        except Exception:
            return ""

    def _locked_deny(self) -> bool:
        """登录失败过多时统一返回 429，便于客户端识别"""
        u = self._auth_username()
        if u and auth_service.login_locked(self._client_ip(), u):
            self._err(429, "登录失败次数过多，请稍后再试",
                      {"Retry-After": str(LOGIN_BLOCK_SECONDS)})
            return True
        return False

    def _need_auth(self, roles=None) -> dict | None:
        """认证 + 角色校验；roles 为空表示仅要求登录（admin 始终放行）"""
        if self._locked_deny():
            return None
        user = auth_service.auth_user(self.headers, self._client_ip())
        if user and (roles is None or auth_service.is_admin(user) or user["role"] in roles):
            return user
        n = int(self.headers.get("Content-Length") or 0)
        if n > 0:
            self._body_left = n
        if user:
            audit(user["name"], "forbidden", self.path,
                  {"role": user["role"]}, self._client_ip())
            self._err(403, "当前账号无权执行该操作")
        else:
            self._err(401, "需要登录", {"WWW-Authenticate": 'Basic realm="pypi-manager"'})
        return None

    def _deny(self, user, msg="无权访问该项目或该资源"):
        audit(user["name"], "forbidden", self.path,
              {"role": user["role"]}, self._client_ip())
        self._err(403, msg)

    def api_whoami(self):
        if self._locked_deny():
            return
        user = auth_service.auth_user(self.headers, self._client_ip())
        if not user:
            audit("anonymous", "login_failed", "", {"ip": self._client_ip()})
            return self._err(401, "未登录")
        audit(user["name"], "login", "", {"ip": self._client_ip()})
        return self._json({"ok": True, "user": user["name"],
                           "role": user["role"], "scope": user["scope"],
                           "pwd_default": bool(user.get("pwd_default"))})
