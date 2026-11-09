# -*- coding: utf-8 -*-
"""包管理业务：列表、上传入库、删除/恢复/备注、导出、PEP 503 数据。"""
from __future__ import annotations

import os
import shutil
import sqlite3
import zipfile
from datetime import datetime

from ..config import CFG, paths
from ..constants import WRITE_LOCK
from ..data.database import db
from ..data.repositories import file_repo
from ..errors import ServiceError
from ..utils.naming import (human_size, norm, now_iso, parse_artifact,
                            read_wheel_meta, safe_name, sanitize_filename,
                            sha256_file)
from . import auth_service
from .audit_service import audit


def _stats_from_rows(live: list[dict]) -> dict:
    return {"c": len(live), "s": sum(r["size"] for r in live),
            "p": len({r["project"] for r in live}),
            "w": sum(1 for r in live if r["kind"] == "wheel"),
            "d": sum(1 for r in live if r["kind"] != "wheel")}


def trash_overview(conn) -> dict:
    """回收站概览：条数 / 占用 / 最旧一条的剩余保留天数"""
    row = file_repo.trash_stats(conn)
    days = int(CFG.get("trash_days") or 0)
    remain = None
    if days > 0 and row["oldest"]:
        try:
            age = (datetime.now().astimezone()
                   - datetime.fromisoformat(row["oldest"])).days
            remain = max(0, days - age)
        except (TypeError, ValueError):
            remain = None
    return {"count": row["c"], "size": row["s"], "size_h": human_size(row["s"]),
            "keep_days": days, "oldest_left_days": remain}


def list_packages(user: dict, kw: str, mode: str) -> dict:
    """管理界面包列表：按 scope 过滤并汇总统计。"""
    conn = db()
    try:
        rows = file_repo.list_packages(conn, mode, kw)
        # 项目范围隔离：非管理员只能看到 scope 内的项目
        if not auth_service.is_admin(user):
            rows = [r for r in rows if auth_service.in_scope(user, r["project"])]
            stats = _stats_from_rows([r for r in rows if not r["deleted"]])
        else:
            stats = file_repo.live_stats(conn)
        trash = trash_overview(conn)
    finally:
        conn.close()

    projects = {}
    for r in rows:
        projects.setdefault(r["project"], {
            "name": r["project"], "display": r["display_name"],
            "description": r["pdesc"] or "", "files": [],
        })["files"].append({
            "id": r["id"], "filename": r["filename"], "version": r["version"],
            "kind": r["kind"], "py_tag": r["py_tag"], "abi_tag": r["abi_tag"],
            "plat_tag": r["plat_tag"], "size": r["size"], "size_h": human_size(r["size"]),
            "sha256": r["sha256"], "summary": r["summary"],
            "requires_python": r["requires_python"], "dep_count": r["dep_count"],
            "note": r["note"], "uploaded_at": r["uploaded_at"],
            "uploaded_by": r["uploaded_by"], "deleted": bool(r["deleted"]),
            "deleted_reason": r["deleted_reason"] or "",
        })
    return {
        "ok": True,
        "stats": {"files": stats["c"], "size": stats["s"], "size_h": human_size(stats["s"]),
                  "projects": stats["p"], "wheel": stats["w"], "sdist": stats["d"],
                  "trash": trash},
        "projects": sorted(projects.values(), key=lambda x: x["name"]),
    }


def export_rows():
    """导出 CSV 所需的未删除文件行。"""
    conn = db()
    try:
        return file_repo.export_rows(conn)
    finally:
        conn.close()


def simple_projects() -> list[str]:
    conn = db()
    try:
        return file_repo.project_names(conn)
    finally:
        conn.close()


def simple_project_files(project: str) -> list[dict]:
    conn = db()
    try:
        return file_repo.project_files(conn, project)
    finally:
        conn.close()


def lookup_file(filename: str):
    """定位包文件并返回物理路径与 sha256；非法/不存在返回错误标记。"""
    if "/" in filename or "\\" in filename or filename.startswith("."):
        return {"error": "invalid"}
    base = paths()["packages"]
    path = os.path.join(base, filename)
    if not os.path.isfile(path):
        # 兼容历史上传时文件名被 safe_name 转换过的文件
        path = os.path.join(base, safe_name(filename))
    if not os.path.isfile(path) or os.path.dirname(os.path.abspath(path)) != os.path.abspath(base):
        return {"error": "missing"}
    conn = db()
    try:
        row = file_repo.get_sha256(conn, filename)
    finally:
        conn.close()
    return {"path": path,
            "sha256": row["sha256"] if row else None,
            "name": os.path.basename(path)}


def _deny_forbidden(user: dict, path: str, ip: str, msg: str):
    audit(user["name"], "forbidden", path, {"role": user["role"]}, ip)
    return 403, {"ok": False, "error": msg}


