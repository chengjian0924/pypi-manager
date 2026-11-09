# -*- coding: utf-8 -*-
"""存储生命周期：启动对账、回收站清理、在线备份与离线恢复。"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime

from ..config import CFG, paths
from ..constants import STOP, VERSION
from ..data import database
from ..data.repositories import file_repo
from ..log import get_logger
from ..utils.naming import now_iso
from .audit_service import audit

_log = get_logger("storage")


_TRASH_PREFIX = re.compile(r"^\d{14}__")


def _trash_entries() -> list[tuple[str, float, int]]:
    """回收站物理文件 [(路径, mtime, 大小)]，按时间从旧到新"""
    entries = []
    try:
        names = os.listdir(paths()["trash"])
    except FileNotFoundError:
        return entries
    for name in names:
        fp = os.path.join(paths()["trash"], name)
        try:
            if os.path.isfile(fp):
                st = os.stat(fp)
                entries.append((fp, st.st_mtime, st.st_size))
        except OSError:
            pass
    entries.sort(key=lambda e: e[1])
    return entries


def cleanup_trash() -> dict:
    """清理回收站：先按保留期、再按容量上限，从最旧的开始删。

    物理副本删除后同步清掉对应的已删除记录，避免留下永远恢复不了的死行。
    """
    days = int(CFG.get("trash_days") or 0)
    max_bytes = int(CFG.get("trash_max_mb") or 0) * 1024 * 1024
    report = {"files": 0, "rows": 0, "freed": 0}
    if days <= 0 and max_bytes <= 0:
        return report

    entries = _trash_entries()
    cutoff = time.time() - days * 86400 if days > 0 else None
    drop, kept = [], []
    for item in entries:
        if cutoff is not None and item[1] < cutoff:
            drop.append(item)
        else:
            kept.append(item)

    if max_bytes > 0 and sum(e[2] for e in kept) > max_bytes:
        total = sum(e[2] for e in kept)
        while kept and total > max_bytes:
            item = kept.pop(0)
            drop.append(item)
            total -= item[2]

    if not drop:
        return report

    removed = []
    for fp, _, size in drop:
        try:
            os.unlink(fp)
            removed.append(os.path.basename(fp))
            report["freed"] += size
        except OSError:
            pass
    report["files"] = len(removed)

    alive = {_TRASH_PREFIX.sub("", os.path.basename(e[0])) for e in kept}
    gone = {_TRASH_PREFIX.sub("", n) for n in removed} - alive
    if gone:
        conn = database.db()
        try:
            cur = conn.executemany("DELETE FROM files WHERE filename=? AND deleted=1",
                                   [(fn,) for fn in gone])
            report["rows"] = max(0, cur.rowcount or 0)
            conn.commit()
        finally:
            conn.close()
    return report


def reconcile_storage() -> dict:
    """启动对账：清理残留临时文件，标记丢失文件，隔离孤儿文件，检查数据库完整性"""
    p = paths()
    report = {"tmp_removed": 0, "missing_marked": 0, "orphans_moved": 0,
              "trash_removed": 0, "db_check": "ok"}
    cutoff = time.time() - 3600
    try:
        for fn in os.listdir(p["tmp"]):
            fp = os.path.join(p["tmp"], fn)
            try:
                if fn.startswith("up-") and os.path.getmtime(fp) < cutoff:
                    os.unlink(fp)
                    report["tmp_removed"] += 1
            except OSError:
                pass
    except FileNotFoundError:
        pass

    conn = database.db()
    known: set[str] = set()
    try:
        check = file_repo.quick_check(conn)
        report["db_check"] = check[0] if check else "unknown"
        conn.execute("BEGIN IMMEDIATE")
        missing = [r["id"] for r in file_repo.live_id_filename(conn)
                   if not os.path.isfile(os.path.join(p["packages"], r["filename"]))]
        for fid in missing:
            file_repo.set_deleted(conn, fid, 1)
        known = file_repo.all_filenames(conn)
        conn.commit()
        report["missing_marked"] = len(missing)
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()

    for fn in os.listdir(p["packages"]):
        if fn.startswith(".") or fn in known:
            continue
        fp = os.path.join(p["packages"], fn)
        if os.path.isfile(fp):
            shutil.move(fp, os.path.join(p["orphans"], fn))
            report["orphans_moved"] += 1

    trash = cleanup_trash()
    report["trash_removed"] = trash["files"]

    if (report["missing_marked"] or report["orphans_moved"] or report["trash_removed"]
            or report["db_check"] != "ok"):
        audit("system", "reconcile", "", report)
    return report


def _link_or_copy(src: str, dst: str):
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


# ---------- 备份盘点与完整性 ----------

_ROLLBACK_PREFIX = ".rollback-"
# 恢复/归档只碰这些条目，避免误删数据目录里的其他文件
_ARCHIVE_NAMES = ("pypi.db", "pypi.db-wal", "pypi.db-shm", "packages",
                  ".trash", ".tmp", ".orphans", "audit.jsonl")


def _files_in(folder: str) -> set[str]:
    if not os.path.isdir(folder):
        return set()
    return {fn for fn in os.listdir(folder) if os.path.isfile(os.path.join(folder, fn))}


def _tree_size(root: str) -> int:
    total = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return total


def _read_manifest(src: str) -> dict:
    try:
        with open(os.path.join(src, "manifest.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _db_live_files(db_path: str) -> set[str]:
    """备份库里"在用"包的文件名集合（老库没有 deleted 列时退化为全量）"""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        try:
            rows = conn.execute("SELECT filename FROM files WHERE deleted=0").fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute("SELECT filename FROM files").fetchall()
        return {r[0] for r in rows if r[0]}
    finally:
        conn.close()


def inspect_backup(src: str) -> dict:
    """只读盘点一份备份：时间、大小、包文件数、库校验、缺失文件。"""
    src = os.path.abspath(src)
    info = {
        "path": src,
        "name": os.path.basename(src.rstrip(os.sep)),
        "created": "",
        "files_expected": None,
        "files_present": 0,
        "trash_files": 0,
        "size": 0,
        "db_ok": False,
        "db_check": "missing",
        "missing": [],
        "ok": False,
    }
    if not os.path.isdir(src):
        info["db_check"] = "not-a-dir"
        return info

    manifest = _read_manifest(src)
    info["created"] = str(manifest.get("created") or "") or datetime.fromtimestamp(
        os.path.getmtime(src)).strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(manifest.get("files"), int):
        info["files_expected"] = manifest["files"]
    info["size"] = _tree_size(src)

    sdb = os.path.join(src, "pypi.db")
    if os.path.isfile(sdb):
        try:
            info["db_check"] = database.quick_check_file(sdb)
        except Exception as e:      # 损坏到连检查都跑不起来，同样算不通过
            info["db_check"] = f"error: {e}"
        info["db_ok"] = info["db_check"] == "ok"

    present = _files_in(os.path.join(src, "packages"))
    info["files_present"] = len(present)
    info["trash_files"] = len(_files_in(os.path.join(src, "trash")))
    if info["db_ok"]:
        try:
            live = _db_live_files(sdb)
        except sqlite3.Error as e:
            info["db_check"] = f"error: {e}"
            info["db_ok"] = False
            live = set()
        info["missing"] = sorted(live - present)

    info["ok"] = bool(info["db_ok"]) and not info["missing"]
    return info


def list_backups() -> list[dict]:
    """备份目录下的备份点，按名称（即时间）倒序。"""
    root = CFG.get("backup_dir") or ""
    if not root or not os.path.isdir(root):
        return []
    names = sorted(
        (n for n in os.listdir(root)
         if n.startswith("backup-") and os.path.isdir(os.path.join(root, n))),
        reverse=True)
    return [inspect_backup(os.path.join(root, n)) for n in names]



def backup_now() -> str:
    """在线备份：SQLite backup API + 包文件硬链接（跨盘自动退化为复制）"""
    root = CFG.get("backup_dir") or ""
    if not root:
        raise RuntimeError("未配置备份目录（--backup-dir）")
    os.makedirs(root, exist_ok=True)
    p = paths()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(root, f"backup-{stamp}")
    if os.path.exists(dest):
        dest += "-" + os.urandom(2).hex()
    os.makedirs(dest)

    database.backup_to(os.path.join(dest, "pypi.db"))

    if os.path.isfile(p["audit"]):
        shutil.copy2(p["audit"], os.path.join(dest, "audit.jsonl"))
    for folder in (p["packages"], p["trash"]):
        target = os.path.join(dest, os.path.basename(folder).lstrip(".") or "trash")
        os.makedirs(target, exist_ok=True)
        for fn in os.listdir(folder):
            sp = os.path.join(folder, fn)
            if os.path.isfile(sp):
                _link_or_copy(sp, os.path.join(target, fn))

    conn = database.db()
    total = file_repo.count_files(conn)
    conn.close()
    with open(os.path.join(dest, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump({"created": now_iso(), "version": VERSION, "files": total},
                  f, ensure_ascii=False)

    keep = max(1, int(CFG["backup_keep"]))
    existing = sorted(d for d in os.listdir(root) if d.startswith("backup-"))
    for old in existing[:-keep]:
        shutil.rmtree(os.path.join(root, old), ignore_errors=True)
    return os.path.abspath(dest)


def _archive_current(root: str) -> str | None:
    """把当前库与包文件移进 <data>/.rollback-<时间戳>，返回归档目录。

    只 move 数据条目、不 rename 数据目录本身：VM 上数据目录常是独立挂载点、
    Docker 里是卷挂载点，对挂载点做 rename 会 EBUSY。
    """
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    arc = os.path.join(root, _ROLLBACK_PREFIX + stamp)
    if os.path.exists(arc):
        arc += "-" + os.urandom(2).hex()
    os.makedirs(arc, exist_ok=True)
    moved = False
    for name in _ARCHIVE_NAMES:
        src = os.path.join(root, name)
        if os.path.lexists(src):
            shutil.move(src, os.path.join(arc, name))
            moved = True
    if not moved:
        os.rmdir(arc)
        return None
    return arc


def _discard_partial(root: str):
    """清掉刚恢复了一半的内容，只动已知的数据条目。"""
    for name in _ARCHIVE_NAMES:
        path = os.path.join(root, name)
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.lexists(path):
            try:
                os.unlink(path)
            except OSError:
                pass


def _restore_archive(root: str, arc: str):
    """恢复失败时把归档内容放回原位。"""
    _discard_partial(root)
    for name in os.listdir(arc):
        try:
            shutil.move(os.path.join(arc, name), os.path.join(root, name))
        except OSError as e:
            _log.error("回滚归档文件失败 %s：%s", name, e)
    try:
        os.rmdir(arc)
    except OSError:
        pass


def restore_backup(src: str, force: bool = False) -> dict:
    """从备份目录恢复到 CFG['data']（离线操作，调用前须停服）。

    force=False：目标目录已有数据库、或备份缺包文件时拒绝，避免静默丢数据。
    force=True ：先把现有数据归档到 <data>/.rollback-<ts> 再恢复；恢复中途失败会自动回滚。
    """
    src = os.path.abspath(src)
    sdb = os.path.join(src, "pypi.db")
    if not os.path.isfile(sdb):
        raise RuntimeError(f"备份目录缺少 pypi.db: {src}")
    check = database.quick_check_file(sdb)
    if check != "ok":
        raise RuntimeError(f"备份数据库完整性校验失败: {check}")

    # 关键：库能打开不代表包文件齐全。以库里的"在用"记录为准逐个核对。
    try:
        live = _db_live_files(sdb)
    except sqlite3.Error as e:
        raise RuntimeError(f"无法读取备份数据库: {e}") from e
    present = _files_in(os.path.join(src, "packages"))
    missing = sorted(live - present)
    if missing and not force:
        shown = "、".join(missing[:5]) + ("…" if len(missing) > 5 else "")
        raise RuntimeError(
            f"备份不完整：数据库引用的 {len(missing)} 个包文件不在备份里（{shown}）。"
            "确认要恢复残缺数据请加 --force")

    p = paths()
    root = p["root"]
    archived = None
    if os.path.exists(p["db"]):
        if not force:
            raise RuntimeError(
                f"目标数据目录已存在数据库，请先清空或换一个 --data 目录: {root}")
        if os.path.abspath(root) in ("/", os.path.abspath(os.path.expanduser("~"))):
            raise RuntimeError(f"拒绝在这个数据目录上执行归档恢复: {root}")
        archived = _archive_current(root)
        _log.warning("恢复前已归档现有数据：%s", archived)

    copied = 0
    try:
        for key in ("root", "packages", "trash", "tmp", "orphans"):
            os.makedirs(p[key], exist_ok=True)
        shutil.copy2(sdb, p["db"])
        for name, dst in (("packages", p["packages"]), ("trash", p["trash"])):
            s = os.path.join(src, name)
            if os.path.isdir(s):
                for fn in os.listdir(s):
                    sp = os.path.join(s, fn)
                    if os.path.isfile(sp):
                        shutil.copy2(sp, os.path.join(dst, fn))
                        copied += 1
        audit_src = os.path.join(src, "audit.jsonl")
        if os.path.isfile(audit_src):
            shutil.copy2(audit_src, p["audit"])
    except Exception:
        if archived:
            _log.error("恢复失败，正在回滚到归档数据：%s", archived)
            _restore_archive(root, archived)
        else:
            _discard_partial(root)
        raise

    return {"root": root, "archived": archived, "copied": copied,
            "missing": missing, "forced": bool(force and missing)}



def start_backup_loop():
    if not CFG.get("backup_dir") or int(CFG.get("backup_interval_hours") or 0) <= 0:
        return None

    def loop():
        while not STOP.is_set():
            try:
                dest = backup_now()
                audit("system", "backup", os.path.basename(dest), {})
                _log.info("已自动备份到 %s", dest)
            except Exception:
                _log.exception("自动备份失败")
            STOP.wait(int(CFG["backup_interval_hours"]) * 3600)

    t = threading.Thread(target=loop, name="backup", daemon=True)
    t.start()
    return t


def start_trash_loop():
    """后台每天清理一次回收站，避免长期运行后磁盘被历史删除文件占满"""
    if int(CFG.get("trash_days") or 0) <= 0 and int(CFG.get("trash_max_mb") or 0) <= 0:
        return None

    def loop():
        while not STOP.is_set():
            try:
                n = cleanup_trash()
                if n["files"]:
                    _log.info("已清理回收站文件 %s 个（释放 %s 字节）", n["files"], n["freed"])
            except Exception:
                _log.exception("回收站清理失败")
            STOP.wait(86400)

    t = threading.Thread(target=loop, name="trash", daemon=True)
    t.start()
    return t


def health_snapshot() -> dict:
    """健康检查：数据库连通性 + 磁盘余量。"""
    status = "ok"
    checks = {}
    try:
        database.ping()
        checks["db"] = "ok"
    except Exception:
        checks["db"] = "error"
        status = "error"
    try:
        free_mb = shutil.disk_usage(paths()["root"]).free // (1024 * 1024)
        checks["disk_free_mb"] = free_mb
        if free_mb < int(CFG["min_free_mb"]):
            checks["disk"] = "low"
            if status == "ok":
                status = "degraded"
        else:
            checks["disk"] = "ok"
    except Exception:
        checks["disk"] = "error"
        status = "error"
    return {"status": status, "checks": checks}
