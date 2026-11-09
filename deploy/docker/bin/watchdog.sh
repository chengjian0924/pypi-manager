#!/usr/bin/env bash
# ============================================================================
# 方案 B（Docker 部署）专用：健康看门狗 / 故障自愈
# ----------------------------------------------------------------------------
# 职责边界（很重要）：
#   · 容器进程崩溃、Docker 守护进程重启、宿主机重启
#       → 已由 compose 的 restart: unless-stopped + Docker 开机自启负责，本脚本不重复处理。
#   · 容器还在 Running 但 HEALTHCHECK=unhealthy（假死）
#       → Docker 原生【不会】因 unhealthy 自动重启容器（只有 Swarm 会），由本脚本接管。
#   · 容器被 docker rm 意外删除 → 本脚本用 compose up -d 重建（不构建镜像）。
#   · 容器意外停止（restart policy 未生效，如 docker kill）→ compose start 拉起。
#   · 计划内停机 → bin/stop.sh 会写暂停标记，本脚本见到标记【不动作】；
#     直接 `docker compose stop` 不走 stop.sh 会被视为意外停止而自动恢复。
#
#   ./watchdog.sh            单次检查（供 systemd timer / cron 调用）
#   ./watchdog.sh --loop     自带定时循环（不装 systemd 时的替代方案）
#   ./watchdog.sh --reset    人工复位熔断计数
#   ./watchdog.sh --status   查看失败/恢复/熔断计数（只读）
#
# 连续 N 次失败才恢复；两次恢复之间冷却；连续恢复仍不健康则熔断，避免重启风暴。
# 本文件只依赖 deploy/docker/bin/_env.sh 与本方案配置，不引用 deploy/vm/。
# ============================================================================
set -Eeuo pipefail

DK_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$DK_BIN_DIR/_env.sh"

DK_WD_RUNTIME="$DK_DEPLOY_DIR/runtime"
DK_WD_STATE="${DK_WATCHDOG_STATE_FILE:-$DK_WD_RUNTIME/watchdog.state}"
DK_WD_PAUSE_FILE="${DK_WATCHDOG_PAUSE_FILE:-$DK_WD_RUNTIME/watchdog.pause}"
DK_WD_FAILS_MAX="${DK_WATCHDOG_FAILS:-3}"
DK_WD_COOLDOWN="${DK_WATCHDOG_COOLDOWN:-120}"
DK_WD_MAX_RECOVERIES="${DK_WATCHDOG_MAX_RECOVERIES:-3}"
DK_WD_WAIT_READY="${DK_WATCHDOG_WAIT_READY:-60}"
DK_WD_INTERVAL="${DK_WATCHDOG_INTERVAL:-30}"
DK_WD_ALERT_CMD="${DK_WATCHDOG_ALERT_CMD:-}"
DK_WD_PROBE_CMD="${DK_WATCHDOG_PROBE_CMD:-}"

dk_wd_log() { printf '%s [Docker watchdog] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

dk_wd_load() {
  DK_WD_FAILS=0
  DK_WD_LAST=0
  DK_WD_STREAK=0
  DK_WD_ALERTED=0
  [[ -f "$DK_WD_STATE" ]] || return 0
  # shellcheck disable=SC1090
  . "$DK_WD_STATE"
}

dk_wd_save() {
  mkdir -p "$(dirname "$DK_WD_STATE")" 2>/dev/null || {
    dk_wd_log "无法写入状态目录：$(dirname "$DK_WD_STATE")" >&2; return 2; }
  local tmp="$DK_WD_STATE.tmp.$$"
  printf 'DK_WD_FAILS=%d\nDK_WD_LAST=%d\nDK_WD_STREAK=%d\nDK_WD_ALERTED=%d\n' \
    "$DK_WD_FAILS" "$DK_WD_LAST" "$DK_WD_STREAK" "$DK_WD_ALERTED" > "$tmp"
  mv -f "$tmp" "$DK_WD_STATE"
}

# 默认以容器内 HEALTHCHECK 结果为准（只需 docker，不需要宿主 Python/curl）。
# 需要「从外部网关地址探 HTTPS」时可用 DK_WATCHDOG_PROBE_CMD 覆盖。
dk_wd_probe() {
  if [[ -n "$DK_WD_PROBE_CMD" ]]; then
    bash -c "$DK_WD_PROBE_CMD" >/dev/null 2>&1
  else
    dk_require_docker || return 1
    dk_container_running || return 1
    [[ "$(dk_health)" == "healthy" ]]
  fi
}

dk_wd_alert() {
  export WATCHDOG_MESSAGE="$1"
  [[ -n "$DK_WD_ALERT_CMD" ]] || return 0
  bash -c "$DK_WD_ALERT_CMD" >/dev/null 2>&1 \
    || dk_wd_log "告警命令执行失败（不影响自愈流程）"
}

dk_wd_container_exists() {
  docker inspect "$DK_CONTAINER" >/dev/null 2>&1
}

dk_wd_recover() {
  local action
  if ! dk_wd_container_exists; then
    action=up
    dk_wd_log "容器 ${DK_CONTAINER} 已不存在，执行 compose up -d 重建（不构建镜像）"
  elif ! dk_container_running; then
    action=start
    dk_wd_log "连续 ${DK_WD_FAILS}/${DK_WD_FAILS_MAX} 次探测失败（容器已停止且无暂停标记），执行 compose start"
  else
    action=restart
    dk_wd_log "连续 ${DK_WD_FAILS}/${DK_WD_FAILS_MAX} 次探测失败（容器 Running 但不健康），执行 compose restart"
  fi
  case "$action" in
    up)
      dk_compose up -d || { dk_wd_log "up -d 失败，等待下一轮检查" >&2; return 1; } ;;
    start)
      dk_compose start "$DK_SERVICE" || {
        dk_wd_log "compose start 失败，等待下一轮检查" >&2; return 1; } ;;
    restart)
      dk_compose restart "$DK_SERVICE" || {
        dk_wd_log "compose restart 失败，等待下一轮检查" >&2; return 1; } ;;
  esac
  local ready=0
  local i
  for ((i = 0; i < DK_WD_WAIT_READY; i++)); do
    if dk_wd_probe; then ready=1; break; fi
    sleep 1
  done
  if [[ "$ready" == "1" ]]; then
    dk_wd_log "恢复成功，容器已 healthy"
    dk_wd_alert "Wheelhouse（Docker 方案）自动恢复成功：$(hostname) $DK_URL"
  else
    dk_wd_log "恢复命令已执行，但 ${DK_WD_WAIT_READY}s 内仍未 healthy，需人工排查" >&2
    dk_wd_alert "Wheelhouse（Docker 方案）恢复后仍不可用，请人工介入：$(hostname) $DK_URL"
  fi
}

