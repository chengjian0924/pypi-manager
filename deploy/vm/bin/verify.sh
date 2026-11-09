#!/usr/bin/env bash
# 方案 A（内网虚拟机直接部署）验证脚本
# 只读检查：进程、依赖、端口、健康检查、静态资源、pip 索引、鉴权、数据/备份目录。
# 不上传、不删除、不改动任何业务数据。
set -Eeuo pipefail

VM_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$VM_BIN_DIR/_env.sh"

printf '=== Wheelhouse 虚拟机直接部署 · 独立验证 ===\n'
printf '配置文件: %s\n' "$VM_CONF_FILE"
printf '目标地址: %s\n\n' "$VM_WH_BASE_URL"

if ! VM_PID="$(vm_wh_pid)"; then
  printf '[FAIL] 进程未运行（PID 文件 %s 不存在或进程已退出）\n' "$VM_WH_PID_FILE" >&2
  exit 1
fi
printf '[PASS] 进程运行中 (PID %s)\n' "$VM_PID"

vm_wh_require_python
printf '[PASS] 运行依赖 Python>=3.10 / SQLite>=3.24 / 前后端文件齐备\n'

"$VM_WH_PYTHON" - \
  "$VM_WH_BASE_URL" \
  "${VM_WH_REQUIRE_AUTH_FOR_READ:-1}" \
  "$VM_WH_DATA_DIR" \
  "$VM_WH_BACKUP_DIR" \
  "$VM_WH_FRONTEND_DIR" <<'PY'
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

base, require_auth, data_dir, backup_dir, frontend_dir = sys.argv[1:6]
ctx = ssl._create_unverified_context() if base.startswith("https://") else None
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
fails = []


def check(label, ok, detail=""):
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           (" — " + detail) if detail else ""))
    if not ok:
        fails.append(label)


def req(path, auth=None, accept=None):
    headers = {}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    if accept:
        headers["Accept"] = accept
    kw = {"timeout": 5}
    if ctx is not None:
        kw["context"] = ctx
    request = urllib.request.Request(base.rstrip("/") + path, headers=headers)
    try:
        with opener.open(request, **kw) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


print("[接口检查]")
code, body, _ = req("/health")
health = {}
try:
    health = json.loads(body.decode("utf-8"))
except Exception:
    pass
check("/health 返回 200", code == 200, "HTTP %s" % code)
check("/health 标识为 wheelhouse", health.get("app") == "wheelhouse",
      "app=%s version=%s" % (health.get("app"), health.get("version")))
check("/health 数据库与磁盘检查通过",
      health.get("checks", {}).get("db") == "ok",
      json.dumps(health.get("checks", {}), ensure_ascii=False))

code, body, _ = req("/api/config")
cfg = {}
try:
    cfg = json.loads(body.decode("utf-8"))
except Exception:
    pass
check("/api/config 返回 200 且含 max_upload",
      code == 200 and "max_upload" in cfg, "HTTP %s" % code)

code, body, _ = req("/")
check("管理首页可访问且为 Wheelhouse", code == 200 and b"Wheelhouse" in body,
      "HTTP %s" % code)

code, body, _ = req("/js/main.js")
check("前端 ES Module 静态资源可访问", code == 200 and len(body) > 0,
      "HTTP %s / %s B" % (code, len(body)))

code, _, _ = req("/simple/", accept="application/vnd.pypi.simple.v1+json")
expect = 401 if require_auth == "1" else 200
check("/simple/ 读权限策略符合配置（require_auth=%s）" % require_auth,
      code == expect, "HTTP %s, 期望 %s" % (code, expect))

code, _, _ = req("/api/packages")
check("/api/packages 未登录被拒绝", code == 401, "HTTP %s" % code)

user = os.environ.get("VM_WH_VERIFY_USER", "")
passwd = os.environ.get("VM_WH_VERIFY_PASSWORD", "")
if user and passwd:
    code, body, _ = req("/api/packages", auth="%s:%s" % (user, passwd))
    ok = False
    try:
        payload = json.loads(body.decode("utf-8"))
        ok = code == 200 and "projects" in payload and "stats" in payload
    except Exception:
        pass
    check("使用验证账号可读取包列表（登录态）", ok, "HTTP %s" % code)
else:
    print("  [SKIP] 登录态检查（未设置 VM_WH_VERIFY_USER / VM_WH_VERIFY_PASSWORD）")

print("[路径检查]")
check("数据库文件已生成", os.path.isfile(os.path.join(data_dir, "pypi.db")))
check("包目录存在且可写", os.path.isdir(os.path.join(data_dir, "packages"))
      and os.access(data_dir, os.W_OK))
check("备份目录存在且可写", os.path.isdir(backup_dir) and os.access(backup_dir, os.W_OK))
check("前端目录存在 index.html", os.path.isfile(os.path.join(frontend_dir, "index.html")))

print()
if fails:
    print("验证失败：%d 项未通过 -> %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("验证通过：虚拟机直接部署方案可独立工作。")
PY
