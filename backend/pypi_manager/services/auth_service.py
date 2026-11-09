# -*- coding: utf-8 -*-
"""认证、RBAC、scope、登录限流与认证缓存。"""
from __future__ import annotations

import base64
import fnmatch
import hashlib
import hmac
import os
import re
import threading
import time

from ..constants import (BATCH_MAX_USERS, DEFAULT_PASSWORD, LOGIN_BLOCK_SECONDS,
                         LOGIN_MAX_FAILS, LOGIN_WINDOW, MAX_PASSWORD_LEN,
                         MIN_PASSWORD_LEN, ROLES)
from ..data.database import db
from ..data.repositories import user_repo
from ..log import get_logger, set_user
from ..utils.naming import now_iso

_log = get_logger("auth")

# 认证缓存：避免每个请求重复执行 20 万轮 PBKDF2（实测单次约 16ms，是并发瓶颈）
_AUTH_CACHE: dict[str, tuple[float, dict]] = {}
_AUTH_CACHE_LOCK = threading.Lock()
_AUTH_CACHE_TTL = 120

# 登录失败限流：同一 IP + 账号在窗口内失败过多则临时封禁
_LOGIN_FAILS: dict[tuple[str, str], list[float]] = {}
_LOGIN_LOCK = threading.Lock()


def hash_password(password: str, salt: bytes | None = None):
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return salt.hex(), dk.hex()


def check_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(dk.hex(), hash_hex)


def _scope_list(scope: str) -> list[str]:
    return [s.strip() for s in (scope or "").split(",") if s.strip()]


def is_admin(user: dict | None) -> bool:
    return bool(user) and user.get("role") == "admin"


def can_upload(user: dict | None) -> bool:
    return bool(user) and user.get("role") in ("admin", "uploader")


def in_scope(user: dict | None, project: str) -> bool:
    """项目范围隔离：admin 拥有全部；其他角色按 scope 中的通配符匹配"""
    if is_admin(user):
        return True
    if not user:
        return False
    return any(fnmatch.fnmatchcase(project, pat) for pat in user.get("scope") or [])


def can_manage_file(user: dict | None, row) -> bool:
    """删除 / 恢复 / 改备注：admin 全部；uploader 仅限自己上传且在其 scope 内"""
    if is_admin(user):
        return True
    if not user or user.get("role") != "uploader":
        return False
    return row["uploaded_by"] == user["name"] and in_scope(user, row["project"])


def _invalidate_auth_cache():
    """账号/密码/角色变更后立刻失效缓存，避免旧凭据在 TTL 内继续可用"""
    with _AUTH_CACHE_LOCK:
        _AUTH_CACHE.clear()


def normalize_name(name) -> str:
    """账号名校验：便于 Basic 认证与命令行传参"""
    n = str(name or "").strip()
    if not n:
        raise ValueError("账号不能为空")
    if len(n) < 2 or len(n) > 32:
        raise ValueError("账号长度需在 2-32 个字符之间")
    if ":" in n or any(ch.isspace() for ch in n) or any(ord(ch) < 32 for ch in n):
        raise ValueError("账号不能包含冒号、空白或控制字符")
    return n


def normalize_role(role) -> str:
    r = str(role or "").strip().lower()
    if r not in ROLES:
        raise ValueError(f"角色必须是 {', '.join(sorted(ROLES))} 之一")
    return r


def normalize_scope(scope) -> str:
    """项目范围：逗号分隔的通配符列表；空值归一为 *"""
    if isinstance(scope, (list, tuple)):
        parts = [str(s).strip() for s in scope]
    else:
        parts = [s.strip() for s in str(scope or "").split(",")]
    parts = [p for p in parts if p]
    if not parts:
        return "*"
    if any(any(ord(ch) < 32 for ch in p) for p in parts):
        raise ValueError("项目范围包含非法字符")
    # 去重但保持顺序
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return ",".join(out)


def check_password_strength(password: str):
    if not password:
        raise ValueError("密码不能为空")
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LEN} 位")
    if len(password) > MAX_PASSWORD_LEN:
        raise ValueError(f"密码不能超过 {MAX_PASSWORD_LEN} 位")


def _pwd_flag(password: str) -> int:
    """标记账号是否仍在用初始密码：用户自行改成别的密码后置 0"""
    return 1 if password == DEFAULT_PASSWORD else 0


