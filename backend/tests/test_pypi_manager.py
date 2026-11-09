"""pypi_manager 关键安全与可靠性回归测试（仅标准库）

运行：cd backend && python3 -m unittest discover -s tests -v
"""
import base64
import contextlib
import datetime
import gzip
import io
import json
import os
import shutil
import socket
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

from pypi_manager.config import CFG, apply_args, parse_args, paths
from pypi_manager.constants import (APP_NAME, DEFAULT_PASSWORD, LOGIN_BLOCK_SECONDS,
                                    LOGIN_MAX_FAILS, VERSION)
from pypi_manager.data.database import db, init_storage
from pypi_manager.log import (clear_request, configure, current_request_id,
                              get_logger, set_request, set_user)
from pypi_manager.middleware.server import LimitedThreadingHTTPServer
from pypi_manager.routes.handler import Handler
from pypi_manager.services.audit_service import audit
from pypi_manager.services.auth_service import create_user
from pypi_manager.services.storage_service import (backup_now, inspect_backup,
                                                   list_backups, reconcile_storage,
                                                   restore_backup)
from pypi_manager.services import storage_service

# 保持原测试对 pm.* 的访问方式，仅把单文件模块替换为分层后的实现
pm = SimpleNamespace(
    CFG=CFG, paths=paths, db=db, init_storage=init_storage, create_user=create_user,
    LimitedThreadingHTTPServer=LimitedThreadingHTTPServer, Handler=Handler,
    reconcile_storage=reconcile_storage, audit=audit, backup_now=backup_now,
    restore_backup=restore_backup, inspect_backup=inspect_backup,
    list_backups=list_backups, APP_NAME=APP_NAME, VERSION=VERSION,
    LOGIN_MAX_FAILS=LOGIN_MAX_FAILS, LOGIN_BLOCK_SECONDS=LOGIN_BLOCK_SECONDS,
)

EMPTY_WHEEL = b"PK\x05\x06" + b"\x00" * 18  # 合法但为空的 zip