def store(filename: str, tmp_path: str, user: dict, note: str = "", ip: str = ""):
    """把临时文件登记入库，返回 (status, payload)"""
    if not auth_service.can_upload(user):
        return 403, {"ok": False, "error": "当前账号没有上传权限"}
    filename = sanitize_filename(filename)
    if not filename:
        return 400, {"ok": False, "error": "文件名不合法"}
    info = parse_artifact(filename)
    if not info:
        return 400, {"ok": False, "error": "只接受 .whl / .tar.gz / .zip 包文件"}
    if info["kind"] == "wheel":
        if not zipfile.is_zipfile(tmp_path):
            return 400, {"ok": False, "error": "不是合法的 wheel（zip 结构损坏）"}
    size = os.path.getsize(tmp_path)
    digest = sha256_file(tmp_path)
    proj = norm(info["name"])
    if not auth_service.in_scope(user, proj):
        return 403, {"ok": False, "error": f"账号无权向项目 {proj} 上传包"}

    summary, requires_python, nreq = ("", "", 0)
    if info["kind"] == "wheel":
        summary, requires_python, nreq = read_wheel_meta(tmp_path)

    with WRITE_LOCK:
        conn = db()
        try:
            conn.execute("BEGIN IMMEDIATE")
            old = file_repo.get_by_filename(conn, filename)
            if old and not old["deleted"] and old["sha256"] == digest:
                conn.rollback()
                conn.close()
                os.unlink(tmp_path)
                return 200, {"ok": True, "skipped": True,
                             "message": "同名同内容已存在，未重复入库", "filename": filename,
                             "project": proj, "version": info["version"]}
            if old and not auth_service.is_admin(user) and old["uploaded_by"] != user["name"]:
                conn.rollback()
                conn.close()
                return 403, {"ok": False,
                             "error": "同名包已由其他账号上传，只有管理员可以覆盖"}
            dst = os.path.join(paths()["packages"], filename)
            if old:
                # 覆盖：旧文件连同数据库行一起进回收站，保留可追溯、可还原
                if os.path.exists(dst):
                    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
                    shutil.move(dst, os.path.join(paths()["trash"], f"{stamp}__{filename}"))
                file_repo.retire(conn, old["id"], "replace")
            shutil.move(tmp_path, dst)
            os.chmod(dst, 0o644)
            file_repo.insert_file(conn, (
                filename, proj, info["name"], info["version"], info["kind"],
                info["py"], info["abi"], info["plat"], digest, size,
                summary, requires_python, nreq, note, now_iso(), user["name"]))
            file_repo.touch_project(conn, proj, now_iso())
            conn.commit()
        except sqlite3.OperationalError as e:
            conn.rollback()
            conn.close()
            if "disk" in str(e).lower() or "space" in str(e).lower():
                raise ServiceError(
                    507, "服务器存储空间不足：数据库所在磁盘已写满，请联系管理员清理空间后重试")
            raise
        except Exception:
            conn.rollback()
            conn.close()
            raise
        conn.close()
    audit(user["name"], "replace" if old else "upload", filename,
          {"project": proj, "version": info["version"], "sha256": digest, "size": size,
           "note": note}, ip)
    return 200, {"ok": True, "filename": filename, "project": proj,
                 "version": info["version"], "sha256": digest, "replaced": bool(old)}


def delete_file(user: dict, fid, path: str, ip: str):
    conn = db()
    row = file_repo.get_by_id(conn, fid)
    if not row or row["deleted"]:
        conn.close()
        return 404, {"ok": False, "error": "记录不存在"}
    if not auth_service.can_manage_file(user, row):
        conn.close()
        return _deny_forbidden(user, path, ip, "只能删除自己上传且在自己的项目范围内的包")
    src = os.path.join(paths()["packages"], row["filename"])
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    with WRITE_LOCK:
        try:
            conn.execute("BEGIN IMMEDIATE")
            if os.path.exists(src):
                shutil.move(src, os.path.join(paths()["trash"], f"{stamp}__{row['filename']}"))
            file_repo.retire(conn, fid, "delete")
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            raise
        conn.close()
    audit(user["name"], "delete", row["filename"],
          {"project": row["project"], "version": row["version"]}, ip)
    return 200, {"ok": True, "message": "已移入回收站，可恢复"}


