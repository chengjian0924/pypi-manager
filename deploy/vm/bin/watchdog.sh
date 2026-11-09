#!/usr/bin/env bash
# ============================================================================
# 方案 A（虚拟机直接部署）专用：健康看门狗 / 故障自愈
# ----------------------------------------------------------------------------
# 职责：处理「进程还在但服务已假死」这类 systemd Restart 覆盖不到的故障，
#       以及非 systemd 环境下进程崩溃后的自动拉起。
#   ./watchdog.sh            单次检查（供 systemd timer / cron 调用）
#   ./watchdog.sh --loop     自带定时循环（不装 systemd 时的替代方案）
#   ./watchdog.sh --reset    人工复位熔断计数（处理完根因后执行）
#   ./watchdog.sh --status   查看当前失败/恢复/熔断计数（只读）
#
# 连续 N 次探测失败才恢复；两次恢复之间有冷却；连续恢复仍不健康则熔断，
# 避免磁盘写满、证书过期等「重启也修不好」的问题造成无限重启风暴。
#
# 本文件只依赖 deploy/vm/bin/_env.sh 与本方案配置，不引用 deploy/docker/。
# ============================================================================
set -Eeuo pipefail

VM_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$VM_BIN_DIR/_env.sh"

VM_WD_STATE="$VM_WH_RUN_DIR/watchdog.state"
VM_WD_FAILS_MAX="${VM_WH_WATCHDOG_FAILS:-3}"
VM_WD_COOLDOWN="${VM_WH_WATCHDOG_COOLDOWN:-120}"
VM_WD_MAX_RECOVERIES="${VM_WH_WATCHDOG_MAX_RECOVERIES:-3}"
VM_WD_WAIT_READY="${VM_WH_WATCHDOG_WAIT_READY:-45}"
VM_WD_INTERVAL="${VM_WH_WATCHDOG_INTERVAL:-30}"
VM_WD_RECOVER_CMD="${VM_WH_WATCHDOG_RECOVER_CMD:-systemctl restart ${VM_WH_SERVICE_UNIT:-wheelhouse}}"
VM_WD_ALERT_CMD="${VM_WH_WATCHDOG_ALERT_CMD:-}"
VM_WD_PROBE_CMD="${VM_WH_WATCHDOG_PROBE_CMD:-}"
VM_WD_PAUSE_FILE="${VM_WH_WATCHDOG_PAUSE_FILE:-$VM_WH_RUN_DIR/watchdog.pause}"

# 参数解析放在文件末尾：--reset / --status 需要用到下面定义的函数。