def make_wheel(name="pkg", extra="x"):
    """内容不同的合法 wheel：用于触发「同名覆盖」而不是「同名同内容跳过」"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("%s-1.0.dist-info/METADATA" % name,
                   "Metadata-Version: 2.1\nName: %s\nVersion: 1.0\n" % name)
        z.writestr("%s/__init__.py" % name, extra)
    return buf.getvalue()


def wheel(name="pkg", version="1.0"):
    return f"{name}-{version}-py3-none-any.whl"


def read_http_response(sock, limit=65536):
    """把一次响应完整读出来：先读到响应头结束，再按 Content-Length 补齐 body。

    不能只 recv 一次——服务端先写响应头、再写 body，两次 send 在 Linux 上
    可能被拆成两个 TCP 分段，一次 recv 只能拿到头，断言 body 就会误报。
    """
    buf = b""
    while b"\r\n\r\n" not in buf and len(buf) < limit:
        part = sock.recv(limit)
        if not part:
            break
        buf += part
    head, sep, body = buf.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n")[1:]:
        key, _, value = line.partition(b":")
        if key.strip().lower() == b"content-length":
            try:
                length = int(value.strip())
            except ValueError:
                length = 0
    while len(body) < length:
        part = sock.recv(limit)
        if not part:
            break
        body += part
    return head + sep + body


class ServerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pm-test-")
        pm.CFG.update({
            "data": os.path.join(cls.tmp, "data"),
            "open_read": True,
            "max_upload": 1024 * 1024,
            "max_workers": 16,
            "min_free_mb": 0,
            "backup_dir": os.path.join(cls.tmp, "backup"),
            "backup_interval_hours": 0,
            "backup_keep": 3,
        })
        pm.init_storage()
        pm.create_user("admin", "admin-pass", "admin")
        pm.create_user("alice", "alice-pass", "uploader", "team-*")
        pm.create_user("bob", "bob-pass", "viewer")
        cls.srv = pm.LimitedThreadingHTTPServer(("127.0.0.1", 0), pm.Handler, max_workers=16)
        cls.port = cls.srv.server_address[1]
        cls.thread = threading.Thread(target=cls.srv.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.thread.join(timeout=5)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def request(self, method, path, data=None, auth=None, headers=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/octet-stream")
        if auth:
            token = base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
            req.add_header("Authorization", "Basic " + token)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status, r.read(), dict(r.headers)
        except urllib.error.HTTPError as e:
            return e.code, e.read(), dict(e.headers)

    def upload(self, filename, body=EMPTY_WHEEL, auth=("admin", "admin-pass")):
        return self.request("PUT", "/api/upload?filename=" + quote(filename), body, auth)


class TestSecurity(ServerTestCase):
    def test_health_ok(self):
        status, body, _ = self.request("GET", "/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_security_headers_present(self):
        _, _, headers = self.request("GET", "/health")
        self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertIn("Content-Security-Policy", headers)

    def test_simple_index_escapes_html(self):
        evil = "<img src=x onerror=alert(1)>-1.0-py3-none-any.whl"
        status, _, _ = self.upload(evil)
        self.assertEqual(status, 200)
        _, page, _ = self.request("GET", "/simple/")
        self.assertIn(b"&lt;img", page)
        self.assertNotIn(b"<img src=x", page)
        _, page, _ = self.request("GET", "/simple/" + quote("<img src=x onerror=alert(1)>") + "/")
        self.assertIn(b"&lt;img", page)
        self.assertNotIn(b"<img src=x", page)

    def test_download_blocks_path_traversal(self):
        status, _, _ = self.request("GET", "/files/" + quote("../pypi.db"))
        self.assertIn(status, (400, 404))

    def test_login_rate_limit_returns_429(self):
        for _ in range(pm.LOGIN_MAX_FAILS):
            status, _, _ = self.request("GET", "/api/whoami", auth=("ghost", "bad"))
            self.assertEqual(status, 401)
        status, _, headers = self.request("GET", "/api/whoami", auth=("ghost", "bad"))
        self.assertEqual(status, 429)
        self.assertEqual(headers.get("Retry-After"), str(pm.LOGIN_BLOCK_SECONDS))

    def test_read_switch_really_gates_traffic(self):
        """open_read=False 时索引与下载必须 401，带凭证才放行。"""
        old = pm.CFG["open_read"]
        pm.CFG["open_read"] = False
        try:
            self.assertEqual(self.request("GET", "/simple/")[0], 401)
            self.assertEqual(self.request("GET", "/simple/", auth=("bob", "bob-pass"))[0], 200)
        finally:
            pm.CFG["open_read"] = old


class TestAuditApi(ServerTestCase):
    """审计接口是前端「审计日志」页的唯一数据源：锁定权限、顺序与 limit 校验。"""

    def test_requires_admin(self):
        self.assertEqual(self.request("GET", "/api/audit")[0], 401)
        self.assertEqual(self.request("GET", "/api/audit", auth=("bob", "bob-pass"))[0], 403)
        status, body, _ = self.request("GET", "/api/audit?limit=5", auth=("admin", "admin-pass"))
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])

    def test_rejects_non_integer_limit(self):
        status, body, _ = self.request("GET", "/api/audit?limit=abc",
                                       auth=("admin", "admin-pass"))
        self.assertEqual(status, 400)
        self.assertIn("limit", json.loads(body)["error"])

    def test_returns_newest_first_with_limit(self):
        pm.audit("tester", "probe_older", "old-target")
        pm.audit("tester", "probe_newer", "new-target")
        status, body, _ = self.request("GET", "/api/audit?limit=2",
                                       auth=("admin", "admin-pass"))
        self.assertEqual(status, 200)
        items = json.loads(body)["items"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["action"], "probe_newer")   # 倒序：最新的在前
        self.assertEqual(items[1]["action"], "probe_older")
        # 页面的固定列依赖这些字段
        for key in ("ts", "user", "action", "target", "ip"):
            self.assertIn(key, items[0])


class TestRBAC(ServerTestCase):
    def test_uploader_scope_isolation(self):
        self.assertEqual(self.upload(wheel("team_pkg"), auth=("alice", "alice-pass"))[0], 200)
        status, body, _ = self.upload(wheel("demo_pkg"), auth=("alice", "alice-pass"))
        self.assertEqual(status, 403)

    def test_viewer_cannot_upload(self):
        status, _, _ = self.upload(wheel("viewer_pkg"), auth=("bob", "bob-pass"))
        self.assertEqual(status, 403)

    def test_viewer_cannot_export(self):
        status, _, _ = self.request("GET", "/api/export.csv", auth=("bob", "bob-pass"))
        self.assertEqual(status, 403)

    def test_scope_filters_package_list(self):
        self.upload(wheel("team_visible"), auth=("alice", "alice-pass"))
        self.upload(wheel("other_visible"), auth=("admin", "admin-pass"))
        _, body, _ = self.request("GET", "/api/packages", auth=("alice", "alice-pass"))
        names = [p["name"] for p in json.loads(body)["projects"]]
        self.assertIn("team-visible", names)
        self.assertNotIn("other-visible", names)

    def test_whoami_reports_role_and_scope(self):
        _, body, _ = self.request("GET", "/api/whoami", auth=("alice", "alice-pass"))
        data = json.loads(body)
        self.assertEqual(data["role"], "uploader")
        self.assertEqual(data["scope"], ["team-*"])


class TestUploadLimits(ServerTestCase):
    def test_oversize_upload_rejected(self):
        old = pm.CFG["max_upload"]
        pm.CFG["max_upload"] = 1024
        try:
            status, _, _ = self.upload(wheel("too_big"), b"x" * 2048)
            self.assertEqual(status, 413)
        finally:
            pm.CFG["max_upload"] = old

    def test_invalid_wheel_rejected(self):
        status, _, _ = self.upload(wheel("broken"), b"not a zip")
        self.assertEqual(status, 400)

    def test_multipart_twine_upload(self):
        boundary = "----pmtestboundary"
        fn = wheel("twine_pkg", "2.0")
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name=":action"\r\n\r\n'
            "file_upload\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="content"; filename="{fn}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + EMPTY_WHEEL + f"\r\n--{boundary}--\r\n".encode()
        status, _, _ = self.request(
            "POST", "/", body, ("admin", "admin-pass"),
            {"Content-Type": f"multipart/form-data; boundary={boundary}",
             "User-Agent": "twine/7.0.0 (cpython 3.13)"})
        self.assertEqual(status, 200)
        self.assertTrue(os.path.isfile(os.path.join(pm.paths()["packages"], fn)))
        # 审计里按客户端标注来源，方便事后核对是谁传的
        _, raw, _ = self.request("GET", "/api/audit?limit=1", auth=("admin", "admin-pass"))
        self.assertEqual(json.loads(raw)["items"][0]["note"], "twine upload")

    def test_multipart_legacy_endpoint(self):
        """poetry 常把上传地址配成 /legacy/，应与 / 等价"""
        boundary = "----pmtestlegacy"
        fn = wheel("legacy_pkg", "3.0")
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name=":action"\r\n\r\n'
            "file_upload\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="content"; filename="{fn}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + EMPTY_WHEEL + f"\r\n--{boundary}--\r\n".encode()
        for path in ("/legacy/", "/legacy"):
            status, _, _ = self.request(
                "POST", path, body, ("admin", "admin-pass"),
                {"Content-Type": f"multipart/form-data; boundary={boundary}"})
            self.assertEqual(status, 200, path)
        self.assertTrue(os.path.isfile(os.path.join(pm.paths()["packages"], fn)))

    def test_chunked_upload_returns_411_not_empty_file(self):
        """分块传输要给出「不支持 chunked」的明确提示，而不是「0 字节」"""
        token = base64.b64encode(b"admin:admin-pass").decode()
        head = (
            "PUT /api/upload?filename=chunked-1.0-py3-none-any.whl HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            f"Authorization: Basic {token}\r\n"
            "Transfer-Encoding: chunked\r\n"
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall(head + b"5\r\nhello\r\n0\r\n\r\n")
            raw = read_http_response(s)
        self.assertIn(b" 411 ", raw.split(b"\r\n")[0])
        self.assertIn("分块传输".encode(), raw)


class TestClientConfig(ServerTestCase):
    """「客户端接入」文案：登录可读、仅管理员可改、可恢复默认"""

    def setUp(self):
        # 每个用例都从默认文案开始，避免用例之间互相污染
        self.request("DELETE", "/api/client-config", auth=("admin", "admin-pass"))

    def get(self, auth=("alice", "alice-pass")):
        status, body, _ = self.request("GET", "/api/client-config", auth=auth)
        return status, (json.loads(body) if body else {})

    def put(self, payload, auth=("admin", "admin-pass")):
        return self.request("PUT", "/api/client-config", json.dumps(payload).encode(), auth,
                            {"Content-Type": "application/json"})

    def test_requires_login(self):
        status, _, _ = self.request("GET", "/api/client-config")
        self.assertEqual(status, 401)

    def test_defaults_are_templates_with_placeholders(self):
        status, data = self.get()
        self.assertEqual(status, 200)
        self.assertFalse(data["custom"])
        self.assertEqual(data["order"], ["pip", "curl", "twine"])
        for key in data["order"]:
            self.assertIn("{base}", data["sections"][key])
        self.assertEqual(data["labels"]["curl"], "CI 上传（curl）")

    def test_admin_writes_viewer_cannot(self):
        payload = {"sections": {"twine": "twine upload -r internal dist/*"}}
        self.assertEqual(self.put(payload, auth=("bob", "bob-pass"))[0], 403)
        self.assertEqual(self.put(payload)[0], 200)

        status, data = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(data["sections"]["twine"], "twine upload -r internal dist/*")
        self.assertTrue(data["custom"])
        self.assertEqual(data["custom_sections"], ["twine"])
        # 没提交的段落仍是默认模板
        self.assertIn("{base}", data["sections"]["pip"])

    def test_empty_section_falls_back_to_default(self):
        self.put({"sections": {"pip": "# 内网\npip config set global.index-url https://m/simple/"}})
        _, data = self.get()
        self.assertTrue(data["custom"])

        self.assertEqual(self.put({"sections": {"pip": "   "}})[0], 200)
        _, data = self.get()
        self.assertFalse(data["custom"])
        self.assertIn("{base}", data["sections"]["pip"])

    def test_reset_restores_defaults(self):
        self.put({"sections": {"pip": "a", "curl": "b"}})
        status, _, _ = self.request("DELETE", "/api/client-config", auth=("admin", "admin-pass"))
        self.assertEqual(status, 200)
        _, data = self.get()
        self.assertFalse(data["custom"])
        self.assertEqual(data["sections"], data["defaults"])

    def test_rejects_bad_payload(self):
        self.assertEqual(self.put({"sections": {"nope": "x"}})[0], 400)
        self.assertEqual(self.put({"sections": {"pip": 123}})[0], 400)
        self.assertEqual(self.put({})[0], 400)
        self.assertEqual(self.put({"sections": {"pip": "x" * 5000}})[0], 400)
        self.assertEqual(self.put({"sections": {"pip": "a\x00b"}})[0], 400)

    def test_edit_is_audited(self):
        self.put({"sections": {"twine": "twine upload -r internal dist/*"}})
        _, raw, _ = self.request("GET", "/api/audit?limit=1", auth=("admin", "admin-pass"))
        item = json.loads(raw)["items"][0]
        self.assertEqual(item["action"], "edit_client_config")
        self.assertEqual(item["sections"], ["twine"])


class TestP1Fixes(ServerTestCase):
    def test_filename_sanitized_against_header_injection(self):
        raw = 'evil"\r\nX-Injected: 1-1.0-py3-none-any.whl'
        status, body, _ = self.upload(raw)
        self.assertEqual(status, 200)
        fn = json.loads(body)["filename"]
        self.assertNotIn("\r", fn)
        self.assertNotIn("\n", fn)
        self.assertNotIn('"', fn)
        self.assertTrue(os.path.isfile(os.path.join(pm.paths()["packages"], fn)))
        _, _, headers = self.request("GET", "/files/" + quote(fn))
        self.assertNotIn("X-Injected", headers)
        self.assertIn("filename*=UTF-8''", headers.get("Content-Disposition", ""))

    def test_download_supports_etag_and_range(self):
        fn = wheel("range_pkg")
        self.assertEqual(self.upload(fn)[0], 200)
        status, body, headers = self.request("GET", "/files/" + quote(fn))
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Accept-Ranges"), "bytes")
        etag = headers.get("ETag")
        self.assertTrue(etag)
        status, _, _ = self.request("GET", "/files/" + quote(fn),
                                    headers={"If-None-Match": etag})
        self.assertEqual(status, 304)
        status, part, headers = self.request("GET", "/files/" + quote(fn),
                                             headers={"Range": "bytes=0-3"})
        self.assertEqual(status, 206)
        self.assertEqual(part, EMPTY_WHEEL[:4])
        self.assertEqual(headers.get("Content-Range"),
                         f"bytes 0-3/{len(EMPTY_WHEEL)}")

    def test_simple_index_etag_returns_304(self):
        status, _, headers = self.request("GET", "/simple/")
        self.assertEqual(status, 200)
        etag = headers.get("ETag")
        self.assertTrue(etag)
        status, _, _ = self.request("GET", "/simple/", headers={"If-None-Match": etag})
        self.assertEqual(status, 304)

    def test_cross_site_write_rejected(self):
        status, _, _ = self.request("POST", "/api/backup", b"", ("admin", "admin-pass"),
                                    {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/backup", b"", ("admin", "admin-pass"),
                                    {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual(status, 200)

    def test_trash_retention_cleans_expired_files(self):
        old = os.path.join(pm.paths()["trash"], "old__pkg-1.0-py3-none-any.whl")
        with open(old, "wb") as f:
            f.write(b"x")
        past = time.time() - 40 * 86400
        os.utime(old, (past, past))
        fresh = os.path.join(pm.paths()["trash"], "fresh__pkg-1.0-py3-none-any.whl")
        with open(fresh, "wb") as f:
            f.write(b"x")
        report = pm.reconcile_storage()
        self.assertGreaterEqual(report["trash_removed"], 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))

    def test_audit_log_rotation(self):
        path = pm.paths()["audit"]
        old = pm.CFG["audit_max_mb"]
        pm.CFG["audit_max_mb"] = 1
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write("x" * (1024 * 1024 + 10) + "\n")
            pm.audit("tester", "rotate", "test")
        finally:
            pm.CFG["audit_max_mb"] = old
        self.assertTrue(os.path.isfile(path + ".1"))
        self.assertLess(os.path.getsize(path), 1024 * 1024)

    def test_password_policy(self):
        with self.assertRaises(ValueError):
            pm.create_user("weak", "short", "viewer")
        pm.create_user("strong", "long-enough-pass", "viewer")

    def test_gzip_for_large_html(self):
        status, body, headers = self.request("GET", "/", headers={"Accept-Encoding": "gzip"})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Encoding"), "gzip")
        self.assertIn(b"<html", gzip.decompress(body))

    def test_server_header_hides_python_version(self):
        _, _, headers = self.request("GET", "/health")
        self.assertEqual(headers.get("Server"), f"{pm.APP_NAME}/{pm.VERSION}")

    def test_csv_export_uses_normalized_project(self):
        fn = wheel("csv_pkg")
        self.assertEqual(self.upload(fn)[0], 200)
        status, body, _ = self.request("GET", "/api/export.csv", auth=("admin", "admin-pass"))
        self.assertEqual(status, 200)
        text = body.decode("utf-8-sig")
        rows = [line.split(",") for line in text.strip().splitlines()]
        match = [r for r in rows if r and r[0] == fn]
        self.assertTrue(match)
        self.assertEqual(match[0][1], "csv-pkg")

    def test_backup_can_be_restored_offline(self):
        fn = wheel("restore_pkg")
        self.assertEqual(self.upload(fn)[0], 200)
        dest = pm.backup_now()
        target = tempfile.mkdtemp(prefix="pm-restore-")
        old_data = pm.CFG["data"]
        pm.CFG["data"] = os.path.join(target, "data")
        try:
            result = pm.restore_backup(dest)
            root = result["root"]
            self.assertEqual(result["copied"], 1)
            self.assertIsNone(result["archived"])
            self.assertTrue(os.path.isfile(os.path.join(root, "pypi.db")))
            self.assertTrue(os.path.isfile(os.path.join(root, "packages", fn)))
            conn = sqlite3.connect(os.path.join(root, "pypi.db"))
            names = [r[0] for r in conn.execute("SELECT filename FROM files")]
            conn.close()
            self.assertIn(fn, names)
        finally:
            pm.CFG["data"] = old_data
            shutil.rmtree(target, ignore_errors=True)


class TestRestoreSafety(ServerTestCase):
    """恢复安全：完整性预检、拒绝非空目标、--force 归档切换"""

    def _backup(self, *names):
        for name in names:
            self.assertEqual(self.upload(wheel(name))[0], 200)
        return pm.backup_now()

    def _target(self):
        base = tempfile.mkdtemp(prefix="pm-restore-")
        self.addCleanup(shutil.rmtree, base, True)
        return os.path.join(base, "data")

    def _with_data(self, target):
        old = pm.CFG["data"]
        pm.CFG["data"] = target
        self.addCleanup(lambda: pm.CFG.__setitem__("data", old))

    def test_backup_keep_never_prunes_the_fresh_backup(self):
        """同一秒内连续备份：目录名会重复、旧名字会被复用，淘汰不能删掉刚建好的那份

        CI 上多个用例的备份能挤进同一秒：backup-<秒>、backup-<秒>-a1b2 …
        旧的那份被淘汰后名字被下一次备份重新用上，若按名字排序淘汰，
        新建的 backup-<秒> 会排在最后一位前面而被自己触发的清理删掉。
        """
        fixed = datetime.datetime(2026, 11, 9, 8, 41, 50)

        class FixedDatetime:
            @staticmethod
            def now():
                return fixed

        dests = []
        with mock.patch.object(storage_service, "datetime", FixedDatetime):
            for i in range(5):
                dests.append(self._backup("keep_%d" % i))

        stored = pm.list_backups()
        self.assertLessEqual(len(stored), 3, "应遵守 backup_keep=3")
        keep = max(1, int(pm.CFG["backup_keep"]))
        for dest in dests[-keep:]:
            self.assertTrue(os.path.isdir(dest), "最新几份备份不能被淘汰：%s" % dest)
            self.assertTrue(os.path.isfile(os.path.join(dest, "pypi.db")), dest)
            self.assertTrue(pm.inspect_backup(dest)["ok"], dest)

    def test_list_backups_reports_integrity(self):
        dest = self._backup("list_a", "list_b")
        info = pm.inspect_backup(dest)
        self.assertTrue(info["ok"], info)
        self.assertGreaterEqual(info["files_present"], 2)
        self.assertEqual(info["missing"], [])

        rows = pm.list_backups()
        self.assertIn(os.path.basename(dest), [r["name"] for r in rows])
        self.assertLessEqual(len(rows), 3, "应遵守 backup_keep=3")
        self.assertTrue(all(os.path.isdir(r["path"]) for r in rows))

    def test_restore_refuses_incomplete_backup(self):
        dest = self._backup("refuse_a", "refuse_b")
        os.unlink(os.path.join(dest, "packages", wheel("refuse_b")))

        info = pm.inspect_backup(dest)
        self.assertFalse(info["ok"])
        self.assertEqual(info["missing"], [wheel("refuse_b")])

        target = self._target()
        self._with_data(target)
        with self.assertRaises(RuntimeError) as ctx:
            pm.restore_backup(dest)
        self.assertIn("备份不完整", str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(target, "pypi.db")),
                         "被拒绝的恢复不应留下半成品")

    def test_restore_refuses_non_empty_target_without_force(self):
        dest = self._backup("guard_a")
        target = self._target()
        os.makedirs(target)
        with open(os.path.join(target, "pypi.db"), "wb") as f:
            f.write(b"keep me")
        self._with_data(target)

        with self.assertRaises(RuntimeError) as ctx:
            pm.restore_backup(dest)
        self.assertIn("已存在数据库", str(ctx.exception))
        with open(os.path.join(target, "pypi.db"), "rb") as f:
            self.assertEqual(f.read(), b"keep me", "拒绝恢复时不能动现场")

    def test_force_restore_archives_and_switches(self):
        dest = self._backup("arch_a", "arch_b")
        target = self._target()
        os.makedirs(os.path.join(target, "packages"))
        with open(os.path.join(target, "pypi.db"), "wb") as f:
            f.write(b"broken db")
        with open(os.path.join(target, "packages", "old-0.1-py3-none-any.whl"), "wb") as f:
            f.write(b"old")

        self._with_data(target)
        result = pm.restore_backup(dest, force=True)

        self.assertEqual(result["root"], os.path.abspath(target))
        self.assertTrue(result["archived"])
        self.assertTrue(os.path.basename(result["archived"]).startswith(".rollback-"))
        # 事故现场被完整搬进归档，可随时取回
        with open(os.path.join(result["archived"], "pypi.db"), "rb") as f:
            self.assertEqual(f.read(), b"broken db")
        self.assertTrue(os.path.isfile(os.path.join(
            result["archived"], "packages", "old-0.1-py3-none-any.whl")))
        # 恢复后的库可用且包含备份里的包
        conn = sqlite3.connect(os.path.join(target, "pypi.db"))
        names = [r[0] for r in conn.execute("SELECT filename FROM files WHERE deleted=0")]
        conn.close()
        self.assertIn(wheel("arch_a"), names)
        self.assertIn(wheel("arch_b"), names)

    def test_force_restore_incomplete_reports_missing(self):
        dest = self._backup("inc_a", "inc_b")
        os.unlink(os.path.join(dest, "packages", wheel("inc_b")))
        target = self._target()
        self._with_data(target)

        result = pm.restore_backup(dest, force=True)
        self.assertTrue(result["forced"])
        self.assertEqual(result["missing"], [wheel("inc_b")])
        self.assertGreaterEqual(result["copied"], 1)

    def test_restore_failure_rolls_back_archive(self):
        """恢复中途失败（如磁盘满）必须把归档放回，不能留下半个数据目录"""
        dest = self._backup("rb_a")
        target = self._target()
        os.makedirs(target)
        with open(os.path.join(target, "pypi.db"), "wb") as f:
            f.write(b"broken db")
        self._with_data(target)

        real_copy2 = shutil.copy2
        calls = {"n": 0}

        def flaky(src, dst, *a, **kw):
            calls["n"] += 1
            if calls["n"] >= 2:      # 第 1 个是 pypi.db，随后复制包文件时失败
                raise OSError("No space left on device")
            return real_copy2(src, dst, *a, **kw)

        with mock.patch("pypi_manager.services.storage_service.shutil.copy2",
                        side_effect=flaky):
            with self.assertRaises(OSError):
                pm.restore_backup(dest, force=True)

        self.assertTrue(os.path.isfile(os.path.join(target, "pypi.db")),
                        "归档数据应被放回原位")
        with open(os.path.join(target, "pypi.db"), "rb") as f:
            self.assertEqual(f.read(), b"broken db")
        self.assertEqual([d for d in os.listdir(target) if d.startswith(".rollback-")],
                         [], "回滚完成后不应残留归档目录")


class TestDataSafety(ServerTestCase):
    def test_backup_creates_complete_snapshot(self):
        self.upload(wheel("backup_pkg"), auth=("admin", "admin-pass"))
        status, body, _ = self.request("POST", "/api/backup", b"", ("admin", "admin-pass"))
        self.assertEqual(status, 200)
        dest = json.loads(body)["backup"]
        self.assertTrue(os.path.isfile(os.path.join(dest, "pypi.db")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "manifest.json")))
        self.assertTrue(os.path.isfile(os.path.join(dest, "packages", wheel("backup_pkg"))))

    def test_backup_requires_admin(self):
        status, _, _ = self.request("POST", "/api/backup", b"", ("alice", "alice-pass"))
        self.assertEqual(status, 403)

    def test_reconcile_marks_missing_and_quarantines_orphans(self):
        fn = wheel("recon_pkg")
        self.assertEqual(self.upload(fn)[0], 200)
        os.unlink(os.path.join(pm.paths()["packages"], fn))
        orphan = wheel("orphan_pkg")
        with open(os.path.join(pm.paths()["packages"], orphan), "wb") as f:
            f.write(EMPTY_WHEEL)
        report = pm.reconcile_storage()
        self.assertGreaterEqual(report["missing_marked"], 1)
        self.assertGreaterEqual(report["orphans_moved"], 1)
        self.assertTrue(os.path.isfile(os.path.join(pm.paths()["orphans"], orphan)))
        conn = pm.db()
        row = conn.execute("SELECT deleted FROM files WHERE filename=?", (fn,)).fetchone()
        conn.close()
        self.assertEqual(row["deleted"], 1)

    def test_graceful_shutdown_completes(self):
        srv = pm.LimitedThreadingHTTPServer(("127.0.0.1", 0), pm.Handler, max_workers=4)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        started = time.time()
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertLess(time.time() - started, 5)


class TestUserAdmin(ServerTestCase):
    """POST /api/users 用户管理：仅 admin、建号、改角色、重置密码、删除"""

    def post_json(self, payload, auth=("admin", "admin-pass")):
        body = json.dumps(payload).encode("utf-8")
        return self.request("POST", "/api/users", body, auth,
                            {"Content-Type": "application/json"})

    def test_list_users_requires_admin(self):
        status, body, _ = self.request("GET", "/api/users", auth=("admin", "admin-pass"))
        self.assertEqual(status, 200)
        data = json.loads(body)
        names = [u["name"] for u in data["items"]]
        self.assertIn("admin", names)
        self.assertIn("alice", names)
        self.assertNotIn("pwhash", data["items"][0])
        self.assertNotIn("salt", data["items"][0])
        self.assertEqual(data["roles"], ["admin", "uploader", "viewer"])

        status, _, _ = self.request("GET", "/api/users", auth=("bob", "bob-pass"))
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/api/users", auth=("alice", "alice-pass"))
        self.assertEqual(status, 403)
        status, _, _ = self.request("GET", "/api/users")
        self.assertEqual(status, 401)

    def test_create_user_then_login(self):
        status, body, _ = self.post_json(
            {"name": "carol", "password": "carol-pass-1", "role": "viewer"})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(body)["action"], "created")
        status, _, _ = self.request("GET", "/api/whoami", auth=("carol", "carol-pass-1"))
        self.assertEqual(status, 200)
        # viewer 不能上传
        self.assertEqual(self.upload(wheel("carol_pkg"), auth=("carol", "carol-pass-1"))[0], 403)

    def test_create_without_password_uses_default(self):
        """不传密码时用初始密码 12345678，并标记 pwd_default"""
        status, body, _ = self.post_json({"name": "nopass", "role": "viewer"})
        self.assertEqual(status, 201)
        self.assertTrue(json.loads(body)["user"]["pwd_default"])
        self.assertEqual(
            self.request("GET", "/api/whoami", auth=("nopass", DEFAULT_PASSWORD))[0], 200)
        status, body, _ = self.post_json(
            {"name": "weak", "password": "123", "role": "viewer"})
        self.assertEqual(status, 400)
        self.assertIn("8", json.loads(body)["error"])

    def test_batch_create_users(self):
        """批量建号：换行/逗号/空格分隔，重名跳过，非法名单独报错"""
        body = json.dumps({
            "text": "u1\nu2, u3  u4\nu1\nbad:name",
            "role": "uploader", "scope": "team-*"
        }).encode("utf-8")
        status, raw, _ = self.request("POST", "/api/users/batch", body,
                                      ("admin", "admin-pass"),
                                      {"Content-Type": "application/json"})
        self.assertEqual(status, 201)
        data = json.loads(raw)
        self.assertEqual(data["created"], ["u1", "u2", "u3", "u4"])
        self.assertEqual(data["invalid"][0]["name"], "bad:name")
        self.assertEqual(data["password"], DEFAULT_PASSWORD)
        # 初始密码可登录，角色与范围生效（uploader + team-* 可上传 team 前缀的包）
        self.assertEqual(self.request("GET", "/api/whoami", auth=("u1", DEFAULT_PASSWORD))[0], 200)
        self.assertEqual(self.upload(wheel("team_pkg"), auth=("u1", DEFAULT_PASSWORD))[0], 200)
        self.assertEqual(self.upload(wheel("other_pkg"), auth=("u1", DEFAULT_PASSWORD))[0], 403)
        # 再跑一次：全部跳过，不覆盖已有密码
        status, raw, _ = self.request("POST", "/api/users/batch", body,
                                      ("admin", "admin-pass"),
                                      {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["created"], [])
        self.assertEqual(sorted(json.loads(raw)["skipped"]), ["u1", "u2", "u3", "u4"])

    def test_batch_create_requires_admin_and_names(self):
        self.assertEqual(
            self.request("POST", "/api/users/batch", b'{"text":"x1"}', ("bob", "bob-pass"),
                         {"Content-Type": "application/json"})[0], 403)
        self.assertEqual(
            self.request("POST", "/api/users/batch", b'{"text":"  "}',
                         ("admin", "admin-pass"),
                         {"Content-Type": "application/json"})[0], 400)

    def test_user_changes_own_password(self):
        """用户自助改密：校验原密码，改完旧密码失效、pwd_default 清除"""
        self.post_json({"name": "ivan", "role": "viewer"})
        self.assertEqual(self.request("GET", "/api/whoami", auth=("ivan", DEFAULT_PASSWORD))[0], 200)

        body = json.dumps({"old_password": "wrong-pass", "new_password": "ivan-pass-1"})
        status, raw, _ = self.request("POST", "/api/password", body.encode(),
                                      ("ivan", DEFAULT_PASSWORD),
                                      {"Content-Type": "application/json"})
        self.assertEqual(status, 400)
        self.assertIn("当前密码", json.loads(raw)["error"])

        body = json.dumps({"old_password": DEFAULT_PASSWORD, "new_password": "ivan-pass-1"})
        status, _, _ = self.request("POST", "/api/password", body.encode(),
                                    ("ivan", DEFAULT_PASSWORD),
                                    {"Content-Type": "application/json"})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/api/whoami", auth=("ivan", "ivan-pass-1"))[0], 200)
        self.assertEqual(self.request("GET", "/api/whoami", auth=("ivan", DEFAULT_PASSWORD))[0], 401)
        # 改成非初始密码后 pwd_default 归零
        _, raw, _ = self.request("GET", "/api/whoami", auth=("ivan", "ivan-pass-1"))
        self.assertFalse(json.loads(raw)["pwd_default"])

    def test_admin_reset_to_default_password(self):
        """管理员重置为初始密码：reset_password=true 即恢复 12345678"""
        self.post_json({"name": "judy", "password": "judy-pass-1", "role": "viewer"})
        _, raw, _ = self.request("GET", "/api/whoami", auth=("judy", "judy-pass-1"))
        self.assertFalse(json.loads(raw)["pwd_default"])
        status, body, _ = self.post_json({"name": "judy", "reset_password": True,
                                          "role": "viewer", "scope": "*"})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["user"]["pwd_default"])
        self.assertEqual(self.request("GET", "/api/whoami", auth=("judy", DEFAULT_PASSWORD))[0], 200)
        self.assertEqual(self.request("GET", "/api/whoami", auth=("judy", "judy-pass-1"))[0], 401)

    def test_password_endpoint_requires_login(self):
        self.assertEqual(
            self.request("POST", "/api/password", b'{"new_password":"x-pass-12"}')[0], 401)

    def test_bad_role_and_bad_name_rejected(self):
        status, _, _ = self.post_json(
            {"name": "dave", "password": "dave-pass-1", "role": "root"})
        self.assertEqual(status, 400)
        status, _, _ = self.post_json(
            {"name": "a:b", "password": "dave-pass-1", "role": "viewer"})
        self.assertEqual(status, 400)

    def test_change_role_promotes_viewer_to_uploader(self):
        self.post_json({"name": "erin", "password": "erin-pass-1", "role": "viewer"})
        self.assertEqual(self.upload(wheel("erin_pkg1"), auth=("erin", "erin-pass-1"))[0], 403)
        status, body, _ = self.post_json(
            {"name": "erin", "role": "uploader", "scope": "erin-*"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["user"]["role"], "uploader")
        # 改角色后立即可上传（认证缓存已失效）；wheel 名里的 _ 会规范成 - 再匹配 scope
        self.assertEqual(self.upload(wheel("erin_team"), auth=("erin", "erin-pass-1"))[0], 200)
        self.assertEqual(self.upload(wheel("other_team"), auth=("erin", "erin-pass-1"))[0], 403)

    def test_reset_password(self):
        self.post_json({"name": "frank", "password": "frank-pass-1", "role": "viewer"})
        status, _, _ = self.post_json(
            {"name": "frank", "password": "frank-pass-2", "role": "viewer"})
        self.assertEqual(status, 200)
        self.assertEqual(
            self.request("GET", "/api/whoami", auth=("frank", "frank-pass-2"))[0], 200)
        self.assertEqual(
            self.request("GET", "/api/whoami", auth=("frank", "frank-pass-1"))[0], 401)

    def test_delete_user(self):
        self.post_json({"name": "grace", "password": "grace-pass-1", "role": "viewer"})
        status, _, _ = self.request("DELETE", "/api/users/grace", auth=("admin", "admin-pass"))
        self.assertEqual(status, 200)
        self.assertEqual(
            self.request("GET", "/api/whoami", auth=("grace", "grace-pass-1"))[0], 401)
        status, _, _ = self.request("DELETE", "/api/users/ghost", auth=("admin", "admin-pass"))
        self.assertEqual(status, 404)

    def test_cannot_delete_self_or_last_admin(self):
        status, body, _ = self.request("DELETE", "/api/users/admin", auth=("admin", "admin-pass"))
        self.assertEqual(status, 400)
        self.assertIn("当前登录", json.loads(body)["error"])
        # 把 admin 降级为 uploader 会失去唯一管理员，应被拒绝
        status, body, _ = self.post_json({"name": "admin", "role": "uploader"})
        self.assertEqual(status, 400)
        self.assertIn("至少保留一个管理员", json.loads(body)["error"])

    def test_non_admin_cannot_create(self):
        status, _, _ = self.post_json(
            {"name": "hacker", "password": "hacker-pass", "role": "admin"},
            auth=("bob", "bob-pass"))
        self.assertEqual(status, 403)
        self.assertEqual(
            self.request("GET", "/api/whoami", auth=("hacker", "hacker-pass"))[0], 401)


class TestTrash(ServerTestCase):
    """回收站：覆盖保留旧版本、管理员彻底删除/清空、恢复冲突、容量兜底"""

    def _upload(self, filename, body=EMPTY_WHEEL, auth=("admin", "admin-pass")):
        return self.upload(filename, body, auth)

    def _trash_files(self, filename):
        return [f for f in os.listdir(pm.paths()["trash"]) if f.endswith("__" + filename)]

    def _rows(self, filename):
        conn = pm.db()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT id, deleted, deleted_reason FROM files WHERE filename=? ORDER BY id",
                (filename,))]
        finally:
            conn.close()

    def test_upload_by_path(self):
        fn = wheel("path_pkg")
        status, body, _ = self.request("PUT", "/api/upload/" + quote(fn), EMPTY_WHEEL,
                                       ("admin", "admin-pass"))
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["filename"], fn)

    def test_upload_by_path_rejects_empty_name(self):
        status, _, _ = self.request("PUT", "/api/upload/", EMPTY_WHEEL, ("admin", "admin-pass"))
        self.assertEqual(status, 400)

    def test_overwrite_keeps_old_version_in_trash(self):
        fn = wheel("dup_pkg")
        self.assertEqual(self._upload(fn, make_wheel("dup_pkg"))[0], 200)
        # 同名不同内容：旧版本进回收站，旧行保留并标记 replace
        status, body, _ = self._upload(fn, make_wheel("dup_pkg", extra="changed"))
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["replaced"])
        rows = self._rows(fn)
        self.assertEqual([r["deleted"] for r in rows], [1, 0])
        self.assertEqual(rows[0]["deleted_reason"], "replace")
        self.assertEqual(len(self._trash_files(fn)), 1)

    def test_restore_conflict_when_same_name_in_use(self):
        fn = wheel("conflict_pkg")
        self._upload(fn, make_wheel("conflict_pkg"))
        status, body, _ = self.request("DELETE", "/api/files/%d" %
                                       self._rows(fn)[0]["id"], b"", ("admin", "admin-pass"))
        self.assertEqual(status, 200)
        self._upload(fn, make_wheel("conflict_pkg", extra="v2"))
        old_id = self._rows(fn)[0]["id"]
        status, body, _ = self.request("POST", "/api/files/%d" % old_id, b"",
                                       ("admin", "admin-pass"))
        self.assertEqual(status, 409)
        self.assertIn("同名文件", json.loads(body)["error"])

    def test_only_admin_can_purge(self):
        fn = wheel("team_purge_rbac")
        self._upload(fn, make_wheel("team_purge_rbac"), ("alice", "alice-pass"))
        fid = self._rows(fn)[0]["id"]
        self.request("DELETE", "/api/files/%d" % fid, b"", ("alice", "alice-pass"))
        status, _, _ = self.request("DELETE", "/api/files/%d/purge" % fid, b"",
                                    ("alice", "alice-pass"))
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/trash/empty", b"", ("alice", "alice-pass"))
        self.assertEqual(status, 403)

    def test_purge_removes_file_and_row(self):
        fn = wheel("purge_pkg")
        self._upload(fn, make_wheel("purge_pkg"))
        fid = self._rows(fn)[0]["id"]
        # 未删除的文件必须先删除才能彻底删除
        status, _, _ = self.request("DELETE", "/api/files/%d/purge" % fid, b"",
                                    ("admin", "admin-pass"))
        self.assertEqual(status, 409)
        self.request("DELETE", "/api/files/%d" % fid, b"", ("admin", "admin-pass"))
        status, body, _ = self.request("DELETE", "/api/files/%d/purge" % fid, b"",
                                       ("admin", "admin-pass"))
        self.assertEqual(status, 200)
        self.assertEqual(self._rows(fn), [])
        self.assertEqual(self._trash_files(fn), [])

    def test_empty_trash(self):
        fn = wheel("empty_pkg")
        self._upload(fn, make_wheel("empty_pkg"))
        fid = self._rows(fn)[0]["id"]
        self.request("DELETE", "/api/files/%d" % fid, b"", ("admin", "admin-pass"))
        status, body, _ = self.request("POST", "/api/trash/empty", b"",
                                       ("admin", "admin-pass"))
        self.assertEqual(status, 200)
        self.assertEqual(self._rows(fn), [])
        self.assertEqual(self._trash_files(fn), [])

    def test_list_default_excludes_trash_rows(self):
        """按需加载契约：deleted=0 只回在用行，但统计与回收站概览仍是全库口径"""
        fn_live = wheel("lazy_pkg", "1.0")
        fn_old = wheel("lazy_pkg", "0.9")
        self._upload(fn_live, make_wheel("lazy_pkg"))
        self._upload(fn_old, make_wheel("lazy_pkg", extra="old"))
        old_id = self._rows(fn_old)[0]["id"]
        self.assertEqual(self.request("DELETE", "/api/files/%d" % old_id, b"",
                                       ("admin", "admin-pass"))[0], 200)

        def listed(query, auth=("admin", "admin-pass")):
            status, body, _ = self.request("GET", "/api/packages" + query, None, auth)
            self.assertEqual(status, 200)
            return body, json.loads(body)

        def mine(payload):
            return [f for p in payload["projects"] if p["name"] == "lazy-pkg"
                    for f in p["files"]]

        live_body, live = listed("?deleted=0")
        _, default = listed("")            # 不带参数时后端默认同样是 deleted=0
        all_body, all_rows = listed("?deleted=all")

        self.assertEqual([f["filename"] for f in mine(live)], [fn_live])
        self.assertFalse(mine(live)[0]["deleted"])
        self.assertEqual(len(mine(default)), 1)
        self.assertEqual(len(mine(all_rows)), 2)
        self.assertEqual(sum(1 for f in mine(all_rows) if f["deleted"]), 1)
        self.assertLess(len(live_body), len(all_body))

        # 看板数字与回收站概览不随 deleted 参数变化，否则默认视图会显示错误统计
        for key in ("files", "projects", "wheel", "sdist", "size_h"):
            self.assertEqual(live["stats"][key], all_rows["stats"][key])
        self.assertGreaterEqual(live["stats"]["trash"]["count"], 1)
        self.assertEqual(live["stats"]["trash"], all_rows["stats"]["trash"])

        # 非管理员走另一条统计分支，同样不应受 deleted 参数影响
        _, bob_live = listed("?deleted=0", ("bob", "bob-pass"))
        _, bob_all = listed("?deleted=all", ("bob", "bob-pass"))
        self.assertGreater(bob_live["stats"]["files"], 0)
        self.assertEqual(bob_live["stats"]["files"], bob_all["stats"]["files"])
        self.assertEqual(len(mine(bob_live)), 1)
        self.assertEqual(len(mine(bob_all)), 2)

    def test_cleanup_trash_by_capacity(self):
        from pypi_manager.services.storage_service import cleanup_trash
        keep_days, keep_mb = pm.CFG["trash_days"], pm.CFG["trash_max_mb"]
        big = wheel("cap_big")
        size = 2 * 1024 * 1024
        try:
            pm.CFG["trash_days"] = 0          # 只测容量兜底
            pm.CFG["trash_max_mb"] = 1
            with open(os.path.join(pm.paths()["trash"], "20200101000000__" + big), "wb") as f:
                f.write(b"0" * size)
            conn = pm.db()
            conn.execute(
                "INSERT INTO files(filename, project, display_name, version, sha256, size, "
                "uploaded_at, uploaded_by, deleted, deleted_reason) "
                "VALUES(?,?,?,?,?,?,?,?,1,'delete')",
                (big, "cap-big", "cap_big", "1.0", "z", size, "2020-01-01T00:00:00+08:00", "admin"))
            conn.commit()
            conn.close()

            report = cleanup_trash()
            self.assertGreaterEqual(report["files"], 1)
            self.assertGreaterEqual(report["rows"], 1)
            self.assertEqual(self._rows(big), [])
            self.assertEqual(self._trash_files(big), [])
        finally:
            pm.CFG["trash_days"] = keep_days
            pm.CFG["trash_max_mb"] = keep_mb


class LoggingTests(unittest.TestCase):
    """日志契约：请求上下文、级别升级、慢请求标记、JSON 字段稳定。"""

    def setUp(self):
        self.buf = io.StringIO()
        configure(level="DEBUG", json_mode=False, stream=self.buf)
        self.addCleanup(configure, "INFO", False, io.StringIO())

    def _handler(self, status, elapsed=0.001, path="/api/packages", size=512):
        """伪造一个 handler 实例：只测 log_request 的判定逻辑，不发真实请求。"""
        h = object.__new__(Handler)
        h.requestline = f"GET {path} HTTP/1.1"
        h.command = "GET"
        h.path = path
        h.client_address = ("10.0.0.9", 54321)
        h._resp_bytes = size
        h._started_at = time.monotonic() - elapsed
        h.log_request(status)
        return self.buf.getvalue()

    def test_text_line_has_request_id_and_context(self):
        set_request("req12345")
        set_user("alice")
        get_logger("unit").info("hello %s", "world")
        out = self.buf.getvalue()
        self.assertIn("[req12345]", out)
        self.assertIn("INFO", out)
        self.assertIn("hello world", out)
        clear_request()
        self.assertEqual(current_request_id(), "-")

    def test_access_log_levels_and_slow_flag(self):
        self.assertIn("INFO", self._handler(200))
        self.assertNotIn("[slow]", self._handler(200))
        self.assertIn("WARNING", self._handler(403))
        self.assertIn("ERROR", self._handler(500))
        slow = self._handler(200, elapsed=2.0)
        self.assertIn("WARNING", slow)
        self.assertIn("[slow]", slow)

    def test_access_log_has_ip_status_bytes_duration(self):
        out = self._handler(200, elapsed=0.05, size=484321)
        self.assertIn("10.0.0.9", out)
        self.assertIn('"GET /api/packages HTTP/1.1" 200 484321', out)
        self.assertRegex(out, r"\d+\.\dms")

    def test_json_mode_fields(self):
        self.buf = io.StringIO()
        configure(level="INFO", json_mode=True, stream=self.buf)
        set_request("json0001")
        set_user("bob")
        get_logger("access").info("request done", extra={
            "status": 200, "bytes": 10, "duration_ms": 3.2, "slow": False})
        rec = json.loads(self.buf.getvalue().strip())
        self.assertEqual(rec["level"], "INFO")
        self.assertEqual(rec["request_id"], "json0001")
        self.assertEqual(rec["user"], "bob")
        self.assertEqual(rec["status"], 200)
        self.assertEqual(rec["duration_ms"], 3.2)
        self.assertEqual(rec["msg"], "request done")
        self.assertIn("ts", rec)


class TestErrorLogging(ServerTestCase):
    """500 必须同时满足：响应体带错误编号、审计可按该编号反查、日志不泄露堆栈给前端。"""

    def test_500_has_request_id_and_audit_record(self):
        from pypi_manager.services import package_service
        original = package_service.list_packages

        def boom(*args, **kwargs):
            raise RuntimeError("injected failure")

        package_service.list_packages = boom
        try:
            status, body, _ = self.request("GET", "/api/packages", auth=("admin", "admin-pass"))
        finally:
            package_service.list_packages = original

        self.assertEqual(status, 500)
        payload = json.loads(body)
        self.assertNotIn("injected failure", payload["error"])   # 堆栈不外泄
        self.assertIn("错误编号", payload["error"])
        rid = payload["error"].split("错误编号 ")[1].split("）")[0]
        self.assertRegex(rid, r"^[0-9a-f]{8}$")

        records = []
        with open(pm.paths()["audit"], encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        hits = [r for r in records
                if r.get("action") == "server_error" and r.get("error_id") == rid]
        self.assertTrue(hits, "审计日志缺少 server_error 记录，错误编号无法反查")
        self.assertEqual(hits[-1]["user"], "admin")
        self.assertIn("/api/packages", hits[-1]["target"])


class ReadPermissionArgsTest(unittest.TestCase):
    """锁定 --allow-anonymous-read 真正生效：它曾声明但从未被读取（空转参数）。"""

    def setUp(self):
        self._snapshot = dict(CFG)   # apply_args 会整体改写 CFG，测完必须还原

    def tearDown(self):
        CFG.clear()
        CFG.update(self._snapshot)

    def _open_read(self, argv):
        apply_args(parse_args(argv))
        return CFG["open_read"]

    def test_default_is_anonymous_read(self):
        self.assertIs(self._open_read([]), True)

    def test_allow_flag_is_effective(self):
        self.assertIs(self._open_read(["--allow-anonymous-read"]), True)

    def test_require_auth_flag_closes_reads(self):
        self.assertIs(self._open_read(["--require-auth-for-read"]), False)

    def test_flags_are_mutually_exclusive(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as ctx:
                parse_args(["--allow-anonymous-read", "--require-auth-for-read"])
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("not allowed with", err.getvalue())
        self.assertIs(CFG["open_read"], True)   # 解析失败不得改动已有配置


if __name__ == "__main__":
    unittest.main()
