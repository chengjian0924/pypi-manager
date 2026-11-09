#!/usr/bin/env bash
# 方案 B（Docker 部署）首次初始化管理员
# 以一次性容器执行 app.py --init-user（会建库/对账后退出，不监听端口、不占用映射端口）。
set -Eeuo pipefail

DK_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$DK_BIN_DIR/_env.sh"

dk_require_docker
mkdir -p "$DK_TLS_DIR" 2>/dev/null || true

if dk_container_running; then
  printf '主容器 %s 正在运行，请先执行 stop.sh 再初始化账号。\n' "$DK_CONTAINER" >&2
  exit 2
fi
if ! docker image inspect "$DK_IMAGE" >/dev/null 2>&1; then
  printf '镜像 %s 不存在，请先执行 start.sh（或 bin/start.sh 的构建步骤）。\n' \
    "$DK_IMAGE" >&2
  exit 2
fi

DK_USER="${DK_INIT_USER:-admin}"
DK_PASS="${DK_INIT_PASSWORD:-}"
if [[ -z "$DK_PASS" ]]; then
  read -r -s -p "请输入 ${DK_USER} 的初始密码（至少 8 位）: " DK_PASS
  printf '\n'
  read -r -s -p "请再次输入: " DK_PASS2
  printf '\n'
  [[ "$DK_PASS" == "$DK_PASS2" ]] || { printf '两次输入不一致。\n' >&2; exit 2; }
fi
if (( ${#DK_PASS} < 8 )); then
  printf '密码长度不足 8 位（后端 MIN_PASSWORD_LEN=8）。\n' >&2
  exit 2
fi

printf '正在初始化账号 %s（角色 %s，范围 %s）…\n' \
  "$DK_USER" "${DK_INIT_ROLE:-admin}" "${DK_INIT_SCOPE:-*}"

# 覆盖 entrypoint，直接调用后端；挂载的数据卷与 compose 定义完全一致。
dk_compose run --rm --no-deps --entrypoint python3 "$DK_SERVICE" \
  /app/backend/app.py \
  --data /data \
  --frontend-dir /app/frontend \
  --init-user "${DK_USER}:${DK_PASS}" \
  --role "${DK_INIT_ROLE:-admin}" \
  --scope "${DK_INIT_SCOPE:-*}"

printf '完成。现在可执行 %s/start.sh 启动服务。\n' "$DK_BIN_DIR"
