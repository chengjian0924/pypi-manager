# -*- coding: utf-8 -*-
"""HTTP 通用小工具：日期格式、Range 解析、Content-Disposition。"""
from __future__ import annotations

import time
from urllib.parse import quote


def http_date(ts: float) -> str:
    return time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(ts))


def content_disposition(name: str) -> str:
    """生成兼容中文文件名的附件下载头，并清洗引号。"""
    fallback = name.encode("ascii", "replace").decode().replace('"', "_")
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def parse_range_header(rng: str, size: int):
    """解析单段 Range。

    返回 (start, end)；无需处理时返回 (0, size-1)；语法非法返回 None。
    """
    start, end = 0, size - 1
    if not (rng.startswith("bytes=") and "," not in rng):
        return start, end
    spec = rng[6:].strip()
    try:
        a, _, b = spec.partition("-")
        if a:
            start = int(a)
            end = int(b) if b else size - 1
        elif b:
            start = max(0, size - int(b))
        if start > end or start >= size:
            raise ValueError
        end = min(end, size - 1)
    except ValueError:
        return None
    return start, end
