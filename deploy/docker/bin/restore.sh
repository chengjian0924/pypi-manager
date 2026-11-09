#!/usr/bin/env bash
# 方案 B（Docker 部署）备份回滚脚本。
#   restore.sh --list                 列出备份卷里的备份点与完整性
#   restore.sh <名称> [--yes]         停容器 → 归档现有数据 → 恢复 → 启动
# 只被本方案使用，不引用虚拟机方案任何内容。
set -Eeuo pipefail

DK_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
用法：
  restore.sh --list              列出备份卷里的备份点（时间、包文件数、大小、完整性）
  restore.sh <名称> [--yes]      回滚到指定备份点（名称取自 --list 的第一列）

流程：
  1. 停容器（写看门狗暂停标记）
  2. 容器内把现有数据移到 /data/.rollback-<时间戳>
  3. 从 /backup/<名称> 恢复；失败自动放回归档数据
  4. 启动并等待健康检查通过

  --yes   跳过交互确认（自动化用）
  --force 备份缺包文件时仍然恢复（缺的包会丢失，默认拒绝）
EOF
}

# 帮助与配置无关：先处理，避免"只想看用法"却卡在缺配置文件
for arg in "$@"; do
  case "$arg" in -h|--help) usage; exit 0 ;; esac
done

# shellcheck disable=SC1090
. "$DK_BIN_DIR/_env.sh"

DK_ACTION=""
DK_TARGET=""
DK_ASSUME_YES=0
DK_ACCEPT_INCOMPLETE=0

for arg in "$@"; do
  case "$arg" in
    --list|-l) DK_ACTION="list" ;;
    --yes|-y)  DK_ASSUME_YES=1 ;;
    --force|-f) DK_ACCEPT_INCOMPLETE=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) printf '未知参数：%s\n\n' "$arg" >&2; usage >&2; exit 2 ;;
    *)
      if [[ -n "$DK_TARGET" ]]; then
        printf '只能指定一个备份点（已指定 %s）\n' "$DK_TARGET" >&2
        exit 2
      fi
      DK_ACTION="restore"
      DK_TARGET="$arg"
      ;;
  esac
done

dk_require_docker

if ! docker image inspect "$DK_IMAGE" >/dev/null 2>&1; then
  printf '镜像不存在：%s\n请先执行 %s/bootstrap.sh 构建镜像。\n' "$DK_IMAGE" "$DK_BIN_DIR" >&2
  exit 2
fi

# 在一次性容器里跑后端命令：不启 entrypoint（它会去开监听），也不碰正在运行的服务
dk_run_app() {
  dk_compose run --rm -T --no-deps --entrypoint python3 \
    "$DK_SERVICE" /app/backend/app.py --data /data --backup-dir /backup "$@"
}

if [[ "$DK_ACTION" == "list" ]]; then
  exec docker compose --env-file "$DK_ENV_FILE" -f "$DK_COMPOSE_FILE" \
    run --rm -T --no-deps --entrypoint python3 \
    "$DK_SERVICE" /app/backend/app.py --data /data --backup-dir /backup --list-backups
fi

if [[ "$DK_ACTION" != "restore" || -z "$DK_TARGET" ]]; then
  usage >&2
  exit 2
fi

if [[ "$DK_TARGET" == /* ]]; then
  printf '请传备份卷里的名称（如 backup-20261109-114330），不要传宿主路径。\n' >&2
  printf '容器内只能看到 /backup 这个卷；先 --list 查看可用名称。\n' >&2
  exit 2
fi

# 备份完整性预检：缺包时默认拒绝，避免"恢复成功但少包"的静默丢数据
DK_LIST="$(dk_run_app --list-backups 2>/dev/null || true)"
DK_LINE="$(printf '%s\n' "$DK_LIST" | grep -F "$DK_TARGET" | head -n 1 || true)"
if [[ -z "$DK_LINE" ]]; then
  printf '备份卷里找不到 %s，可用备份：\n%s\n' "$DK_TARGET" "$DK_LIST" >&2
  exit 2
fi
if [[ "$DK_LINE" != *"完整"* && "$DK_ACCEPT_INCOMPLETE" != "1" ]]; then
  printf '该备份不完整，已拒绝回滚：\n  %s\n' "$DK_LINE" >&2
  printf '确认要恢复残缺数据（缺的包会丢失）请加 --force。\n' >&2
  exit 2
fi

printf '即将回滚\n'
printf '  备份点    /backup/%s\n' "$DK_TARGET"
printf '  数据卷    %s\n' "${DK_DATA_SOURCE:-wheelhouse-data}"
printf '  现有数据  容器内 /data/.rollback-<时间戳>（可在确认无误后删除）\n'

if [[ "$DK_ASSUME_YES" != "1" ]]; then
  printf '\n确认回滚请输入 yes（其他任意输入取消）：'
  DK_ANSWER=""
  read -r DK_ANSWER || true
  if [[ "$DK_ANSWER" != "yes" ]]; then
    printf '已取消，未做任何改动。\n'
    exit 1
  fi
fi

printf '\n[1/4] 停止容器…\n'
"$DK_BIN_DIR/stop.sh"

printf '\n[2/4] 恢复备份…\n'
set +e
DK_OUT="$(dk_run_app --restore "/backup/$DK_TARGET" --force 2>&1)"
DK_RC=$?
set -e
printf '%s\n' "$DK_OUT"

if [[ "$DK_RC" -ne 0 ]]; then
  printf '\n[失败] 恢复未完成。若已有归档，数据已自动放回原位。\n' >&2
  printf '       服务当前是停止状态，确认数据后执行：%s/start.sh\n' "$DK_BIN_DIR" >&2
  exit "$DK_RC"
fi

printf '\n[3/4] 启动容器…\n'
"$DK_BIN_DIR/start.sh"

printf '\n[4/4] 检查结果…\n'
"$DK_BIN_DIR/status.sh" || true
printf '\n回滚完成。核对包列表无误后可清理归档：\n'
printf '  docker compose --env-file %s -f %s run --rm -T --entrypoint sh %s \\\n' \
  "$DK_ENV_FILE" "$DK_COMPOSE_FILE" "$DK_SERVICE"
printf '    -c "rm -rf /data/.rollback-*"\n'
