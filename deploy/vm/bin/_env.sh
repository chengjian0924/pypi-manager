#!/usr/bin/env bash
# 方案 A（虚拟机直接部署）专用：加载配置、计算派生路径、提供公共检查函数。
# 只被 deploy/vm/bin/*.sh 引用；Docker 方案有自己的 bin/_env.sh，二者互不引用。
set -Eeuo pipefail

VM_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VM_DEPLOY_DIR="$(cd "$VM_BIN_DIR/.." && pwd)"
VM_REPO_ROOT="$(cd "$VM_DEPLOY_DIR/../.." && pwd)"
VM_CONF_FILE="${VM_WH_CONF_FILE:-$VM_DEPLOY_DIR/config/wheelhouse.env}"

if [[ ! -f "$VM_CONF_FILE" ]]; then
  printf '缺少虚拟机方案配置文件：%s\n' "$VM_CONF_FILE" >&2
  printf '请先执行：\n  cp %s/config/wheelhouse.env.example %s\n' \
    "$VM_DEPLOY_DIR" "$VM_CONF_FILE" >&2
  return 2
fi

set -a
# shellcheck disable=SC1090
. "$VM_CONF_FILE"
set +a

VM_WH_APP_DIR="${VM_WH_APP_DIR:-$VM_REPO_ROOT}"
VM_WH_PYTHON="${VM_WH_PYTHON:-python3}"
VM_WH_DATA_DIR="${VM_WH_DATA_DIR:-/var/lib/wheelhouse}"
VM_WH_BACKUP_DIR="${VM_WH_BACKUP_DIR:-/var/backups/wheelhouse}"
VM_WH_LOG_DIR="${VM_WH_LOG_DIR:-/var/log/wheelhouse}"
VM_WH_RUN_DIR="${VM_WH_RUN_DIR:-/run/wheelhouse}"
VM_WH_FRONTEND_DIR="${VM_WH_FRONTEND_DIR:-$VM_WH_APP_DIR/frontend}"
VM_WH_HOST="${VM_WH_HOST:-127.0.0.1}"
VM_WH_PORT="${VM_WH_PORT:-8080}"

VM_WH_PID_FILE="$VM_WH_RUN_DIR/wheelhouse.pid"
VM_WH_LOG_FILE="$VM_WH_LOG_DIR/wheelhouse.log"
VM_WH_APP_ENTRY="$VM_WH_APP_DIR/backend/app.py"

case "$VM_WH_HOST" in
  127.0.0.1|localhost|0.0.0.0|::1|"") VM_WH_PROBE_HOST=127.0.0.1 ;;
  *) VM_WH_PROBE_HOST="$VM_WH_HOST" ;;
esac
if [[ -n "${VM_WH_TLS_CERT:-}" ]]; then VM_WH_SCHEME=https; else VM_WH_SCHEME=http; fi
VM_WH_BASE_URL="${VM_WH_SCHEME}://${VM_WH_PROBE_HOST}:${VM_WH_PORT}"

vm_wh_require_python() {
  command -v "$VM_WH_PYTHON" >/dev/null 2>&1 || {
    printf '找不到 Python 解释器：%s（请修改 VM_WH_PYTHON）\n' "$VM_WH_PYTHON" >&2
    return 2
  }
  "$VM_WH_PYTHON" -c '
import sys, sqlite3
if sys.version_info < (3, 10):
    sys.exit("需要 Python >= 3.10，当前 " + sys.version.split()[0])
if sqlite3.sqlite_version_info < (3, 24):
    sys.exit("需要 SQLite >= 3.24，当前 " + sqlite3.sqlite_version)
' || return 2
  [[ -f "$VM_WH_APP_ENTRY" ]] || {
    printf '找不到后端入口：%s（请检查 VM_WH_APP_DIR）\n' "$VM_WH_APP_ENTRY" >&2
    return 2
  }
  [[ -f "$VM_WH_FRONTEND_DIR/index.html" ]] || {
    printf '找不到前端资源：%s/index.html（请检查 VM_WH_FRONTEND_DIR）\n' \
      "$VM_WH_FRONTEND_DIR" >&2
    return 2
  }
}

vm_wh_prepare_dirs() {
  local d
  for d in "$VM_WH_DATA_DIR" "$VM_WH_BACKUP_DIR" "$VM_WH_LOG_DIR" "$VM_WH_RUN_DIR"; do
    mkdir -p "$d" 2>/dev/null || {
      printf '无法创建目录：%s（权限不足？）\n' "$d" >&2; return 2; }
    [[ -w "$d" ]] || { printf '目录不可写：%s\n' "$d" >&2; return 2; }
  done
}

vm_wh_health() {
  "$VM_WH_PYTHON" - "$VM_WH_BASE_URL" <<'PY'
import ssl, sys, urllib.request
url = sys.argv[1].rstrip("/") + "/health"
kw = {"timeout": 3}
if url.startswith("https://"):
    kw["context"] = ssl._create_unverified_context()
# 内网机器常配置全局 HTTP 代理，探测本机服务必须绕过它
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(url, **kw) as r:
        sys.exit(0 if r.status == 200 else 1)
except Exception:
    sys.exit(1)
PY
}

vm_wh_pid() {
  [[ -f "$VM_WH_PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$VM_WH_PID_FILE" 2>/dev/null || true)"
  [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  printf '%s' "$pid"
}