# bash 3.2（macOS 自带）没有 printf %()T，统一用 date，保证脚本跨 Unix 可跑
vm_wd_log() { printf '%s [VM watchdog] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

vm_wd_load() {
  VM_WD_FAILS=0
  VM_WD_LAST=0
  VM_WD_STREAK=0
  VM_WD_ALERTED=0
  [[ -f "$VM_WD_STATE" ]] || return 0
  # shellcheck disable=SC1090
  . "$VM_WD_STATE"
}

vm_wd_save() {
  mkdir -p "$VM_WH_RUN_DIR" 2>/dev/null || {
    vm_wd_log "无法写入状态目录：$VM_WH_RUN_DIR" >&2; return 2; }
  local tmp="$VM_WD_STATE.tmp.$$"
  printf 'VM_WD_FAILS=%d\nVM_WD_LAST=%d\nVM_WD_STREAK=%d\nVM_WD_ALERTED=%d\n' \
    "$VM_WD_FAILS" "$VM_WD_LAST" "$VM_WD_STREAK" "$VM_WD_ALERTED" > "$tmp"
  mv -f "$tmp" "$VM_WD_STATE"
}

# 探测：默认走 _env.sh 的 /health（显式绕过全局 HTTP 代理）；
# 也可通过 VM_WH_WATCHDOG_PROBE_CMD 改成「从外部网关地址探测 HTTPS」。
vm_wd_probe() {
  if [[ -n "$VM_WD_PROBE_CMD" ]]; then
    bash -c "$VM_WD_PROBE_CMD" >/dev/null 2>&1
  else
    vm_wh_health
  fi
}

# 告警钩子：消息通过 WATCHDOG_MESSAGE 传给外部命令，留空则只写日志（journald 可采集）
vm_wd_alert() {
  export WATCHDOG_MESSAGE="$1"
  [[ -n "$VM_WD_ALERT_CMD" ]] || return 0
  bash -c "$VM_WD_ALERT_CMD" >/dev/null 2>&1 \
    || vm_wd_log "告警命令执行失败（不影响自愈流程）"
}

vm_wd_recover() {
  vm_wd_log "连续 ${VM_WD_FAILS}/${VM_WD_FAILS_MAX} 次探测失败，执行恢复：$VM_WD_RECOVER_CMD"
  if ! bash -c "$VM_WD_RECOVER_CMD"; then
    vm_wd_log "恢复命令返回非零，等待下一轮检查" >&2
    return 1
  fi
  local ready=0
  local i
  for ((i = 0; i < VM_WD_WAIT_READY; i++)); do
    if vm_wd_probe; then ready=1; break; fi
    sleep 1
  done
  if [[ "$ready" == "1" ]]; then
    vm_wd_log "恢复成功，服务已重新就绪"
    vm_wd_alert "Wheelhouse（VM 方案）自动恢复成功：$(hostname) $VM_WH_BASE_URL"
  else
    vm_wd_log "恢复命令已执行，但 ${VM_WD_WAIT_READY}s 内仍未就绪，需人工排查" >&2
    vm_wd_alert "Wheelhouse（VM 方案）恢复后仍不可用，请人工介入：$(hostname) $VM_WH_BASE_URL"
  fi
}

vm_wd_tick() {
  local now
  now="$(date +%s)"
  vm_wd_load

  # 计划内停机（升级 / 备份恢复 / 迁移）时先放暂停标记，避免看门狗把服务拉起来
  if [[ -f "$VM_WD_PAUSE_FILE" ]]; then
    vm_wd_log "检测到暂停标记 ${VM_WD_PAUSE_FILE}，本轮跳过（维护完成请删除该文件）"
    return 0
  fi

  if vm_wd_probe; then
    if (( VM_WD_FAILS > 0 || VM_WD_STREAK > 0 )); then
      vm_wd_log "探测已恢复健康，复位失败/恢复计数"
    fi
    VM_WD_FAILS=0; VM_WD_LAST=0; VM_WD_STREAK=0; VM_WD_ALERTED=0
    vm_wd_save
    return 0
  fi

  VM_WD_FAILS=$((VM_WD_FAILS + 1))
  if (( VM_WD_FAILS < VM_WD_FAILS_MAX )); then
    vm_wd_log "健康检查失败 ${VM_WD_FAILS}/${VM_WD_FAILS_MAX}（未达阈值，暂不动作）"
    vm_wd_save
    return 0
  fi

  if (( VM_WD_STREAK >= VM_WD_MAX_RECOVERIES )); then
    if (( VM_WD_ALERTED == 0 )); then
      vm_wd_log "已连续 ${VM_WD_STREAK} 次自动恢复仍不健康，熔断：停止自动重启，等待人工处理（--reset 复位）" >&2
      vm_wd_alert "Wheelhouse（VM 方案）已熔断，请人工介入：$(hostname) $VM_WH_BASE_URL"
      VM_WD_ALERTED=1
    fi
    vm_wd_save
    return 0
  fi

  if (( now - VM_WD_LAST < VM_WD_COOLDOWN )); then
    vm_wd_log "处于冷却期（剩余 $((VM_WD_COOLDOWN - now + VM_WD_LAST))s），本轮跳过恢复"
    vm_wd_save
    return 0
  fi

  vm_wd_recover || true
  VM_WD_STREAK=$((VM_WD_STREAK + 1))
  VM_WD_LAST="$now"
  VM_WD_FAILS=0
  vm_wd_save
}

MODE=once
case "${1:-}" in
  ""|--once) MODE=once ;;
  --loop) MODE=loop ;;
  --reset)
    rm -f "$VM_WD_STATE"
    printf '看门狗状态已复位（%s）。\n' "$VM_WD_STATE"
    exit 0
    ;;
  --status)
    vm_wd_load
    printf '状态文件      %s\n' "$VM_WD_STATE"
    printf '连续失败      %s / %s\n' "$VM_WD_FAILS" "$VM_WD_FAILS_MAX"
    printf '连续恢复次数  %s / %s\n' "$VM_WD_STREAK" "$VM_WD_MAX_RECOVERIES"
    printf '暂停标记      %s\n' "$([[ -f "$VM_WD_PAUSE_FILE" ]] && echo 生效 || echo 无)"
    printf '恢复命令      %s\n' "$VM_WD_RECOVER_CMD"
    exit 0
    ;;
  *) printf '用法: %s [--once|--loop|--reset|--status]\n' "$0" >&2; exit 2 ;;
esac

if [[ "$MODE" == "loop" ]]; then
  vm_wd_log "进入常驻循环，每 ${VM_WD_INTERVAL}s 检查一次（Ctrl+C 退出）"
  while :; do
    vm_wd_tick
    sleep "$VM_WD_INTERVAL"
  done
fi

vm_wd_tick
