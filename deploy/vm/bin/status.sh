#!/usr/bin/env bash
# 方案 A（内网虚拟机直接部署）状态脚本：进程 + 配置 + /health 深度检查
set -Eeuo pipefail

VM_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$VM_BIN_DIR/_env.sh"

printf '方案      : 虚拟机直接部署（deploy/vm）\n'
printf '配置文件  : %s\n' "$VM_CONF_FILE"
printf '监听      : %s:%s (%s)\n' "$VM_WH_HOST" "$VM_WH_PORT" "$VM_WH_SCHEME"
printf '数据/备份 : %s | %s\n' "$VM_WH_DATA_DIR" "$VM_WH_BACKUP_DIR"
printf '日志/PID  : %s | %s\n' "$VM_WH_LOG_FILE" "$VM_WH_PID_FILE"

if VM_PID="$(vm_wh_pid)"; then
  printf '进程      : 运行中 (PID %s)\n' "$VM_PID"
else
  printf '进程      : 未运行\n'
  exit 1
fi

if ! vm_wh_health; then
  printf '健康检查  : 失败（/health 非 200）\n' >&2
  exit 1
fi

"$VM_WH_PYTHON" - "$VM_WH_BASE_URL" <<'PY'
import json, ssl, sys, urllib.request
url = sys.argv[1].rstrip("/") + "/health"
kw = {"timeout": 3}
if url.startswith("https://"):
    kw["context"] = ssl._create_unverified_context()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
with opener.open(url, **kw) as r:
    d = json.load(r)
print("健康检查  : %s (app=%s, version=%s)" %
      (d.get("status"), d.get("app"), d.get("version")))
print("明细      : %s" % json.dumps(d.get("checks", {}), ensure_ascii=False))
PY
