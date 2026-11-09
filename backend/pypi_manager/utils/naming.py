# -*- coding: utf-8 -*-
"""工具层：命名、时间、文件指纹等纯函数。"""
from __future__ import annotations

import hashlib
import os
import re
import zipfile
from datetime import datetime

from ..constants import CHUNK


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def norm(name: str) -> str:
    """PEP 503 规范化：小写，连续的 - _ . 折叠成一个 -"""
    return re.sub(r"[-_.]+", "-", name).lower()


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)


_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_filename(name: str) -> str:
    """上传文件名清洗：去掉控制字符（防响应头注入）、路径分隔符，限制长度"""
    name = os.path.basename((name or "").replace("\\", "/"))
    name = _CTRL.sub("", name).replace('"', "_").strip()
    if len(name) > 200:
        stem, dot, ext = name.rpartition(".")
        name = (stem[:200 - len(ext) - 1] + "." + ext) if dot else name[:200]
    return name


def parse_artifact(fn: str):
    """解析 wheel / sdist 文件名，取不到就返回 None"""
    lower = fn.lower()
    if lower.endswith(".whl"):
        parts = fn[:-4].split("-")
        if len(parts) < 5:
            return None
        return {
            "name": parts[0], "version": parts[1],
            "py": parts[-3], "abi": parts[-2], "plat": parts[-1], "kind": "wheel",
        }
    for ext in (".tar.gz", ".zip", ".tar.bz2"):
        if lower.endswith(ext):
            base = fn[: -len(ext)]
            if "-" not in base:
                return None
            name, _, version = base.rpartition("-")
            return {"name": name, "version": version, "py": "", "abi": "", "plat": "", "kind": "sdist"}
    return None


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def read_wheel_meta(path: str):
    """从 wheel 里读 Summary / Requires-Python / 依赖数量，失败不影响上传"""
    summary, requires_python, nreq = "", "", 0
    try:
        with zipfile.ZipFile(path) as z:
            meta_name = next((n for n in z.namelist()
                              if n.endswith(".dist-info/METADATA")), None)
            if not meta_name:
                return summary, requires_python, nreq
            info = z.getinfo(meta_name)
            if info.file_size > 512 * 1024:
                return summary, requires_python, nreq
            with z.open(meta_name) as f:
                text = f.read().decode("utf-8", "replace")
    except Exception:
        return summary, requires_python, nreq
    for line in text.splitlines():
        if line.startswith("Summary:"):
            summary = line[8:].strip()[:300]
        elif line.startswith("Requires-Python:"):
            requires_python = line[16:].strip()[:100]
        elif line.startswith("Requires-Dist:"):
            nreq += 1
        elif line.strip() == "":
            break  # 头部结束
    return summary, requires_python, nreq


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"
