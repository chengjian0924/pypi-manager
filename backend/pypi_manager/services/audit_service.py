# -*- coding: utf-8 -*-
"""审计日志：追加写入、轮转、尾部读取。"""
from __future__ import annotations

import json
import os
import threading

from ..config import CFG, paths
from ..log import get_logger
from ..utils.naming import now_iso

AUDIT_LOCK = threading.Lock()
_log = get_logger("audit")


def tail_lines(path: str, limit: int) -> list[str]:
    """从文件尾部读取最多 limit 行，避免把整个日志读进内存"""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            block, data = 8192, b""
            while size > 0 and data.count(b"\n") <= limit:
                step = min(block, size)
                size -= step
                f.seek(size)
                data = f.read(step) + data
        return data.decode("utf-8", "replace").splitlines()[-limit:]
    except OSError:
        return []


def _rotate_audit():
    """审计日志超过上限时轮转，避免无限增长"""
    path = paths()["audit"]
    max_bytes = int(CFG.get("audit_max_mb") or 0) * 1024 * 1024
    keep = max(1, int(CFG.get("audit_keep") or 1))
    if max_bytes <= 0 or not os.path.isfile(path):
        return
    try:
        if os.path.getsize(path) < max_bytes:
            return
        for i in range(keep - 1, 0, -1):
            src, dst = f"{path}.{i}", f"{path}.{i + 1}"
            if os.path.exists(src):
                os.replace(src, dst)
        os.replace(path, f"{path}.1")
        for i in range(keep + 1, keep + 20):
            if os.path.exists(f"{path}.{i}"):
                os.unlink(f"{path}.{i}")
    except OSError as e:
        _log.error("审计日志轮转失败：%s", e)


def audit(user: str, action: str, target: str, extra: dict | None = None, ip: str = ""):
    rec = {"ts": now_iso(), "user": user, "action": action, "target": target, "ip": ip}
    if extra:
        rec.update(extra)
    try:
        with AUDIT_LOCK:
            _rotate_audit()
            with open(paths()["audit"], "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        # 审计失败不能静默：写 error 级日志，便于采集器告警
        _log.error("审计写入失败 action=%s target=%s：%s", action, target, e)


def read_recent(limit: int) -> list[dict]:
    """按轮转文件从新到旧读取最多 limit 条审计记录。"""
    out: list[dict] = []
    keep = max(1, int(CFG.get("audit_keep") or 1))
    base = paths()["audit"]
    for i in range(0, keep + 1):
        if len(out) >= limit:
            break
        fp = base if i == 0 else f"{base}.{i}"
        for ln in reversed(tail_lines(fp, limit - len(out))):
            try:
                out.append(json.loads(ln))
            except Exception:
                continue
            if len(out) >= limit:
                break
    return out
