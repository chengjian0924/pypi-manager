# -*- coding: utf-8 -*-
"""管理列表、导出与项目备注路由。"""
from __future__ import annotations

import csv
import io
import json
from urllib.parse import unquote

from ..constants import MAX_FIELD
from ..services import package_service
from ..utils.naming import norm


class PackageRoutes:
    def api_packages(self, q):
        user = self._need_auth()
        if not user:
            return
        kw = (q.get("q", [""])[0] or "").strip().lower()
        mode = q.get("deleted", ["0"])[0]
        return self._json(package_service.list_packages(user, kw, mode))

    def api_export(self):
        user = self._need_auth({"admin"})
        if not user:
            return
        rows = package_service.export_rows()
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["filename", "project", "version", "py_tag", "abi_tag", "plat_tag",
                    "bytes", "sha256", "uploaded_at", "uploaded_by", "note"])
        for r in rows:
            w.writerow(list(r))
        body = ("\ufeff" + buf.getvalue()).encode("utf-8")
        self._send(200, body, "text/csv; charset=utf-8",
                   {"Content-Disposition": 'attachment; filename="pypi-manifest.csv"'})

    def project_patch(self, path):
        user = self._need_auth({"admin"})
        if not user:
            return
        proj = norm(unquote(path.rstrip("/").rsplit("/", 1)[-1]))
        try:
            data = json.loads(self._read_body(cap=MAX_FIELD) or b"{}")
        except Exception:
            return self._err(400, "请求体不是合法 JSON")
        desc = str(data.get("description", ""))[:1000]
        status, payload = package_service.update_project(
            proj, desc, self.path, user, self._client_ip())
        return self._json(payload, status)