def restore_file(user: dict, fid, path: str, ip: str):
    conn = db()
    row = file_repo.get_by_id(conn, fid)
    if not row or not row["deleted"]:
        conn.close()
        return 404, {"ok": False, "error": "记录不存在或未删除"}
    if not auth_service.can_manage_file(user, row):
        conn.close()
        return _deny_forbidden(user, path, ip, "只能恢复自己上传且在自己的项目范围内的包")
    target = os.path.join(paths()["packages"], row["filename"])
    with WRITE_LOCK:
        try:
            conn.execute("BEGIN IMMEDIATE")
            # 删除后可能又上传了同名文件（此时旧行是被覆盖的历史版本），恢复会顶掉现役文件
            if file_repo.has_live_filename(conn, row["filename"]):
                conn.rollback()
                conn.close()
                return 409, {"ok": False, "error": "已有同名文件在使用中，请先删除它再恢复这一版"}
            cand = sorted(
                (f for f in os.listdir(paths()["trash"])
                 if f.endswith("__" + row["filename"])),
                reverse=True)
            if cand:
                shutil.move(os.path.join(paths()["trash"], cand[0]), target)
            elif not os.path.exists(target):
                conn.rollback()
                conn.close()
                return 410, {"ok": False, "error": "回收站里已找不到文件（可能已被清理）"}
            file_repo.set_deleted(conn, fid, 0)
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            raise
        conn.close()
    audit(user["name"], "restore", row["filename"], {}, ip)
    return 200, {"ok": True, "message": "已恢复"}


def _trash_copies(filename: str) -> list[str]:
    """回收站里该文件名的所有物理副本（形如 20260101120000__pkg-1.0.whl）"""
    try:
        names = os.listdir(paths()["trash"])
    except FileNotFoundError:
        return []
    return [os.path.join(paths()["trash"], n) for n in names if n.endswith("__" + filename)]


def purge_file(user: dict, fid, path: str, ip: str):
    """管理员彻底删除：物理文件与数据库记录一起清除，之后无法恢复"""
    if not auth_service.is_admin(user):
        return _deny_forbidden(user, path, ip, "只有管理员可以彻底删除文件")
    conn = db()
    row = file_repo.get_by_id(conn, fid)
    if not row:
        conn.close()
        return 404, {"ok": False, "error": "记录不存在"}
    if not row["deleted"]:
        conn.close()
        return 409, {"ok": False, "error": "请先删除该文件，再从回收站彻底删除"}
    removed = 0
    with WRITE_LOCK:
        try:
            conn.execute("BEGIN IMMEDIATE")
            for fp in _trash_copies(row["filename"]):
                try:
                    os.unlink(fp)
                    removed += 1
                except OSError:
                    pass
            # 同名历史版本已无文件可还原，一并清掉，避免残留永远恢复不了的行
            file_repo.delete_deleted_by_filenames(conn, [row["filename"]])
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            raise
        conn.close()
    audit(user["name"], "purge", row["filename"],
          {"project": row["project"], "size": row["size"], "copies": removed}, ip)
    return 200, {"ok": True, "message": "已彻底删除"}


def empty_trash(user: dict, path: str, ip: str):
    """管理员清空回收站：物理文件与已删除记录全部清除"""
    if not auth_service.is_admin(user):
        return _deny_forbidden(user, path, ip, "只有管理员可以清空回收站")
    conn = db()
    rows = file_repo.trash_rows(conn)
    total = sum(r["size"] for r in rows)
    removed = 0
    with WRITE_LOCK:
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                names = os.listdir(paths()["trash"])
            except FileNotFoundError:
                names = []
            for name in names:
                fp = os.path.join(paths()["trash"], name)
                try:
                    if os.path.isfile(fp):
                        os.unlink(fp)
                        removed += 1
                except OSError:
                    pass
            conn.execute("DELETE FROM files WHERE deleted=1")
            conn.commit()
        except Exception:
            conn.rollback()
            conn.close()
            raise
        conn.close()
    audit(user["name"], "trash_empty", "", {"files": len(rows), "size": total,
                                            "copies": removed}, ip)
    return 200, {"ok": True, "files": len(rows), "size_h": human_size(total),
                 "message": "回收站已清空"}


def update_file_note(user: dict, fid, data: dict, path: str, ip: str):
    conn = db()
    row = file_repo.get_by_id(conn, fid)
    if not row:
        conn.close()
        return 404, {"ok": False, "error": "记录不存在"}
    if not auth_service.can_manage_file(user, row):
        conn.close()
        return _deny_forbidden(user, path, ip, "只能修改自己上传且在自己的项目范围内的包")
    if "note" in data:
        with WRITE_LOCK:
            file_repo.set_note(conn, fid, str(data["note"])[:500])
            conn.commit()
        audit(user["name"], "edit_note", row["filename"],
              {"note": str(data["note"])[:500]}, ip)
    conn.close()
    return 200, {"ok": True}


def update_project(project: str, desc: str, path: str, user: dict, ip: str):
    conn = db()
    with WRITE_LOCK:
        file_repo.upsert_project_description(conn, project, desc, now_iso())
        conn.commit()
    conn.close()
    audit(user["name"], "edit_project", project, {"description": desc}, ip)
    return 200, {"ok": True}
