# -*- coding: utf-8 -*-
"""管理员路由：审计日志、备份与站点级可编辑配置。"""
from __future__ import annotations

import os
import traceback

from ..services import audit_service, settings_service, storage_service
from ..services.audit_service import audit


class AdminRoutes:
    def api_audit(self, q):
        user = self._need_auth({"admin"})
        if not user:
            return
        try:
            limit = max(1, min(int(q.get("limit", ["200"])[0]), 2000))
        except (TypeError, ValueError):
            return self._err(400, "limit 必须是 1-2000 的整数")
        return self._json({"ok": True, "items": audit_service.read_recent(limit)})

    def api_backup(self):
        user = self._need_auth({"admin"})
        if not user:
            return
        try:
            dest = storage_service.backup_now()
        except Exception as e:
            traceback.print_exc()
            return self._err(500, f"备份失败：{e}")
        audit(user["name"], "backup", os.path.basename(dest), {}, self._client_ip())
        return self._json({"ok": True, "backup": dest})

    def api_client_config(self, method):
        """「客户端接入」文案：读需要登录（页面要用），写仅 admin。

        段落内容只做纯文本展示，前端负责 HTML 转义；此处只校验长度与字符。
        """
        if method == "GET":
            if not self._need_auth():
                return
            return self._json({"ok": True, **settings_service.get_client_config()})

        user = self._need_auth({"admin"})
        if not user:
            return
        if method == "DELETE":
            result = settings_service.reset_client_config(user, self._client_ip())
            return self._json({"ok": True, **result})
        if method != "PUT":
            return self._err(405, "仅支持 GET / PUT / DELETE")

        data, bad = self._read_json()   # 定义在 UserRoutes，Handler 组合后可用
        if bad:
            return
        try:
            result = settings_service.save_client_config(data, user, self._client_ip())
        except ValueError as e:
            return self._err(400, str(e))
        return self._json({"ok": True, **result})
