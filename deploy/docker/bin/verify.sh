#!/usr/bin/env bash
# 方案 B（Docker 部署）验证脚本
# 只读检查：守护进程、容器、镜像、端口映射、健康检查、静态资源、pip 索引、鉴权、卷。
# 检查在容器内部用镜像自带 Python 执行，宿主机无需 Python/curl 也能完成。
set -Eeuo pipefail

DK_BIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1090
. "$DK_BIN_DIR/_env.sh"

printf '=== Wheelhouse Docker 部署 · 独立验证 ===\n'
printf '配置文件: %s\n' "$DK_ENV_FILE"
printf '目标地址: %s\n\n' "$DK_URL"

dk_require_docker
printf '[PASS] Docker 守护进程与 Compose v2 可用\n'

if ! dk_container_running; then
  printf '[FAIL] 容器 %s 未运行\n' "$DK_CONTAINER" >&2
  exit 1
fi
printf '[PASS] 容器 %s 运行中\n' "$DK_CONTAINER"

DK_ACTUAL_IMAGE="$(docker inspect -f '{{.Config.Image}}' "$DK_CONTAINER" 2>/dev/null || true)"
if [[ "$DK_ACTUAL_IMAGE" != "$DK_IMAGE" ]]; then
  printf '[FAIL] 镜像不匹配：实际 %s，配置 %s\n' "$DK_ACTUAL_IMAGE" "$DK_IMAGE" >&2
  exit 1
fi
printf '[PASS] 使用本方案构建的镜像 %s\n' "$DK_IMAGE"

DK_STATE="$(dk_health)"
if [[ "$DK_STATE" != "healthy" ]]; then
  printf '[FAIL] 容器健康检查状态：%s\n' "$DK_STATE" >&2
  exit 1
fi
printf '[PASS] 容器 HEALTHCHECK = healthy\n'

DK_MAPPED="$(dk_compose port "$DK_SERVICE" "$DK_CONTAINER_PORT" 2>/dev/null || true)"
if [[ -z "$DK_MAPPED" ]]; then
  printf '[FAIL] 未找到 %s:%s 的端口映射\n' "$DK_HOST_BIND" "$DK_HOST_PORT" >&2
  exit 1
fi
printf '[PASS] 端口映射 %s -> 容器 %s\n' "$DK_MAPPED" "$DK_CONTAINER_PORT"

# 「本机通了」不等于「同事能用」：绑定回环时下面的宿主机探测仍会 200，
# 容易给出全绿的假象，所以单独把可达范围标出来。
case "$DK_HOST_BIND" in
  127.0.0.1|localhost|::1)
    printf '[WARN] 端口仅绑定 %s：只有本机可访问；同事需经本机反代，或改 DK_HOST_BIND=0.0.0.0\n' \
      "$DK_HOST_BIND"
    ;;
  *)
    printf '[PASS] 端口对局域网开放（%s:%s），请确认防火墙已放行\n' \
      "$DK_HOST_BIND" "$DK_HOST_PORT"
    ;;
esac

if command -v curl >/dev/null 2>&1; then
  DK_CODE="$(curl --noproxy '*' -k -s -o /dev/null -w '%{http_code}' --max-time 5 "$DK_URL/health" || true)"
  if [[ "$DK_CODE" == "200" ]]; then
    printf '[PASS] 宿主机经映射端口可访问 /health (HTTP 200)\n'
  else
    printf '[FAIL] 宿主机访问 %s/health 返回 %s\n' "$DK_URL" "$DK_CODE" >&2
    exit 1
  fi
else
  printf '[SKIP] 宿主机 HTTP 探测（未安装 curl，容器内检查已覆盖）\n'
fi

printf '\n'
dk_compose exec -T \
  -e VERIFY_USER="${DK_VERIFY_USER:-}" \
  -e VERIFY_PASSWORD="${DK_VERIFY_PASSWORD:-}" \
  "$DK_SERVICE" python3 - \
  "${DK_SCHEME}://127.0.0.1:${DK_CONTAINER_PORT}" \
  "${DK_REQUIRE_AUTH_FOR_READ:-1}" <<'PY'