def create_user(name: str, password: str = "", role: str = "admin", scope: str = "*"):
    """命令行走廊：创建账号或重置已有账号（同名即覆盖），返回 created/updated。

    password 留空时使用初始密码（DEFAULT_PASSWORD）。
    """
    name = normalize_name(name)
    role = normalize_role(role)
    scope = normalize_scope(scope)
    password = password or DEFAULT_PASSWORD
    check_password_strength(password)
    salt, h = hash_password(password)
    conn = db()
    try:
        existed = user_repo.get_user(conn, name) is not None
        user_repo.upsert_user(conn, name, salt, h, role, scope, now_iso(),
                              _pwd_flag(password))
        conn.commit()
    finally:
        conn.close()
    _invalidate_auth_cache()
    return "updated" if existed else "created"


def get_user(name: str):
    conn = db()
    try:
        row = user_repo.get_user(conn, normalize_name(name))
    finally:
        conn.close()
    return dict(row) if row else None


def list_users() -> list[dict]:
    conn = db()
    try:
        rows = user_repo.list_users(conn)
    finally:
        conn.close()
    return [dict(r) for r in rows]


def save_account(payload: dict) -> tuple[str, dict]:
    """HTTP 建号 / 改角色 / 重置密码的统一入口。

    payload: {name, role?, scope?, password?, reset_password?}
    - 新账号不传 password 时使用初始密码 DEFAULT_PASSWORD。
    - 已有账号不传 password 则只更新角色与范围。
    - reset_password=True 时把密码重置为 DEFAULT_PASSWORD（管理员帮用户找回）。
    - 返回 (created|updated, 账号信息)；密码永不出现在返回值里。
    """
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    name = normalize_name(payload.get("name"))
    password = str(payload.get("password") or "")
    reset = bool(payload.get("reset_password"))

    conn = db()
    try:
        row = user_repo.get_user(conn, name)
        if row is None:
            role = normalize_role(payload.get("role") or "viewer")
            scope = normalize_scope(payload.get("scope"))
            if reset or not password:
                password = DEFAULT_PASSWORD
            check_password_strength(password)
            salt, h = hash_password(password)
            user_repo.upsert_user(conn, name, salt, h, role, scope, now_iso(),
                                  _pwd_flag(password))
            action = "created"
        else:
            # 只重置密码时不要顺手把角色/范围改成默认值：缺省字段一律保留原值
            role = normalize_role(payload["role"]) if payload.get("role") else row["role"]
            scope = normalize_scope(payload["scope"]) if "scope" in payload else row["scope"]
            if reset:
                password = DEFAULT_PASSWORD
            if password:
                check_password_strength(password)
                salt, h = hash_password(password)
                user_repo.set_password(conn, name, salt, h, _pwd_flag(password))
            if row["role"] == "admin" and role != "admin" \
                    and user_repo.count_admins(conn) <= 1:
                raise ValueError("至少保留一个管理员，不能降低最后一个管理员的角色")
            user_repo.update_meta(conn, name, role, scope)
            action = "updated"
        conn.commit()
    finally:
        conn.close()
    _invalidate_auth_cache()
    info = get_user(name) or {"name": name, "role": role, "scope": scope}
    info["password_reset"] = bool(password)
    return action, info


def batch_create(payload: dict) -> dict:
    """批量建号：账号列表用换行 / 逗号 / 空格分隔，初始密码统一。

    - 已存在的账号一律跳过，不会被覆盖密码或角色。
    - 同一批账号共用一次 PBKDF2（初始密码相同），200 个账号也能秒级完成。
    - 返回 {created, skipped, invalid, role, scope, password}
    """
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    raw = payload.get("names")
    if raw is None:
        raw = payload.get("text") or ""
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = re.split(r"[\s,;，；、]+", str(raw))

    role = normalize_role(payload.get("role") or "viewer")
    scope = normalize_scope(payload.get("scope"))
    password = str(payload.get("password") or "").strip() or DEFAULT_PASSWORD
    check_password_strength(password)
    pwd_default = _pwd_flag(password)

    invalid, names, seen = [], [], set()
    for item in items:
        text = item.strip()
        if not text:
            continue
        try:
            n = normalize_name(text)
        except ValueError as e:
            invalid.append({"name": text[:40], "reason": str(e)})
            continue
        key = n.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(n)

    if not names:
        if invalid:
            raise ValueError(f"没有可用的账号名：{invalid[0]['reason']}")
        raise ValueError("请至少填写一个账号")
    if len(names) > BATCH_MAX_USERS:
        raise ValueError(f"单次最多批量创建 {BATCH_MAX_USERS} 个账号，当前 {len(names)} 个")

    salt, h = hash_password(password)
    created, skipped = [], []
    conn = db()
    try:
        existing = {n.casefold() for n in user_repo.list_names(conn)}
        now = now_iso()
        for n in names:
            if n.casefold() in existing:
                skipped.append(n)
                continue
            user_repo.upsert_user(conn, n, salt, h, role, scope, now, pwd_default)
            created.append(n)
        conn.commit()
    finally:
        conn.close()
    _invalidate_auth_cache()
    return {"created": created, "skipped": skipped, "invalid": invalid,
            "role": role, "scope": scope, "password": password}


