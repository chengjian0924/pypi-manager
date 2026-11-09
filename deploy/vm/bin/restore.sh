#!/usr/bin/env bash
# 方案 A（内网虚拟机直接部署）备份回滚脚本。
#   restore.sh --list                 列出备份点（时间、包文件数、大小、完整性）
#   restore.sh <名称|路径> [--yes]    停服 → 归档现有数据 → 恢复 → 启动
# 只被本方案使用，不引用 Docker 方案任何内容。
set -Eeuo pipefail

VM_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
用法：
  restore.sh --list                 列出备份点（时间、包文件数、大小、完整性）
  restore.sh <名称|路径> [--yes]    回滚到指定备份点

流程：
  1. 停服（写看门狗暂停标记）
  2. 把现有数据移到 <数据目录>/.rollback-<时间戳>
  3. 从备份恢复；失败会自动把归档数据放回原位
  4. 启动并等待 /health 就绪

  --yes   跳过交互确认（自动化用）
  --force 备份缺包文件时仍然恢复（缺的包会丢失，默认拒绝）
EOF
}

# 帮助与配置无关：先处理，避免"只想看用法"却卡在缺配置文件
for arg in "$@"; do
  case "$arg" in -h|--help) usage; exit 0 ;; esac
done

# shellcheck disable=SC1090
. "$VM_BIN_DIR/_env.sh"

VM_ACTION=""
VM_TARGET=""
VM_ASSUME_YES=0
VM_ACCEPT_INCOMPLETE=0

for arg in "$@"; do
  case "$arg" in
    --list|-l) VM_ACTION="list" ;;
    --yes|-y)  VM_ASSUME_YES=1 ;;
    --force|-f) VM_ACCEPT_INCOMPLETE=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) printf '未知参数：%s\n\n' "$arg" >&2; usage >&2; exit 2 ;;
    *)
      if [[ -n "$VM_TARGET" ]]; then
        printf '只能指定一个备份点（已指定 %s）\n' "$VM_TARGET" >&2
        exit 2
      fi
      VM_ACTION="restore"
      VM_TARGET="$arg"
      ;;
  esac
done

vm_wh_require_python
vm_wh_prepare_dirs

VM_APP_ARGS=(--data "$VM_WH_DATA_DIR" --backup-dir "$VM_WH_BACKUP_DIR")

if [[ "$VM_ACTION" == "list" ]]; then
  exec "$VM_WH_PYTHON" "$VM_WH_APP_ENTRY" "${VM_APP_ARGS[@]}" --list-backups
fi

if [[ "$VM_ACTION" != "restore" || -z "$VM_TARGET" ]]; then
  usage >&2
  exit 2
fi

# 支持「备份目录里的名称」与「绝对路径」两种写法
if [[ -d "$VM_TARGET" ]]; then
  VM_BK="$VM_TARGET"
else
  VM_BK="$VM_WH_BACKUP_DIR/$VM_TARGET"
fi
[[ -d "$VM_BK" ]] || {
  printf '备份不存在：%s\n可用备份：\n' "$VM_BK" >&2
  "$VM_WH_PYTHON" "$VM_WH_APP_ENTRY" "${VM_APP_ARGS[@]}" --list-backups >&2 || true
  exit 2
}
[[ -f "$VM_BK/pypi.db" ]] || { printf '备份缺少 pypi.db：%s\n' "$VM_BK" >&2; exit 2; }

# 服务在跑但不是本脚本 PID 文件管的（典型是 systemd 托管），停服会停不掉，
# 此时继续恢复等于往正在写的目录里灌数据，必须拒绝。
if vm_wh_health && ! vm_wh_pid >/dev/null 2>&1; then
  printf '检测到服务正在运行，但不是本脚本 PID 文件管理的进程（可能是 systemd）。\n' >&2
  printf '请先停止服务（如 sudo systemctl stop wheelhouse），再重跑本脚本。\n' >&2
  exit 2
fi

# 备份完整性预检：缺包时默认拒绝，避免"恢复成功但少包"的静默丢数据
VM_LIST="$("$VM_WH_PYTHON" "$VM_WH_APP_ENTRY" "${VM_APP_ARGS[@]}" --list-backups 2>/dev/null || true)"
VM_LINE="$(printf '%s\n' "$VM_LIST" | grep -F "$(basename "$VM_BK")" | head -n 1 || true)"
if [[ -n "$VM_LINE" && "$VM_LINE" != *"完整"* && "$VM_ACCEPT_INCOMPLETE" != "1" ]]; then
  printf '该备份不完整，已拒绝回滚：\n  %s\n' "$VM_LINE" >&2
  printf '确认要恢复残缺数据（缺的包会丢失）请加 --force。\n' >&2
  exit 2
fi

printf '即将回滚\n'
printf '  备份点    %s\n' "$VM_BK"
printf '  数据目录  %s\n' "$VM_WH_DATA_DIR"
printf '  现有数据  %s/.rollback-<时间戳>（可在确认无误后删除）\n' "$VM_WH_DATA_DIR"
if [[ -f "$VM_BK/manifest.json" ]]; then
  printf '  清单      %s\n' "$(tr -d '\n' < "$VM_BK/manifest.json")"
fi

if [[ "$VM_ASSUME_YES" != "1" ]]; then
  printf '\n确认回滚请输入 yes（其他任意输入取消）：'
  VM_ANSWER=""
  read -r VM_ANSWER || true
  if [[ "$VM_ANSWER" != "yes" ]]; then
    printf '已取消，未做任何改动。\n'
    exit 1
  fi
fi

printf '\n[1/4] 停止服务…\n'
"$VM_BIN_DIR/stop.sh"

printf '\n[2/4] 恢复备份…\n'
set +e
VM_OUT="$("$VM_WH_PYTHON" "$VM_WH_APP_ENTRY" "${VM_APP_ARGS[@]}" \
  --restore "$VM_BK" --force 2>&1)"
VM_RC=$?
set -e
printf '%s\n' "$VM_OUT"

if [[ "$VM_RC" -ne 0 ]]; then
  printf '\n[失败] 恢复未完成。若已有归档，数据已自动放回原位；\n' >&2
  printf '       归档目录请查看 %s 下的 .rollback-* 。\n' "$VM_WH_DATA_DIR" >&2
  printf '       服务当前是停止状态，确认数据后执行：%s\n' \
    "$VM_BIN_DIR/start.sh" >&2
  exit "$VM_RC"
fi

printf '\n[3/4] 启动服务…\n'
"$VM_BIN_DIR/start.sh"

printf '\n[4/4] 检查结果…\n'
"$VM_BIN_DIR/status.sh" || true
printf '\n回滚完成。核对包列表无误后可删除归档：rm -rf %s/.rollback-*\n' \
  "$VM_WH_DATA_DIR"
