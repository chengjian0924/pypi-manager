#!/usr/bin/env bash
# 方案 B（Docker 部署）启动脚本：按需构建镜像 -> up -d -> 等待 healthy
set -Eeuo pipefail

DK_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$DK_BIN_DIR/_env.sh"

dk_require_docker
mkdir -p "$DK_TLS_DIR" || {
  printf '无法创建证书挂载目录：%s\n' "$DK_TLS_DIR" >&2; exit 2; }

if [[ "${DK_BUILD_ON_START:-1}" == "1" ]] \
   || ! docker image inspect "$DK_IMAGE" >/dev/null 2>&1; then
  printf '构建镜像 %s（基础镜像 %s，零 pip 依赖）…\n' \
    "$DK_IMAGE" "${DK_BASE_IMAGE:-python:3.12-slim}"
  dk_compose build
else
  printf '复用已有镜像 %s（DK_BUILD_ON_START=0）。\n' "$DK_IMAGE"
fi

dk_compose up -d

DK_READY=0
for _ in $(seq 1 60); do
  if [[ "$(dk_health)" == "healthy" ]]; then DK_READY=1; break; fi
  if ! dk_container_running; then
    printf '容器意外退出，日志尾部：\n' >&2
    dk_compose logs --tail=30 "$DK_SERVICE" >&2 || true
    exit 1
  fi
  sleep 1
done
if [[ "$DK_READY" != "1" ]]; then
  printf '启动超时（60s 内健康检查未就绪），请执行 %s/logs.sh 查看日志。\n' \
    "$DK_BIN_DIR" >&2
  exit 1
fi

printf 'Wheelhouse 已启动 [Docker 部署方案]\n'
printf '  访问地址  %s/\n' "$DK_URL"
printf '  pip 源    %s/simple/\n' "$DK_URL"
printf '  容器/镜像 %s | %s\n' "$DK_CONTAINER" "$DK_IMAGE"
printf '  宿主端口  %s:%s -> 容器 %s\n' \
  "$DK_HOST_BIND" "$DK_HOST_PORT" "$DK_CONTAINER_PORT"
printf '  数据卷    %s -> /data\n' "${DK_DATA_SOURCE:-wheelhouse-data}"
printf '  备份卷    %s -> /backup\n' "${DK_BACKUP_SOURCE:-wheelhouse-backup}"

# 绑定回环时只有本机能访问，这是「前面有反向代理」的安全默认值。
# 不说清楚会让人以为服务坏了，所以每次启动都显式提示。
case "$DK_HOST_BIND" in
  127.0.0.1|localhost|::1)
    printf '\n注意：端口只绑定了 %s，只有本机可以访问。\n' "$DK_HOST_BIND"
    printf '  同事的 pip / 浏览器要访问，二选一：\n'
    printf '    1) 本机 Nginx 监听 443，反代到 %s（TLS 在 Nginx 终止，推荐）\n' "$DK_URL"
    printf '    2) 改 .env 的 DK_HOST_BIND=0.0.0.0 后重新执行本脚本（建议同时启用 TLS）\n'
    ;;
  *)
    # 绑 0.0.0.0 时上面的「访问地址」是回环探测地址，直接发给同事仍然连不上，
    # 这里按本机第一个网卡地址给出真正可分发的 URL。
    DK_LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    if [[ -z "$DK_LAN_IP" ]]; then
      DK_LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || true)"   # macOS 兜底
    fi
    if [[ -n "$DK_LAN_IP" ]]; then
      printf '  对局域网开放 http://%s:%s/（明文 HTTP，请确认防火墙已放行）\n' \
        "$DK_LAN_IP" "$DK_HOST_PORT"
    else
      printf '  对局域网开放 %s:%s（未识别出本机 IP，请手工确认，并放行防火墙）\n' \
        "$DK_HOST_BIND" "$DK_HOST_PORT"
    fi
    ;;
esac

# 显式启动 = 退出维护状态，恢复看门狗的自动恢复能力（与 stop.sh 成对）
DK_WD_PAUSE_FILE="${DK_WATCHDOG_PAUSE_FILE:-$DK_DEPLOY_DIR/runtime/watchdog.pause}"
rm -f "$DK_WD_PAUSE_FILE"
