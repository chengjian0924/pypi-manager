#!/usr/bin/env bash
# 方案 B（Docker 部署）专用：加载 .env、计算派生值、封装 compose 调用。
# 只被 deploy/docker/bin/*.sh 引用；虚拟机方案有自己的 bin/_env.sh，二者互不引用。
set -Eeuo pipefail

DK_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DK_DEPLOY_DIR="$(cd "$DK_BIN_DIR/.." && pwd)"
DK_REPO_ROOT="$(cd "$DK_DEPLOY_DIR/../.." && pwd)"
DK_ENV_FILE="${DK_ENV_FILE:-$DK_DEPLOY_DIR/.env}"
DK_COMPOSE_FILE="$DK_DEPLOY_DIR/compose.yaml"

if [[ ! -f "$DK_ENV_FILE" ]]; then
  printf '缺少 Docker 方案配置文件：%s\n' "$DK_ENV_FILE" >&2
  printf '请先执行：\n  cp %s/.env.example %s\n' "$DK_DEPLOY_DIR" "$DK_ENV_FILE" >&2
  return 2
fi

set -a
# shellcheck disable=SC1090
. "$DK_ENV_FILE"
set +a

DK_IMAGE="${DK_IMAGE_NAME:-wheelhouse-intranet}:${DK_IMAGE_TAG:-1.1}"
DK_SERVICE=wheelhouse
DK_CONTAINER="${DK_CONTAINER_NAME:-wheelhouse}"
DK_HOST_PORT="${DK_HOST_PORT:-8081}"
DK_CONTAINER_PORT="${DK_CONTAINER_PORT:-8080}"
DK_HOST_BIND="${DK_HOST_BIND:-127.0.0.1}"
DK_TLS_DIR="${DK_TLS_DIR:-$DK_DEPLOY_DIR/runtime/tls}"
# shell 侧统一转成绝对路径（compose 侧通过环境变量拿到同样的绝对路径）
case "$DK_TLS_DIR" in
  /*) ;;
  *) DK_TLS_DIR="$DK_DEPLOY_DIR/${DK_TLS_DIR#./}" ;;
esac
export DK_TLS_DIR

if [[ -n "${DK_TLS_CERT:-}" ]]; then DK_SCHEME=https; else DK_SCHEME=http; fi
case "$DK_HOST_BIND" in
  0.0.0.0|::|"") DK_PROBE_HOST=127.0.0.1 ;;
  *) DK_PROBE_HOST="$DK_HOST_BIND" ;;
esac
DK_URL="${DK_SCHEME}://${DK_PROBE_HOST}:${DK_HOST_PORT}"

dk_compose() {
  docker compose --env-file "$DK_ENV_FILE" -f "$DK_COMPOSE_FILE" "$@"
}

dk_require_docker() {
  command -v docker >/dev/null 2>&1 || {
    printf '未找到 docker 命令。\n' >&2; return 2; }
  docker compose version >/dev/null 2>&1 || {
    printf '未找到 docker compose 插件（需要 Docker Compose v2）。\n' >&2; return 2; }
  docker info >/dev/null 2>&1 || {
    printf 'Docker 守护进程不可用，请先启动 Docker。\n' >&2; return 2; }
}

dk_container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "$DK_CONTAINER" 2>/dev/null || echo false)" == "true" ]]
}

dk_health() {
  docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
    "$DK_CONTAINER" 2>/dev/null || echo missing
}
