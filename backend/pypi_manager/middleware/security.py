# -*- coding: utf-8 -*-
"""安全响应头与 CSRF 判定。"""
from __future__ import annotations

from urllib.parse import urlparse

from ..config import CFG


def security_headers(extra=None) -> dict:
    extra = dict(extra or {})
    extra.setdefault("X-Content-Type-Options", "nosniff")
    extra.setdefault("X-Frame-Options", "DENY")
    extra.setdefault("Referrer-Policy", "no-referrer")
    extra.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    extra.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
    if CFG.get("tls"):
        extra.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return extra


def is_cross_site(headers) -> bool:
    """写操作 CSRF 防护：浏览器跨站请求直接拒绝；非浏览器客户端不受影响"""
    site = headers.get("Sec-Fetch-Site", "")
    if site == "cross-site":
        return True
    origin = headers.get("Origin", "")
    if origin:
        host = headers.get("Host", "")
        return urlparse(origin).netloc != host
    return False
