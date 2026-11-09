# -*- coding: utf-8 -*-
"""PEP 503 / PEP 691 简单索引路由。"""
from __future__ import annotations

import hashlib
import html
import json
from urllib.parse import quote

from ..config import CFG
from ..services import auth_service, package_service
from ..utils.naming import norm


class SimpleRoutes:
    def _accepts_json(self) -> bool:
        a = self.headers.get("Accept", "")
        return "application/vnd.pypi.simple.v1+json" in a

    def simple_root(self):
        if not CFG["open_read"] and not auth_service.auth_user(self.headers, self._client_ip()):
            return self._err(401, "需要登录")
        names = package_service.simple_projects()
        etag = '"' + hashlib.sha256("\n".join(names).encode()).hexdigest()[:32] + '"'
        cache = {"Vary": "Accept", "Cache-Control": "no-cache", "ETag": etag}
        if (self.headers.get("If-None-Match") or "") == etag:
            return self._send(304, b"", "text/html; charset=utf-8", cache)
        if self._accepts_json():
            payload = {"meta": {"api-version": "1.1"},
                       "projects": [{"name": n} for n in names]}
            return self._send(200, json.dumps(payload).encode("utf-8"),
                              "application/vnd.pypi.simple.v1+json", cache)
        links = "\n".join(
            '<a href="/simple/{}/">{}</a><br/>'.format(
                html.escape(quote(n, safe="")), html.escape(n))
            for n in names)
        page = ("<!DOCTYPE html><html><head>"
                '<meta name="pypi:repository-version" content="1.1">'
                f"<title>Simple index</title></head><body>\n{links}\n</body></html>")
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", cache)

    def simple_project(self, name):
        if not CFG["open_read"] and not auth_service.auth_user(self.headers, self._client_ip()):
            return self._err(401, "需要登录")
        proj = norm(name)
        rows = package_service.simple_project_files(proj)
        if not rows:
            return self._err(404, f"没有这个项目: {proj}")
        sig = "\n".join(f"{r['filename']}:{r['sha256']}:{r['uploaded_at']}" for r in rows)
        etag = '"' + hashlib.sha256(sig.encode()).hexdigest()[:32] + '"'
        cache = {"Vary": "Accept", "Cache-Control": "no-cache", "ETag": etag}
        if (self.headers.get("If-None-Match") or "") == etag:
            return self._send(304, b"", "text/html; charset=utf-8", cache)
        if self._accepts_json():
            files = [{
                "filename": r["filename"],
                "url": f"/files/{quote(r['filename'], safe='')}",
                "hashes": {"sha256": r["sha256"]},
                "size": r["size"],
                "upload-time": r["uploaded_at"],
                **({"requires-python": r["requires_python"]} if r["requires_python"] else {}),
            } for r in rows]
            versions = sorted({r["version"] for r in rows})
            payload = {"meta": {"api-version": "1.1"}, "name": proj,
                       "files": files, "versions": versions}
            return self._send(200, json.dumps(payload).encode("utf-8"),
                              "application/vnd.pypi.simple.v1+json", cache)
        links = "\n".join(
            '<a href="/files/{}#sha256={}">{}</a><br/>'.format(
                html.escape(quote(r["filename"], safe="")),
                html.escape(r["sha256"] or ""),
                html.escape(r["filename"]))
            for r in rows)
        safe_proj = html.escape(proj)
        page = ("<!DOCTYPE html><html><head>"
                '<meta name="pypi:repository-version" content="1.1">'
                f"<title>Links for {safe_proj}</title></head><body>\n"
                f"<h1>Links for {safe_proj}</h1>\n{links}\n</body></html>")
        self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", cache)