dk_wd_tick() {
  local now
  now="$(date +%s)"
  dk_wd_load

  if [[ -f "$DK_WD_PAUSE_FILE" ]]; then
    dk_wd_log "检测到暂停标记 ${DK_WD_PAUSE_FILE}，本轮跳过（维护完成请删除该文件）"
    return 0
  fi

  dk_require_docker || { dk_wd_log "Docker 守护进程不可用，本轮跳过" >&2; return 0; }

  # 计划内停机由 stop.sh 写入的暂停标记表达（已在本函数开头处理）。
  # 没有标记却处于 stopped = 意外停止，交给下面的失败计数 -> compose start 拉起。
  if dk_wd_probe; then
    if (( DK_WD_FAILS > 0 || DK_WD_STREAK > 0 )); then
      dk_wd_log "探测已恢复健康，复位失败/恢复计数"
    fi
    DK_WD_FAILS=0; DK_WD_LAST=0; DK_WD_STREAK=0; DK_WD_ALERTED=0
    dk_wd_save
    return 0
  fi

  DK_WD_FAILS=$((DK_WD_FAILS + 1))
  if (( DK_WD_FAILS < DK_WD_FAILS_MAX )); then
    dk_wd_log "健康检查失败 ${DK_WD_FAILS}/${DK_WD_FAILS_MAX}（未达阈值，暂不动作）"
    dk_wd_save
    return 0
  fi

  if (( DK_WD_STREAK >= DK_WD_MAX_RECOVERIES )); then
    if (( DK_WD_ALERTED == 0 )); then
      dk_wd_log "已连续 ${DK_WD_STREAK} 次自动恢复仍不健康，熔断：停止自动重启，等待人工处理（--reset 复位）" >&2
      dk_wd_alert "Wheelhouse（Docker 方案）已熔断，请人工介入：$(hostname) $DK_URL"
      DK_WD_ALERTED=1
    fi
    dk_wd_save
    return 0
  fi

  if (( now - DK_WD_LAST < DK_WD_COOLDOWN )); then
    dk_wd_log "处于冷却期（剩余 $((DK_WD_COOLDOWN - now + DK_WD_LAST))s），本轮跳过恢复"
    dk_wd_save
    return 0
  fi

  dk_wd_recover || true
  DK_WD_STREAK=$((DK_WD_STREAK + 1))
  DK_WD_LAST="$now"
  DK_WD_FAILS=0
  dk_wd_save
}

MODE=once
case "${1:-}" in
  ""|--once) MODE=once ;;
  --loop) MODE=loop ;;
  --reset)
    rm -f "$DK_WD_STATE"
    printf '看门狗状态已复位（%s）。\n' "$DK_WD_STATE"
    exit 0
    ;;
  --status)
    dk_wd_load
    printf '状态文件      %s\n' "$DK_WD_STATE"
    printf '连续失败      %s / %s\n' "$DK_WD_FAILS" "$DK_WD_FAILS_MAX"
    printf '连续恢复次数  %s / %s\n' "$DK_WD_STREAK" "$DK_WD_MAX_RECOVERIES"
    printf '暂停标记      %s\n' "$([[ -f "$DK_WD_PAUSE_FILE" ]] && echo 生效 || echo 无)"
    printf '容器          %s\n' "$DK_CONTAINER"
    exit 0
    ;;
  *) printf '用法: %s [--once|--loop|--reset|--status]\n' "$0" >&2; exit 2 ;;
esac

if [[ "$MODE" == "loop" ]]; then
  dk_wd_log "进入常驻循环，每 ${DK_WD_INTERVAL}s 检查一次（Ctrl+C 退出）"
  while :; do
    dk_wd_tick
    sleep "$DK_WD_INTERVAL"
  done
fi

dk_wd_tick
