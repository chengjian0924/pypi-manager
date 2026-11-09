# -*- coding: utf-8 -*-
"""站点级可编辑配置：目前是「客户端接入」的三段示例命令。

存在 settings 表里（值为 JSON），空值表示沿用内置默认。默认文案里的
`{base}` / `{host}` 由前端在渲染时替换成本次访问的实际地址，因此换域名、
加反代前缀都不需要再改文案。
"""
from __future__ import annotations

import json
import re

from ..constants import WRITE_LOCK
from ..data.database import db
from ..data.repositories import settings_repo
from ..utils.naming import now_iso
from .audit_service import audit

KEY = "client_config"
SECTION_ORDER = ("pip", "curl", "twine")
SECTION_LABELS = {
    "pip": "pip / uv / poetry",
    "curl": "CI 上传（curl）",
    "twine": "twine（兼容旧接口）",
}
MAX_SECTION_CHARS = 4000
MAX_TOTAL_CHARS = 12000

DEFAULT_SECTIONS = {
    "pip": (
        "# pip\n"
        "pip config set global.index-url {base}/simple/\n"
        "pip config set trusted-host {host}\n"
        "\n"
        "# uv\n"
        "export UV_INDEX_URL={base}/simple/\n"
        "\n"
        "# poetry\n"
        "[[tool.poetry.source]]\n"
        'name = "internal"\n'
        'url = "{base}/simple/"\n'
        'priority = "primary"'
    ),
    "curl": (
        "# 单个\n"
        "curl -u 用户名:密码 -T dist/pkg-1.0.0-py3-none-any.whl \\\n"
        "  {base}/api/upload/\n"
        "\n"
        "# 批量\n"
        "for f in dist/*.whl; do\n"
        '  curl -u 用户名:密码 -T "$f" {base}/api/upload/\n'
        "done"
    ),
    "twine": "twine upload --repository-url {base}/ dist/*",
}

# 只保留换行与制表符，其余控制字符会让渲染出的等宽块错位
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _effective(custom: dict) -> dict:
    out = {}
    for k in SECTION_ORDER:
        text = (custom.get(k) or "").strip("\n")
        out[k] = text if text.strip() else DEFAULT_SECTIONS[k]
    return out


def _load() -> tuple[dict, dict]:
    """返回 (自定义段落, settings 行)；行里带 updated_at / updated_by"""
    conn = db()
    try:
        row = settings_repo.get(conn, KEY)
    finally:
        conn.close()
    if not row or not row["value"]:
        return {}, dict(row) if row else {}
    try:
        data = json.loads(row["value"])
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    custom = {k: v for k, v in data.items() if k in SECTION_ORDER and isinstance(v, str)}
    return custom, dict(row)


def get_client_config() -> dict:
    """给出当前生效的段落 + 内置默认，供前端渲染与编辑器预填"""
    custom, row = _load()
    sections = _effective(custom)
    return {
        "sections": sections,
        "defaults": dict(DEFAULT_SECTIONS),
        "labels": dict(SECTION_LABELS),
        "order": list(SECTION_ORDER),
        "custom": any((t or "").strip() for t in custom.values()),
        "custom_sections": [k for k in SECTION_ORDER if (custom.get(k) or "").strip()],
        "updated_at": (row or {}).get("updated_at") or "",
        "updated_by": (row or {}).get("updated_by") or "",
        "max_section_chars": MAX_SECTION_CHARS,
    }


def _clean(key: str, value) -> str:
    if not isinstance(value, str):
        raise ValueError(f"段落 {key} 必须是字符串")
    text = value.replace("\r\n", "\n").replace("\r", "\n")
    if _CTRL.search(text):
        raise ValueError(f"段落 {key} 含有不可见控制字符，请重新粘贴")
    text = text.strip("\n")
    if not text.strip():
        return ""   # 只有空白 = 该段用默认
    if len(text) > MAX_SECTION_CHARS:
        raise ValueError(f"段落 {key} 过长（上限 {MAX_SECTION_CHARS} 字符，当前 {len(text)}）")
    # 与默认一致就不落库，将来改进默认文案时这些段落还能自动跟进
    return "" if text == DEFAULT_SECTIONS[key].strip("\n") else text


def save_client_config(payload: dict, user: dict, ip: str = "") -> dict:
    """只更新提交的段落；传空字符串等于该段恢复默认。"""
    if not isinstance(payload, dict):
        raise ValueError("请求体必须是 JSON 对象")
    sections = payload.get("sections")
    if not isinstance(sections, dict):
        raise ValueError("sections 必须是对象")
    unknown = sorted(set(sections) - set(SECTION_ORDER))
    if unknown:
        raise ValueError("未知的配置项：" + ", ".join(unknown))

    custom, _ = _load()
    changed = []
    for key in SECTION_ORDER:
        if key not in sections:
            continue
        text = _clean(key, sections[key])
        if text:
            custom[key] = text
        else:
            custom.pop(key, None)
        changed.append(key)
    if not changed:
        raise ValueError("没有需要更新的段落")

    total = sum(len(custom.get(k) or "") for k in SECTION_ORDER)
    if total > MAX_TOTAL_CHARS:
        raise ValueError(f"三段内容合计过长（上限 {MAX_TOTAL_CHARS} 字符）")

    with WRITE_LOCK:
        conn = db()
        try:
            if any((t or "").strip() for t in custom.values()):
                settings_repo.put(conn, KEY, json.dumps(custom, ensure_ascii=False),
                                  now_iso(), user["name"])
            else:
                settings_repo.delete(conn, KEY)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    audit(user["name"], "edit_client_config", "", {
        "sections": changed,
        "custom": any((t or "").strip() for t in custom.values()),
        "reset": not any((t or "").strip() for t in custom.values()),
    }, ip)
    return get_client_config()


def reset_client_config(user: dict, ip: str = "") -> dict:
    """恢复全部默认：直接删掉这一行"""
    with WRITE_LOCK:
        conn = db()
        try:
            settings_repo.delete(conn, KEY)
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    audit(user["name"], "edit_client_config", "", {"sections": list(SECTION_ORDER),
                                                   "custom": False, "reset": True}, ip)
    return get_client_config()
