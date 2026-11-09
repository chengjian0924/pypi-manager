#!/usr/bin/env bash
# 方案 B（Docker 部署）容器内唯一启动逻辑。
# 把容器内 WH_* 环境变量翻译成 backend/app.py 的命令行参数，最后 exec 成为 PID 1。
# 与 deploy/vm/bin/start.sh 是两套各自独立的实现，互不引用。
set -Eeuo pipefail

HOST="${WH_LISTEN_HOST:-0.0.0.0}"
PORT="${WH_LISTEN_PORT:-8080}"
DATA_DIR="${WH_DATA_DIR:-/data}"
BACKUP_DIR="${WH_BACKUP_DIR:-/backup}"
FRONTEND_DIR="${WH_FRONTEND_DIR:-/app/frontend}"
APP_ENTRY=/app/backend/app.py

[[ -f "$APP_ENTRY" ]] || { printf '镜像缺少后端入口：%s\n' "$APP_ENTRY" >&2; exit 2; }
[[ -f "$FRONTEND_DIR/index.html" ]] || {
  printf '镜像缺少前端资源：%s/index.html\n' "$FRONTEND_DIR" >&2; exit 2; }
mkdir -p "$DATA_DIR" "$BACKUP_DIR" 2>/dev/null || true
[[ -w "$DATA_DIR" ]] || {
  printf '数据目录不可写：%s\n请检查卷权限 / DK_UID:DK_GID（默认 10001）。\n' "$DATA_DIR" >&2
  exit 2
}

ARGS=(
  --host "$HOST"
  --port "$PORT"
  --data "$DATA_DIR"
  --frontend-dir "$FRONTEND_DIR"
  --max-upload-mb "${WH_MAX_UPLOAD_MB:-512}"
  --max-workers "${WH_MAX_WORKERS:-64}"
  --min-free-mb "${WH_MIN_FREE_MB:-2048}"
  --backup-dir "$BACKUP_DIR"
  --backup-interval-hours "${WH_BACKUP_INTERVAL_HOURS:-24}"
  --backup-keep "${WH_BACKUP_KEEP:-14}"
  --trash-days "${WH_TRASH_DAYS:-30}"
  --trash-max-mb "${WH_TRASH_MAX_MB:-2048}"
  --audit-max-mb "${WH_AUDIT_MAX_MB:-10}"
  --audit-keep "${WH_AUDIT_KEEP:-5}"
  --log-level "${WH_LOG_LEVEL:-INFO}"
  --slow-ms "${WH_SLOW_MS:-1000}"
)
if [[ "${WH_LOG_JSON:-0}" == "1" ]]; then
  ARGS+=(--log-json)
fi
if [[ "${WH_REQUIRE_AUTH_FOR_READ:-1}" == "1" ]]; then
  ARGS+=(--require-auth-for-read)
else
  ARGS+=(--allow-anonymous-read)
fi

if [[ -n "${WH_TLS_CERT:-}" ]]; then
  [[ -f "$WH_TLS_CERT" ]] || { printf 'TLS 证书不存在：%s\n' "$WH_TLS_CERT" >&2; exit 2; }
  ARGS+=(--tls-cert "$WH_TLS_CERT")
  if [[ -n "${WH_TLS_KEY:-}" ]]; then
    [[ -f "$WH_TLS_KEY" ]] || { printf 'TLS 私钥不存在：%s\n' "$WH_TLS_KEY" >&2; exit 2; }
    ARGS+=(--tls-key "$WH_TLS_KEY")
  fi
  SCHEME=https
elif [[ "$HOST" != "127.0.0.1" && "$HOST" != "localhost" && "$HOST" != "::1" ]]; then
  if [[ "${WH_ALLOW_INSECURE_HTTP:-0}" != "1" ]]; then
    printf '安全启动检查未通过：容器监听 %s 却未配置 TLS。\n' "$HOST" >&2
    printf '请在 DK_TLS_CERT/DK_TLS_KEY 指定挂载到 /tls 的证书，\n' >&2
    printf '或确认已有网关终止 TLS 并设置 DK_ALLOW_INSECURE_HTTP=1。\n' >&2
    exit 2
  fi
  ARGS+=(--insecure-http)
  SCHEME=http
  printf '警告：容器内为明文 HTTP，TLS 必须在容器外终止（DK_ALLOW_INSECURE_HTTP=1）。\n' >&2
else
  SCHEME=http
fi

printf 'Wheelhouse 容器启动 [Docker 方案]\n'
printf '  监听      %s://%s:%s\n' "$SCHEME" "$HOST" "$PORT"
printf '  数据/备份 %s | %s\n' "$DATA_DIR" "$BACKUP_DIR"
printf '  前端      %s\n' "$FRONTEND_DIR"
printf '  读权限    %s\n' "$([ "${WH_REQUIRE_AUTH_FOR_READ:-1}" = "1" ] && echo 需登录 || echo 免登录)"

exec python3 "$APP_ENTRY" "${ARGS[@]}"