def change_own_password(name: str, old_password: str, new_password: str):
    """用户自助改密：必须校验原密码；成功后清缓存，旧凭据立即失效。"""
    name = normalize_name(name)
    if not old_password:
        raise ValueError("请输入当前密码")
    new_password = str(new_password or "")
    check_password_strength(new_password)
    if new_password == old_password:
        raise ValueError("新密码不能与当前密码相同")

    conn = db()
    try:
        row = user_repo.get_credentials(conn, name)
        if row is None:
            raise LookupError("账号不存在")
        if not check_password(old_password, row["salt"], row["pwhash"]):
            raise ValueError("当前密码不正确")
        salt, h = hash_password(new_password)
        user_repo.set_password(conn, name, salt, h, _pwd_flag(new_password))
        conn.commit()
    finally:
        conn.close()
    _invalidate_auth_cache()


def delete_account(name: str, actor: str = ""):
    name = normalize_name(name)
    conn = db()
    try:
        row = user_repo.get_user(conn, name)
        if row is None:
            raise LookupError("账号不存在")
        if actor and name == actor:
            raise ValueError("不能删除当前登录的账号")
        if row["role"] == "admin" and user_repo.count_admins(conn) <= 1:
            raise ValueError("至少保留一个管理员，不能删除最后一个管理员")
        user_repo.delete_user(conn, name)
        conn.commit()
    finally:
        conn.close()
    _invalidate_auth_cache()


def login_locked(ip: str, user: str) -> bool:
    if not ip:
        return False
    now = time.time()
    with _LOGIN_LOCK:
        fails = [t for t in _LOGIN_FAILS.get((ip, user), []) if now - t < LOGIN_WINDOW]
        _LOGIN_FAILS[(ip, user)] = fails
        return len(fails) >= LOGIN_MAX_FAILS and now - fails[-1] < LOGIN_BLOCK_SECONDS


def _record_login_fail(ip: str, user: str):
    if not ip:
        return
    now = time.time()
    with _LOGIN_LOCK:
        if len(_LOGIN_FAILS) > 5000:
            for k in [k for k, v in _LOGIN_FAILS.items()
                      if not v or now - v[-1] > LOGIN_WINDOW]:
                _LOGIN_FAILS.pop(k, None)
        _LOGIN_FAILS.setdefault((ip, user), []).append(now)


def auth_user(headers, ip: str = "") -> dict | None:
    """校验 Basic 认证，返回 {name, role, scope}；失败返回 None"""
    h = headers.get("Authorization", "")
    if not h.startswith("Basic "):
        return None
    now = time.time()
    with _AUTH_CACHE_LOCK:
        cached = _AUTH_CACHE.get(h)
        if cached and cached[0] > now:
            set_user(cached[1].get("name"))
            return dict(cached[1])
        if len(_AUTH_CACHE) > 2000:
            for k, v in [kv for kv in _AUTH_CACHE.items() if kv[1][0] <= now]:
                _AUTH_CACHE.pop(k, None)
    try:
        raw = base64.b64decode(h[6:]).decode("utf-8")
        user, _, pw = raw.partition(":")
    except Exception:
        return None
    if not user or login_locked(ip, user):
        return None
    conn = db()
    row = user_repo.get_credentials(conn, user)
    conn.close()
    if not row or not check_password(pw, row["salt"], row["pwhash"]):
        _record_login_fail(ip, user)
        _log.info("认证失败 user=%s ip=%s", user, ip or "-")
        return None
    obj = {"name": user, "role": row["role"] or "admin",
           "scope": _scope_list(row["scope"] or "*"),
           "pwd_default": bool(row["pwd_default"])}
    with _AUTH_CACHE_LOCK:
        _AUTH_CACHE[h] = (now + _AUTH_CACHE_TTL, obj)
    set_user(user)
    return dict(obj)
