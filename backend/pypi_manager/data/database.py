# -*- coding: utf-8 -*-
"""SQLite 连接、建表、迁移与存储初始化。"""
from __future__ import annotations

import os
import sqlite3

from ..config import paths

FILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  filename      TEXT NOT NULL,
  project       TEXT NOT NULL,
  display_name  TEXT NOT NULL,
  version       TEXT NOT NULL,
  kind          TEXT NOT NULL DEFAULT 'wheel',
  py_tag        TEXT, abi_tag TEXT, plat_tag TEXT,
  sha256        TEXT NOT NULL,
  size          INTEGER NOT NULL,
  summary       TEXT DEFAULT '',
  requires_python TEXT DEFAULT '',
  dep_count     INTEGER DEFAULT 0,
  note          TEXT DEFAULT '',
  uploaded_at   TEXT NOT NULL,
  uploaded_by   TEXT NOT NULL,
  deleted       INTEGER NOT NULL DEFAULT 0,
  deleted_reason TEXT NOT NULL DEFAULT ''
);
"""

# filename 只在「未删除」的行之间唯一：同名文件被覆盖后旧行仍留在回收站里可追溯
FILES_INDEX = """
CREATE INDEX IF NOT EXISTS idx_files_project ON files(project, deleted);
CREATE UNIQUE INDEX IF NOT EXISTS idx_files_live_filename ON files(filename) WHERE deleted=0;
"""

SCHEMA = FILES_SCHEMA + FILES_INDEX + """
CREATE TABLE IF NOT EXISTS projects(
  name         TEXT PRIMARY KEY,
  description  TEXT DEFAULT '',
  updated_at   TEXT
);
CREATE TABLE IF NOT EXISTS users(
  name      TEXT PRIMARY KEY,
  salt      TEXT NOT NULL,
  pwhash    TEXT NOT NULL,
  role      TEXT NOT NULL DEFAULT 'admin',
  scope     TEXT NOT NULL DEFAULT '*',
  created   TEXT NOT NULL,
  pwd_default INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL DEFAULT '',
  updated_at TEXT,
  updated_by TEXT NOT NULL DEFAULT ''
);
"""


def db():
    conn = sqlite3.connect(paths()["db"], timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA journal_size_limit=67108864")
    conn.execute("PRAGMA wal_autocheckpoint=1000")
    return conn


_FILE_COLUMNS = (
    "id, filename, project, display_name, version, kind, py_tag, abi_tag, plat_tag, "
    "sha256, size, summary, requires_python, dep_count, note, uploaded_at, uploaded_by, "
    "deleted, deleted_reason"
)


def _single_column_unique(conn, table: str):
    """返回 table 上「只有一列」的唯一索引名（旧库的列级 UNIQUE 会生成这种索引）"""
    for idx in conn.execute("PRAGMA index_list(%s)" % table):
        if not idx["unique"] or idx["partial"]:
            continue
        cols = [r["name"] for r in conn.execute("PRAGMA index_info('%s')" % idx["name"])]
        if len(cols) == 1:
            return idx["name"], cols[0]
    return None, None


def _run_statements(conn, script: str):
    """逐条执行多语句脚本：executescript 会隐式提交，无法与建表同事务"""
    for stmt in script.split(";"):
        if stmt.strip():
            conn.execute(stmt)


def _rebuild_files(conn):
    """旧库的 filename 是列级 UNIQUE，覆盖上传只能物理删旧行。

    改成「未删除行唯一」的部分索引后，被覆盖的旧版本可以留在回收站里追溯／还原。
    SQLite 无法直接删除列级约束，只能重建表（事务内完成，失败整体回滚）。
    """
    conn.execute("ALTER TABLE files RENAME TO files_legacy")
    conn.execute(FILES_SCHEMA)
    old_cols = {r["name"] for r in conn.execute("PRAGMA table_info(files_legacy)")}
    select = _FILE_COLUMNS if "deleted_reason" in old_cols else \
        _FILE_COLUMNS.replace("deleted_reason", "''")
    conn.execute("INSERT INTO files(%s) SELECT %s FROM files_legacy" % (_FILE_COLUMNS, select))
    conn.execute("DROP TABLE files_legacy")
    _run_statements(conn, FILES_INDEX)


def _migrate(conn):
    """兼容旧库：files 唯一约束、deleted_reason，以及 users.scope / role / pwd_default"""
    name, column = _single_column_unique(conn, "files")
    if column == "filename":
        _rebuild_files(conn)
    elif "deleted_reason" not in {r["name"] for r in conn.execute("PRAGMA table_info(files)")}:
        conn.execute("ALTER TABLE files ADD COLUMN deleted_reason TEXT NOT NULL DEFAULT ''")

    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "scope" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN scope TEXT NOT NULL DEFAULT '*'")
    if "role" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'admin'")
    if "pwd_default" not in cols:
        # 旧库无法判断谁还在用初始密码，统一按「已自行设置」处理，避免误报
        conn.execute("ALTER TABLE users ADD COLUMN pwd_default INTEGER NOT NULL DEFAULT 0")
    # 业务上不再区分项目范围：所有账号都能使用全部包，历史账号统一放开
    conn.execute("UPDATE users SET scope='*' WHERE scope<>'*'")


def init_storage():
    p = paths()
    for k in ("root", "packages", "trash", "tmp", "orphans"):
        os.makedirs(p[k], exist_ok=True)
    conn = db()
    conn.executescript(SCHEMA)
    try:
        _migrate(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()


def ping():
    """健康检查用的最小连通性探测。"""
    conn = db()
    try:
        conn.execute("SELECT 1").fetchone()
    finally:
        conn.close()


def backup_to(dest_path: str):
    """使用 SQLite 在线备份 API 把当前数据库复制到 dest_path。"""
    src = db()
    dst = sqlite3.connect(dest_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def quick_check_file(path: str) -> str:
    """对指定数据库文件执行 PRAGMA quick_check。"""
    conn = sqlite3.connect(path)
    try:
        check = conn.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        conn.close()
    return check
