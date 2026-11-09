# -*- coding: utf-8 -*-
"""users 表的数据访问层。pwd_default=1 表示该账号仍在使用初始密码。"""
from __future__ import annotations

_FIELDS = "name, role, scope, created, pwd_default"


def get_credentials(conn, name: str):
    return conn.execute(
        "SELECT salt, pwhash, role, scope, pwd_default FROM users WHERE name=?", (name,)).fetchone()


def get_user(conn, name: str):
    return conn.execute(
        f"SELECT {_FIELDS} FROM users WHERE name=?", (name,)).fetchone()


def list_users(conn):
    return conn.execute(
        f"SELECT {_FIELDS} FROM users ORDER BY name COLLATE NOCASE").fetchall()


def list_names(conn) -> list[str]:
    return [r["name"] for r in conn.execute("SELECT name FROM users").fetchall()]


def count_users(conn) -> int:
    return conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]


def count_admins(conn) -> int:
    return conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin'").fetchone()["c"]


def upsert_user(conn, name: str, salt: str, pwhash: str, role: str, scope: str,
                created: str, pwd_default: int = 0):
    conn.execute(
        "INSERT INTO users(name, salt, pwhash, role, scope, created, pwd_default) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET salt=excluded.salt, pwhash=excluded.pwhash, "
        "role=excluded.role, scope=excluded.scope, pwd_default=excluded.pwd_default",
        (name, salt, pwhash, role, scope or "*", created, 1 if pwd_default else 0))


def update_meta(conn, name: str, role: str, scope: str):
    conn.execute("UPDATE users SET role=?, scope=? WHERE name=?",
                 (role, scope or "*", name))


def set_password(conn, name: str, salt: str, pwhash: str, pwd_default: int = 0):
    conn.execute("UPDATE users SET salt=?, pwhash=?, pwd_default=? WHERE name=?",
                 (salt, pwhash, 1 if pwd_default else 0, name))


def delete_user(conn, name: str):
    conn.execute("DELETE FROM users WHERE name=?", (name,))
