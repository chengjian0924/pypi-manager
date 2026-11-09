#!/usr/bin/env bash
# 方案 A（内网虚拟机直接部署）首次初始化管理员（一次性动作，不会启动服务）
# 服务运行中会拒绝执行，避免两个进程同时做启动对账。
set -Eeuo pipefail

VM_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$VM_BIN_DIR/_env.sh"

vm_wh_require_python
vm_wh_prepare_dirs

if VM_PID="$(vm_wh_pid)"; then
  printf '服务正在运行（PID %s），请先执行 stop.sh 再初始化账号。\n' "$VM_PID" >&2
  exit 2
fi

VM_USER="${VM_WH_INIT_USER:-admin}"
VM_PASS="${VM_WH_INIT_PASSWORD:-}"
if [[ -z "$VM_PASS" ]]; then
  read -r -s -p "请输入 ${VM_USER} 的初始密码（至少 8 位）: " VM_PASS
  printf '\n'
  read -r -s -p "请再次输入: " VM_PASS2
  printf '\n'
  [[ "$VM_PASS" == "$VM_PASS2" ]] || { printf '两次输入不一致。\n' >&2; exit 2; }
fi
if (( ${#VM_PASS} < 8 )); then
  printf '密码长度不足 8 位（后端 MIN_PASSWORD_LEN=8）。\n' >&2
  exit 2
fi

printf '正在初始化账号 %s（角色 %s，范围 %s）…\n' \
  "$VM_USER" "${VM_WH_INIT_ROLE:-admin}" "${VM_WH_INIT_SCOPE:-*}"

# --init-user 会让 app.py 完成建库/对账后创建账号并立即返回，不会监听端口。
"$VM_WH_PYTHON" "$VM_WH_APP_ENTRY" \
  --data "$VM_WH_DATA_DIR" \
  --frontend-dir "$VM_WH_FRONTEND_DIR" \
  --init-user "${VM_USER}:${VM_PASS}" \
  --role "${VM_WH_INIT_ROLE:-admin}" \
  --scope "${VM_WH_INIT_SCOPE:-*}"

printf '完成。现在可执行 %s/start.sh 启动服务。\n' "$VM_BIN_DIR"
