#!/usr/bin/env python3
"""方案 B（Docker 部署）容器健康检查：仅用标准库，跟随容器内 WH_* 环境变量。"""
import os
import ssl
import sys
import urllib.request

port = os.environ.get("WH_LISTEN_PORT", "8080")
use_https = bool(os.environ.get("WH_TLS_CERT"))
url = "%s://127.0.0.1:%s/health" % ("https" if use_https else "http", port)
kwargs = {"timeout": 3}
if use_https:
    kwargs["context"] = ssl._create_unverified_context()
# 显式绕过可能从宿主继承的代理设置
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

ok = False
try:
    with opener.open(url, **kwargs) as response:
        body = response.read()
        ok = response.status == 200 and b'"wheelhouse"' in body
except Exception:
    ok = False
sys.exit(0 if ok else 1)
