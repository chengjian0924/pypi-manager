#!/usr/bin/env bash
# 方案 B（Docker 部署）日志跟踪
set -Eeuo pipefail

DK_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$DK_BIN_DIR/_env.sh"

dk_require_docker
exec docker compose --env-file "$DK_ENV_FILE" -f "$DK_COMPOSE_FILE" \
  logs -f --tail=100 "$DK_SERVICE"
