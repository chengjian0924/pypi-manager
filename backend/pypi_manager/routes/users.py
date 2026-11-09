# -*- coding: utf-8 -*-
"""用户管理路由。

- /api/users          列表、建号、改角色、重置密码（仅 admin）
- /api/users/batch    批量建号（仅 admin），初始密码统一
- /api/users/<name>   删除账号（仅 admin）
- /api/password       用户自助修改自己的密码（任意已登录账号）
"""
from __future__ import annotations

import json
from urllib.parse import unquote

from ..constants import DEFAULT_PASSWORD, MAX_FIELD, ROLES
from ..services import auth_service
from ..services.audit_service import audit


class UserRoutes:
    def _read_json(self):
        """读取并解析请求体；成功返回 (data, False)，出错时已写好响应并返回 (None, True)"""
        try:
            raw = self._read_body(cap=MAX_FIELD)
        except ValueError as e:
            self._err(413, str(e))
            return None, True
        try:
            return json.loads(raw or b"{}"), False
        except ValueError:
            self._err(400, "请求体不是合法 JSON")
            return None, True

    def api_users(self, method):
        user = self._need_auth({"admin"})
        if not user:
            return
        if method == "GET":
            return self._json({"ok": True, "items": auth_service.list_users(),
                               "roles": sorted(ROLES), "me": user["name"],
                               "default_password": DEFAULT_PASSWORD})
        if method != "POST":
            return self._err(405, "仅支持 GET / POST")
        data, bad = self._read_json()
        if bad:
            return
        try:
            action, item = auth_service.save_account(data)
        except ValueError as e:
            return self._err(400, str(e))

        if action == "created":
            event = "user_create"
        elif item.get("password_reset"):
            event = "user_password"
        else:
            event = "user_update"
        audit(user["name"], event, item["name"],
              {"role": item.get("role"), "scope": item.get("scope")}, self._client_ip())
        pub = {k: item.get(k) for k in ("name", "role", "scope", "created", "pwd_default")}
        return self._json({"ok": True, "action": action, "user": pub},
                          201 if action == "created" else 200)

    def api_users_batch(self, method):
        user = self._need_auth({"admin"})
        if not user:
            return
        if method != "POST":
            return self._err(405, "仅支持 POST")
        data, bad = self._read_json()
        if bad:
            return
        try:
            result = auth_service.batch_create(data)
        except ValueError as e:
            return self._err(400, str(e))

        audit(user["name"], "user_batch_create", ",".join(result["created"])[:500],
              {"created": len(result["created"]), "skipped": len(result["skipped"]),
               "invalid": len(result["invalid"]), "role": result["role"],
               "scope": result["scope"]}, self._client_ip())
        return self._json({"ok": True, "action": "batch_created",
                           "created": result["created"], "skipped": result["skipped"],
                           "invalid": result["invalid"], "role": result["role"],
                           "scope": result["scope"], "password": result["password"]},
                          201 if result["created"] else 200)

    def api_user_delete(self, path):
        user = self._need_auth({"admin"})
        if not user:
            return
        name = unquote(path[len("/api/users/"):].strip("/"))
        if name == "batch":
            return self._err(405, "批量建号请使用 POST")
        try:
            auth_service.delete_account(name, user["name"])
        except LookupError as e:
            return self._err(404, str(e))
        except ValueError as e:
            return self._err(400, str(e))
        audit(user["name"], "user_delete", name, {}, self._client_ip())
        return self._json({"ok": True})

    def api_password(self, method):
        """自助改密：任何已登录账号都能改自己的密码，必须提供原密码。"""
        user = self._need_auth()
        if not user:
            return
        if method != "POST":
            return self._err(405, "仅支持 POST")
        data, bad = self._read_json()
        if bad:
            return
        if not isinstance(data, dict):
            return self._err(400, "请求体必须是 JSON 对象")
        try:
            auth_service.change_own_password(
                user["name"], str(data.get("old_password") or ""),
                str(data.get("new_password") or ""))
        except LookupError as e:
            return self._err(404, str(e))
        except ValueError as e:
            return self._err(400, str(e))
        audit(user["name"], "user_password_self", user["name"], {}, self._client_ip())
        return self._json({"ok": True, "user": user["name"]})
