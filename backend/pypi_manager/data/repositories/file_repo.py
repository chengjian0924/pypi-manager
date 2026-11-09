# -*- coding: utf-8 -*-
"""files / projects 表的数据访问层：只做 SQL 读写，不含权限判断。"""
from __future__ import annotations


def get_by_id(conn, fid):
    return conn.execute("SELECT * FROM files WHERE id=?", (fid,)).fetchone()


def get_by_filename(conn, filename):
    """同名文件可能同时存在「在用」和「已进回收站」的行，这里只取在用的那条"""
    return conn.execute(
        "SELECT id, sha256, deleted, uploaded_by, project, version FROM files "
        "WHERE filename=? AND deleted=0", (filename,)).fetchone()


def get_sha256(conn, filename):
    return conn.execute(
        "SELECT sha256 FROM files WHERE filename=? AND deleted=0 ORDER BY id DESC LIMIT 1",
        (filename,)).fetchone()


def has_live_filename(conn, filename) -> bool:
    return conn.execute(
        "SELECT 1 FROM files WHERE filename=? AND deleted=0 LIMIT 1", (filename,)).fetchone() is not None


def list_packages(conn, mode: str, kw: str) -> list[dict]:
    sql = ("SELECT f.*, p.description AS pdesc FROM files f "
           "LEFT JOIN projects p ON p.name=f.project WHERE 1=1")
    args = []
    if mode == "1":
        sql += " AND f.deleted=1"
    elif mode != "all":
        sql += " AND f.deleted=0"
    if kw:
        sql += " AND (f.project LIKE ? OR f.filename LIKE ? OR f.note LIKE ?)"
        args += [f"%{kw}%"] * 3
    sql += " ORDER BY f.project, f.uploaded_at DESC"
    return [dict(r) for r in conn.execute(sql, args)]


def live_stats(conn):
    return conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(size),0) s, COUNT(DISTINCT project) p, "
        "COALESCE(SUM(CASE WHEN kind='wheel' THEN 1 ELSE 0 END),0) w, "
        "COALESCE(SUM(CASE WHEN kind<>'wheel' THEN 1 ELSE 0 END),0) d "
        "FROM files WHERE deleted=0").fetchone()


def trash_stats(conn):
    """回收站概览：条数、占用字节、最旧一条的上传时间"""
    return conn.execute(
        "SELECT COUNT(*) c, COALESCE(SUM(size),0) s, MIN(uploaded_at) oldest "
        "FROM files WHERE deleted=1").fetchone()


def trash_rows(conn):
    return [dict(r) for r in conn.execute(
        "SELECT id, filename, size FROM files WHERE deleted=1")]


def delete_row(conn, fid: int):
    conn.execute("DELETE FROM files WHERE id=?", (fid,))


def delete_deleted_by_filenames(conn, filenames):
    for fn in filenames:
        conn.execute("DELETE FROM files WHERE filename=? AND deleted=1", (fn,))


def export_rows(conn):
    return conn.execute(
        "SELECT filename, project, version, py_tag, abi_tag, plat_tag, size, sha256, "
        "uploaded_at, uploaded_by, note FROM files WHERE deleted=0 "
        "ORDER BY project, uploaded_at").fetchall()


def project_names(conn) -> list[str]:
    return [r["project"] for r in conn.execute(
        "SELECT DISTINCT project FROM files WHERE deleted=0 ORDER BY project")]


def project_files(conn, project: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM files WHERE project=? AND deleted=0 ORDER BY uploaded_at",
        (project,))]


def insert_file(conn, values: tuple):
    conn.execute(
        "INSERT INTO files(filename, project, display_name, version, kind, py_tag, abi_tag, "
        "plat_tag, sha256, size, summary, requires_python, dep_count, note, uploaded_at, "
        "uploaded_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        values)


def retire(conn, fid: int, reason: str = "delete"):
    """把文件标记为已删除（进回收站），reason 区分主动删除与被新版本覆盖"""
    conn.execute("UPDATE files SET deleted=1, deleted_reason=? WHERE id=?", (reason, fid))


def set_deleted(conn, fid: int, deleted: int):
    conn.execute("UPDATE files SET deleted=? WHERE id=?", (deleted, fid))


def set_note(conn, fid: int, note: str):
    conn.execute("UPDATE files SET note=? WHERE id=?", (note, fid))


def touch_project(conn, project: str, updated_at: str):
    conn.execute("INSERT INTO projects(name, updated_at) VALUES(?,?) "
                 "ON CONFLICT(name) DO UPDATE SET updated_at=excluded.updated_at",
                 (project, updated_at))


def upsert_project_description(conn, project: str, desc: str, updated_at: str):
    conn.execute("INSERT INTO projects(name, description, updated_at) VALUES(?,?,?) "
                 "ON CONFLICT(name) DO UPDATE SET description=excluded.description, "
                 "updated_at=excluded.updated_at", (project, desc, updated_at))


def live_id_filename(conn):
    return conn.execute("SELECT id, filename FROM files WHERE deleted=0").fetchall()


def all_filenames(conn) -> set[str]:
    return {r["filename"] for r in conn.execute("SELECT filename FROM files").fetchall()}


def count_files(conn) -> int:
    return conn.execute("SELECT COUNT(*) c FROM files").fetchone()["c"]


def quick_check(conn):
    return conn.execute("PRAGMA quick_check").fetchone()