import base64
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

base, require_auth = sys.argv[1], sys.argv[2]
ctx = ssl._create_unverified_context() if base.startswith("https://") else None
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
fails = []


def check(label, ok, detail=""):
    print("  [%s] %s%s" % ("PASS" if ok else "FAIL", label,
                           (" — " + detail) if detail else ""))
    if not ok:
        fails.append(label)


def req(path, auth=None, accept=None):
    headers = {}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()
    if accept:
        headers["Accept"] = accept
    kw = {"timeout": 5}
    if ctx is not None:
        kw["context"] = ctx
    request = urllib.request.Request(base.rstrip("/") + path, headers=headers)
    try:
        with opener.open(request, **kw) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


print("[容器内接口检查]")
code, body, _ = req("/health")
health = {}
try:
    health = json.loads(body.decode("utf-8"))
except Exception:
    pass
check("/health 返回 200", code == 200, "HTTP %s" % code)
check("/health 标识为 wheelhouse", health.get("app") == "wheelhouse",
      "app=%s version=%s" % (health.get("app"), health.get("version")))
check("/health 数据库与磁盘检查通过",
      health.get("checks", {}).get("db") == "ok",
      json.dumps(health.get("checks", {}), ensure_ascii=False))

code, body, _ = req("/api/config")
cfg = {}
try:
    cfg = json.loads(body.decode("utf-8"))
except Exception:
    pass
check("/api/config 返回 200 且含 max_upload",
      code == 200 and "max_upload" in cfg, "HTTP %s" % code)

code, body, _ = req("/")
check("管理首页可访问且为 Wheelhouse", code == 200 and b"Wheelhouse" in body,
      "HTTP %s" % code)

code, body, _ = req("/js/main.js")
check("前端 ES Module 静态资源可访问", code == 200 and len(body) > 0,
      "HTTP %s / %s B" % (code, len(body)))

code, _, _ = req("/simple/", accept="application/vnd.pypi.simple.v1+json")
expect = 401 if require_auth == "1" else 200
check("/simple/ 读权限策略符合配置（require_auth=%s）" % require_auth,
      code == expect, "HTTP %s, 期望 %s" % (code, expect))

code, _, _ = req("/api/packages")
check("/api/packages 未登录被拒绝", code == 401, "HTTP %s" % code)

user = os.environ.get("VERIFY_USER", "")
passwd = os.environ.get("VERIFY_PASSWORD", "")
if user and passwd:
    code, body, _ = req("/api/packages", auth="%s:%s" % (user, passwd))
    ok = False
    try:
        payload = json.loads(body.decode("utf-8"))
        ok = code == 200 and "projects" in payload and "stats" in payload
    except Exception:
        pass
    check("使用验证账号可读取包列表（登录态）", ok, "HTTP %s" % code)
else:
    print("  [SKIP] 登录态检查（未设置 DK_VERIFY_USER / DK_VERIFY_PASSWORD）")

print("[容器内路径与环境变量检查]")
check("数据库文件已生成", os.path.isfile("/data/pypi.db"))
check("包目录存在且可写", os.path.isdir("/data/packages") and os.access("/data", os.W_OK))
check("备份卷已挂载且可写", os.path.isdir("/backup") and os.access("/backup", os.W_OK))
check("前端目录存在 index.html", os.path.isfile("/app/frontend/index.html"))
check("WH_DATA_DIR / WH_LISTEN_PORT 已注入",
      os.environ.get("WH_DATA_DIR") == "/data" and bool(os.environ.get("WH_LISTEN_PORT")),
      "WH_DATA_DIR=%s WH_LISTEN_PORT=%s" %
      (os.environ.get("WH_DATA_DIR"), os.environ.get("WH_LISTEN_PORT")))

print()
if fails:
    print("验证失败：%d 项未通过 -> %s" % (len(fails), ", ".join(fails)))
    sys.exit(1)
print("验证通过：Docker 部署方案可独立工作。")
PY
