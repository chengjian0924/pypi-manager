#!/usr/bin/env bash
# 方案 B：把镜像打成 tar 包，供隔离网 / 内网主机 docker load 使用。
#
# 解决的三个常见坑：
#   1) 架构不匹配：在 x86 构建、aarch64 运行，load 进去后 exec format error；
#   2) 忘了带基础镜像：内网重新构建时拉不到 python:3.12-slim；
#   3) 包传过去不知道版本 / 校验。
#
# 用法：
#   bin/export-image.sh                        # 输出到 deploy/docker/dist/，架构跟随本机
#   bin/export-image.sh /tmp/bundle            # 指定输出目录
#   bin/export-image.sh --platform linux/arm64 # 显式指定目标架构
#   bin/export-image.sh --no-base              # 不打包基础镜像（内网不再重建时）
#   bin/export-image.sh --help
set -Eeuo pipefail

DK_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$DK_BIN_DIR/_env.sh"

usage() {
  sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

OUT_DIR=""
TARGET_PLATFORM="${DK_TARGET_PLATFORM:-}"
WITH_BASE=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    --platform) TARGET_PLATFORM="${2:-}"; shift 2 ;;
    --platform=*) TARGET_PLATFORM="${1#*=}"; shift ;;
    --no-base) WITH_BASE=0; shift ;;
    -*) printf '未知参数：%s（--help 查看用法）\n' "$1" >&2; exit 2 ;;
    *) OUT_DIR="$1"; shift ;;
  esac
done
OUT_DIR="${OUT_DIR:-$DK_DEPLOY_DIR/dist}"

dk_require_docker

# ---- 目标架构 ---------------------------------------------------------------
HOST_ARCH="$(uname -m)"
case "$HOST_ARCH" in
  arm64|aarch64) HOST_PLATFORM=linux/arm64 ;;
  x86_64|amd64)  HOST_PLATFORM=linux/amd64 ;;
  *)             HOST_PLATFORM="" ;;
esac
if [[ -z "$TARGET_PLATFORM" ]]; then
  TARGET_PLATFORM="${HOST_PLATFORM:-linux/amd64}"
  printf '未指定 --platform，按本机架构推断为 %s（本机 %s）\n' "$TARGET_PLATFORM" "$HOST_ARCH"
fi
case "$TARGET_PLATFORM" in
  linux/arm64|linux/amd64) ;;
  *) printf '[FAIL] 不支持的目标架构：%s（只支持 linux/arm64 / linux/amd64）\n' \
       "$TARGET_PLATFORM" >&2; exit 2 ;;
esac

if [[ -n "$HOST_PLATFORM" && "$TARGET_PLATFORM" != "$HOST_PLATFORM" ]]; then
  printf '[WARN] 目标架构 %s 与本机 %s 不同，需要 buildx + QEMU 模拟，构建会明显变慢。\n' \
    "$TARGET_PLATFORM" "$HOST_PLATFORM" >&2
  printf '       服务器是 aarch64 时，建议找一台 arm64 机器构建，或直接用 --platform linux/arm64。\n' >&2
fi

# ---- 构建 -------------------------------------------------------------------
printf '\n=== 构建镜像 %s（目标 %s）===\n' "$DK_IMAGE" "$TARGET_PLATFORM"

dk_build() {
  docker build \
    --platform "$TARGET_PLATFORM" \
    --build-arg "BASE_IMAGE=$DK_BASE_IMAGE" \
    -f "$DK_DEPLOY_DIR/Dockerfile" \
    -t "$DK_IMAGE" \
    "$DK_REPO_ROOT"
}

BUILD_LOG="$(mktemp)"
if ! dk_build 2>&1 | tee "$BUILD_LOG"; then
  # Docker Desktop（macOS）已知问题：buildx 更新「builder 最近活跃时间」时，
  # 覆盖已有的时间戳文件会 EPERM（rename ... operation not permitted）。
  # 该文件只是时间戳、会被自动重建，删掉目标文件后重试一次即可。
  DK_STALE="$(sed -n 's/.*rename [^ ]* \([^ ]*\): operation not permitted.*/\1/p' \
    "$BUILD_LOG" | head -1)"
  if [[ -n "$DK_STALE" && -f "$DK_STALE" ]]; then
    printf '\n[WARN] Docker Desktop 的 buildx 时间戳文件无法覆盖写，删除后重试一次：%s\n' \
      "$DK_STALE" >&2
    rm -f "$DK_STALE"
    dk_build
  else
    rm -f "$BUILD_LOG"
    exit 1
  fi
fi
rm -f "$BUILD_LOG"

