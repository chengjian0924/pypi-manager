#!/usr/bin/env bash
# 方案 A（内网虚拟机直接部署）停止脚本：SIGTERM 优雅停机，超时才 SIGKILL
set -Eeuo pipefail

VM_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$VM_BIN_DIR/_env.sh"

if ! VM_PID="$(vm_wh_pid)"; then
  printf '服务未在运行（PID 文件：%s）。\n' "$VM_WH_PID_FILE"
  rm -f "$VM_WH_PID_FILE"
  exit 0
fi

printf '正在停止 Wheelhouse（PID %s）…\n' "$VM_PID"
kill -TERM "$VM_PID"

VM_TIMEOUT="${VM_WH_STOP_TIMEOUT:-30}"
VM_STOPPED=0
for _ in $(seq 1 "$VM_TIMEOUT"); do
  if ! kill -0 "$VM_PID" 2>/dev/null; then VM_STOPPED=1; break; fi
  sleep 1
done
if [[ "$VM_STOPPED" != "1" ]]; then
  printf '优雅停机超时（%ss），发送 SIGKILL。\n' "$VM_TIMEOUT" >&2
  kill -KILL "$VM_PID" 2>/dev/null || true
fi
rm -f "$VM_WH_PID_FILE"

# 计划内停机留下明确标记，避免 watchdog.sh 把服务重新拉起；
# 下次执行 start.sh（后台模式）会自动清除。systemd 托管时请停 timer 或手动放标记。
VM_WD_PAUSE_FILE="${VM_WH_WATCHDOG_PAUSE_FILE:-$VM_WH_RUN_DIR/watchdog.pause}"
mkdir -p "$(dirname "$VM_WD_PAUSE_FILE")" 2>/dev/null || true
: > "$VM_WD_PAUSE_FILE"

printf '已停止。数据目录 %s 与备份目录 %s 均保持原样。\n' \
  "$VM_WH_DATA_DIR" "$VM_WH_BACKUP_DIR"
printf '看门狗已暂停（%s）；下次执行 start.sh 会自动清除标记。\n' "$VM_WD_PAUSE_FILE"
