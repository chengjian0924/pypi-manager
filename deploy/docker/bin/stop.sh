#!/usr/bin/env bash
# 方案 B（Docker 部署）停止脚本：只停容器，保留镜像与数据卷
# 彻底移除（含数据）请手动执行：docker compose ... down -v  # 危险，会删除所有包
set -Eeuo pipefail

DK_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$DK_BIN_DIR/_env.sh"

dk_require_docker
printf '正在停止容器 %s …\n' "$DK_CONTAINER"
dk_compose stop

# 计划内停机必须留下明确标记：看门狗见到标记才不会把服务拉起。
# 若直接用 `docker compose stop` 而不走本脚本，看门狗会按「意外停止」自动恢复。
DK_WD_PAUSE_FILE="${DK_WATCHDOG_PAUSE_FILE:-$DK_DEPLOY_DIR/runtime/watchdog.pause}"
mkdir -p "$(dirname "$DK_WD_PAUSE_FILE")" 2>/dev/null || true
: > "$DK_WD_PAUSE_FILE"

printf '已停止。镜像 %s、数据卷 %s、备份卷 %s 均保留。\n' \
  "$DK_IMAGE" "${DK_DATA_SOURCE:-wheelhouse-data}" "${DK_BACKUP_SOURCE:-wheelhouse-backup}"
printf '看门狗已暂停（%s）；下次执行 start.sh 会自动清除标记。\n' "$DK_WD_PAUSE_FILE"