ACTUAL="$(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$DK_IMAGE")"
if [[ "$ACTUAL" != "$TARGET_PLATFORM" ]]; then
  printf '[FAIL] 镜像架构不符：期望 %s，实际 %s\n' "$TARGET_PLATFORM" "$ACTUAL" >&2
  exit 1
fi
printf '[PASS] 镜像架构 %s\n' "$ACTUAL"

# ---- 导出 -------------------------------------------------------------------
mkdir -p "$OUT_DIR"
SAFE_IMAGE="${DK_IMAGE//\//_}"
SAFE_IMAGE="${SAFE_IMAGE//:/_}"
ARCH_TAG="${TARGET_PLATFORM#linux/}"
APP_TAR="$OUT_DIR/${SAFE_IMAGE}-${ARCH_TAG}.tar"

printf '\n=== 导出到 %s ===\n' "$OUT_DIR"
docker save -o "$APP_TAR" "$DK_IMAGE"
printf '  业务镜像  %s\n' "$(basename "$APP_TAR")"

BASE_TAR=""
if [[ "$WITH_BASE" == "1" ]]; then
  SAFE_BASE="${DK_BASE_IMAGE//\//_}"
  SAFE_BASE="${SAFE_BASE//:/_}"
  BASE_TAR="$OUT_DIR/${SAFE_BASE}-${ARCH_TAG}.tar"
  printf '  基础镜像  %s\n' "$(basename "$BASE_TAR")"
  # 基础镜像必须与目标架构一致，否则内网重建镜像时又会踩架构的坑
  docker image inspect "$DK_BASE_IMAGE" >/dev/null 2>&1 \
    || docker pull --platform "$TARGET_PLATFORM" "$DK_BASE_IMAGE"
  BASE_ACTUAL="$(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$DK_BASE_IMAGE")"
  if [[ "$BASE_ACTUAL" != "$TARGET_PLATFORM" ]]; then
    printf '[WARN] 本地基础镜像 %s 是 %s，与目标 %s 不符，按目标架构重新拉取…\n' \
      "$DK_BASE_IMAGE" "$BASE_ACTUAL" "$TARGET_PLATFORM" >&2
    docker pull --platform "$TARGET_PLATFORM" "$DK_BASE_IMAGE"
    BASE_ACTUAL="$(docker image inspect -f '{{.Os}}/{{.Architecture}}' "$DK_BASE_IMAGE")"
  fi
  printf '            架构 %s\n' "$BASE_ACTUAL"
  docker save -o "$BASE_TAR" "$DK_BASE_IMAGE"
fi

# ---- 校验值 + 内网侧说明 ----------------------------------------------------
(
  cd "$OUT_DIR"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum ./*.tar > SHA256SUMS
  else
    shasum -a 256 ./*.tar > SHA256SUMS
  fi
)
printf '  校验文件  SHA256SUMS\n'

APP_LOAD_CMD="docker load -i $(basename "$APP_TAR")"
BASE_LOAD_CMD=""
BASE_NOTE=""
if [[ -n "$BASE_TAR" ]]; then
  BASE_LOAD_CMD="docker load -i $(basename "$BASE_TAR")"
  BASE_NOTE="基础镜像：\`${DK_BASE_IMAGE}\`（内网需要重新构建时用）"
fi

# 注意：变量紧邻中文全角括号时必须写 ${VAR}，否则 bash 会把全角字符
# 当成变量名的一部分，报 unbound variable。
cat > "$OUT_DIR/LOAD.md" <<EOF
# 内网主机导入步骤（${TARGET_PLATFORM}）

\`\`\`bash
# 1) 校验传输完整性（与本目录 SHA256SUMS 比对）
sha256sum -c SHA256SUMS

# 2) 导入镜像
${APP_LOAD_CMD}
${BASE_LOAD_CMD}

# 3) 确认镜像架构（必须显示 ${TARGET_PLATFORM}）
docker image inspect -f '{{.Os}}/{{.Architecture}}' "${DK_IMAGE}"

# 4) 关闭构建：镜像已导入，不要再让 start.sh 重新构建
#    编辑 deploy/docker/.env：DK_BUILD_ON_START=0
#    （内网若无法访问公网，这一步不改会卡在拉取 ${DK_BASE_IMAGE}）

# 5) 启动并验证
deploy/docker/bin/start.sh
deploy/docker/bin/verify.sh
\`\`\`

镜像：\`${DK_IMAGE}\`（${TARGET_PLATFORM}）
${BASE_NOTE}
EOF

printf '\n导出完成：%s\n' "$OUT_DIR"
du -h "$APP_TAR" ${BASE_TAR:+"$BASE_TAR"} 2>/dev/null | sed 's/^/  /' || true
printf '\n下一步：把整个目录拷到内网主机，按 %s/LOAD.md 操作。\n' "$OUT_DIR"
