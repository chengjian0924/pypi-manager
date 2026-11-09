# -*- coding: utf-8 -*-
"""settings 表的键值存取：站点级可编辑配置（目前只有「客户端接入」文案）。"""
from __future__ import annotations


def get(conn, key: str):
    return conn.execute(
        "SELECT value, updated_at, updated_by FROM settings WHERE key=?", (key,)).fetchone()


def put(conn, key: str, value: str, updated_at: str, updated_by: str):
    conn.execute(
        "INSERT INTO settings(key, value, updated_at, updated_by) VALUES(?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
        "updated_at=excluded.updated_at, updated_by=excluded.updated_by",
        (key, value, updated_at, updated_by))


def delete(conn, key: str):
    conn.execute("DELETE FROM settings WHERE key=?", (key,))
