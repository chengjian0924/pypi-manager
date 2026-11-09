#!/usr/bin/env bash
# 方案 B（Docker 部署）状态脚本：容器 / 镜像 / 端口映射 / 健康检查
set -Eeuo pipefail

DK_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$DK_BIN_DIR/_env.sh"

dk_require_docker
printf '方案      : Docker 部署（deploy/docker）\n'
printf '配置文件  : %s\n' "$DK_ENV_FILE"
printf '镜像/容器 : %s | %s\n' "$DK_IMAGE" "$DK_CONTAINER"
printf '端口映射  : %s:%s -> 容器 %s\n' \
  "$DK_HOST_BIND" "$DK_HOST_PORT" "$DK_CONTAINER_PORT"
printf '数据/备份 : %s -> /data | %s -> /backup\n' \
  "${DK_DATA_SOURCE:-wheelhouse-data}" "${DK_BACKUP_SOURCE:-wheelhouse-backup}"
printf '\n'

dk_compose ps

if ! dk_container_running; then
  printf '\n容器未运行。\n' >&2
  exit 1
fi

DK_STATE="$(dk_health)"
printf '\n健康检查  : %s\n' "$DK_STATE"
printf '端口实际映射: %s\n' "$(dk_compose port "$DK_SERVICE" "$DK_CONTAINER_PORT" 2>/dev/null || echo 未映射)"
printf '\n最近日志：\n'
dk_compose logs --tail=15 "$DK_SERVICE" || true

[[ "$DK_STATE" == "healthy" ]] || exit 1
