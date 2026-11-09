# -*- coding: utf-8 -*-
"""流式 multipart 解析（twine upload）。"""
from __future__ import annotations

import re

from ..constants import MAX_FIELD
from .naming import human_size


class _MultipartReader:
    """在 socket 上增量读取 multipart，避免把整个请求体放进内存"""

    def __init__(self, rfile, remaining: int):
        self.rfile = rfile
        self.remaining = remaining
        self.buf = b""
        self.eof = False

    def fill(self, n: int = 65536):
        if self.remaining <= 0:
            self.eof = True
            return
        chunk = self.rfile.read(min(n, self.remaining))
        if not chunk:
            self.eof = True
            self.remaining = 0
            return
        self.remaining -= len(chunk)
        self.buf += chunk

    def read_line(self, limit: int = 8192) -> bytes:
        while b"\r\n" not in self.buf:
            if self.eof:
                raise ValueError("请求体不完整（multipart 头部未结束）")
            if len(self.buf) > limit:
                raise ValueError("multipart 头部过大")
            self.fill()
        line, _, self.buf = self.buf.partition(b"\r\n")
        return line

    def pump_until(self, marker: bytes, sink, limit: int | None = None) -> int:
        keep = len(marker) - 1
        total = 0
        while True:
            idx = self.buf.find(marker)
            if idx >= 0:
                data = self.buf[:idx]
                self.buf = self.buf[idx + len(marker):]
                if data:
                    sink(data)
                    total += len(data)
                return total
            if len(self.buf) > keep:
                data = self.buf[:-keep]
                self.buf = self.buf[-keep:]
                sink(data)
                total += len(data)
                if limit is not None and total > limit:
                    raise ValueError("multipart 字段超出上限")
            if self.eof:
                raise ValueError("请求体不完整（multipart 未找到结束边界）")
            self.fill()


def save_multipart_stream(rfile, total: int, boundary: bytes, dest_path: str,
                          max_file: int):
    """流式解析 multipart，只把 content 文件字段写入 dest_path"""
    rd = _MultipartReader(rfile, total)
    delim = b"\r\n--" + boundary
    first = rd.read_line()
    if first.strip() != b"--" + boundary:
        raise ValueError("multipart 格式错误：缺少起始边界")
    fields: dict[str, str] = {}
    fname = ""
    written = 0
    found = False
    while True:
        headers: dict[bytes, bytes] = {}
        while True:
            line = rd.read_line()
            if not line:
                break
            k, _, v = line.partition(b":")
            headers[k.strip().lower()] = v.strip()
        if not headers:
            break
        disp = headers.get(b"content-disposition", b"").decode("latin-1")
        m = re.search(r'name="([^"]*)"(?:[^;]*;\s*filename="([^"]*)")?', disp)
        name = m.group(1) if m else ""
        part_name = (m.group(2) or "") if m else ""
        if name == "content":
            fname = part_name or "upload.bin"
            found = True
            fh = open(dest_path, "wb")

            def sink(data, _fh=fh):
                nonlocal written
                written += len(data)
                if written > max_file:
                    raise ValueError(f"文件大小超出上限（上限 {human_size(max_file)}）")
                _fh.write(data)

            try:
                rd.pump_until(delim, sink)
            finally:
                fh.close()
        else:
            chunks: list[bytes] = []

            def sink_field(data):
                if sum(len(c) for c in chunks) + len(data) > MAX_FIELD:
                    raise ValueError("multipart 字段体积过大")
                chunks.append(data)

            rd.pump_until(delim, sink_field)
            fields[name] = b"".join(chunks).decode("utf-8", "replace")
        while len(rd.buf) < 2 and not rd.eof:
            rd.fill()
        if rd.buf.startswith(b"--"):
            break
        if rd.buf.startswith(b"\r\n"):
            rd.buf = rd.buf[2:]
        else:
            raise ValueError("multipart 边界后格式错误")
    # 读掉尾部残留，保证 keep-alive 连接上的下一个请求不会错位
    while not rd.eof:
        rd.fill()
    if not found:
        return "", fields, 0
    return fname, fields, written
