#!/usr/bin/env bash
# 方案 A（内网虚拟机直接部署）启动脚本
#   ./start.sh               后台启动 + 等待 /health 就绪 + 写 PID 文件
#   ./start.sh --foreground  前台运行（供 systemd ExecStart 使用）
set -Eeuo pipefail

VM_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$VM_BIN_DIR/_env.sh"

FOREGROUND=0
case "${1:-}" in
  --foreground) FOREGROUND=1 ;;
  "") ;;
  *) printf '用法: %s [--foreground]\n' "$0" >&2; exit 2 ;;
esac

vm_wh_require_python
vm_wh_prepare_dirs

# 端口占用预检：两套方案默认端口故意不同（VM 8080 / Docker 8081），
# 即便同时验证也不会互相抢占。
if ! "$VM_WH_PYTHON" - "$VM_WH_HOST" "$VM_WH_PORT" <<'PY'
import socket, sys
host, port = sys.argv[1], int(sys.argv[2])
if host in ("", "0.0.0.0", "::"):
    host = "127.0.0.1"
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind((host, port))
except OSError as e:
    sys.exit("端口已被占用 %s:%s (%s)" % (host, port, e))
finally:
    s.close()
PY
then
  printf '端口预检未通过，已停止启动。请用 VM_WH_PORT 换端口或先停止占用者。\n' >&2
  exit 2
fi

# ---- 唯一的「环境变量 -> app.py 命令行参数」映射（仅本方案使用） ----
VM_ARGS=(
  --host "$VM_WH_HOST"
  --port "$VM_WH_PORT"
  --data "$VM_WH_DATA_DIR"
  --frontend-dir "$VM_WH_FRONTEND_DIR"
  --max-upload-mb "${VM_WH_MAX_UPLOAD_MB:-512}"
  --max-workers "${VM_WH_MAX_WORKERS:-64}"
  --min-free-mb "${VM_WH_MIN_FREE_MB:-2048}"
  --backup-dir "$VM_WH_BACKUP_DIR"
  --backup-interval-hours "${VM_WH_BACKUP_INTERVAL_HOURS:-24}"
  --backup-keep "${VM_WH_BACKUP_KEEP:-14}"
  --trash-days "${VM_WH_TRASH_DAYS:-30}"
  --trash-max-mb "${VM_WH_TRASH_MAX_MB:-2048}"
  --audit-max-mb "${VM_WH_AUDIT_MAX_MB:-10}"
  --audit-keep "${VM_WH_AUDIT_KEEP:-5}"
  --log-level "${VM_WH_LOG_LEVEL:-INFO}"
  --slow-ms "${VM_WH_SLOW_MS:-1000}"
)
if [[ "${VM_WH_LOG_JSON:-0}" == "1" ]]; then
  VM_ARGS+=(--log-json)
fi
if [[ "${VM_WH_REQUIRE_AUTH_FOR_READ:-1}" == "1" ]]; then
  VM_ARGS+=(--require-auth-for-read)
else
  VM_ARGS+=(--allow-anonymous-read)
fi

if [[ -n "${VM_WH_TLS_CERT:-}" ]]; then
  [[ -f "$VM_WH_TLS_CERT" ]] || {
    printf 'TLS 证书不存在：%s\n' "$VM_WH_TLS_CERT" >&2; exit 2; }
  VM_ARGS+=(--tls-cert "$VM_WH_TLS_CERT")
  if [[ -n "${VM_WH_TLS_KEY:-}" ]]; then
    [[ -f "$VM_WH_TLS_KEY" ]] || {
      printf 'TLS 私钥不存在：%s\n' "$VM_WH_TLS_KEY" >&2; exit 2; }
    VM_ARGS+=(--tls-key "$VM_WH_TLS_KEY")
  fi
elif [[ "$VM_WH_HOST" != "127.0.0.1" && "$VM_WH_HOST" != "localhost" && "$VM_WH_HOST" != "::1" ]]; then
  if [[ "${VM_WH_ALLOW_INSECURE_HTTP:-0}" != "1" ]]; then
    printf '安全启动检查未通过：监听 %s 却未配置 TLS。\n' "$VM_WH_HOST" >&2
    printf '请填 VM_WH_TLS_CERT/VM_WH_TLS_KEY，或绑定 127.0.0.1 由前置反代终止 TLS；\n' >&2
    printf '确需明文监听请显式设置 VM_WH_ALLOW_INSECURE_HTTP=1（不推荐）。\n' >&2
    exit 2
  fi
  VM_ARGS+=(--insecure-http)
  printf '警告：明文 HTTP 监听 %s，已由 VM_WH_ALLOW_INSECURE_HTTP=1 显式放行。\n' \
    "$VM_WH_HOST" >&2
fi

if [[ "$FOREGROUND" == "1" ]]; then
  exec "$VM_WH_PYTHON" "$VM_WH_APP_ENTRY" "${VM_ARGS[@]}"
fi

if VM_OLD_PID="$(vm_wh_pid)"; then
  printf '服务已在运行（PID %s），请先执行 stop.sh。\n' "$VM_OLD_PID" >&2
  exit 1
fi

nohup "$VM_WH_PYTHON" "$VM_WH_APP_ENTRY" "${VM_ARGS[@]}" \
  >> "$VM_WH_LOG_FILE" 2>&1 < /dev/null &
VM_PID=$!
printf '%s\n' "$VM_PID" > "$VM_WH_PID_FILE"

VM_READY=0
for _ in $(seq 1 60); do
  if ! kill -0 "$VM_PID" 2>/dev/null; then
    printf '进程提前退出，日志尾部：\n' >&2
    tail -n 30 "$VM_WH_LOG_FILE" >&2 || true
    rm -f "$VM_WH_PID_FILE"
    exit 1
  fi
  if vm_wh_health; then VM_READY=1; break; fi
  sleep 0.5
done
if [[ "$VM_READY" != "1" ]]; then
  printf '启动超时（30s 内 /health 未就绪），请查看 %s\n' "$VM_WH_LOG_FILE" >&2
  exit 1
fi

printf 'Wheelhouse 已启动 [VM 直接部署方案]\n'
printf '  访问地址  %s/\n' "$VM_WH_BASE_URL"
printf '  pip 源    %s/simple/\n' "$VM_WH_BASE_URL"
printf '  PID       %s  (%s)\n' "$VM_PID" "$VM_WH_PID_FILE"
printf '  数据目录  %s\n' "$VM_WH_DATA_DIR"
printf '  备份目录  %s\n' "$VM_WH_BACKUP_DIR"
printf '  日志      %s\n' "$VM_WH_LOG_FILE"

# 显式启动 = 退出维护状态（与 stop.sh 成对；--foreground 分支已 exec，不走这里）
VM_WD_PAUSE_FILE="${VM_WH_WATCHDOG_PAUSE_FILE:-$VM_WH_RUN_DIR/watchdog.pause}"
rm -f "$VM_WD_PAUSE_FILE"
