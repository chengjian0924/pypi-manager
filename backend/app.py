#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pypi-manager 可执行入口：装配配置、存储、服务与路由并启动。"""
from __future__ import annotations

import os
import signal
import ssl
import sys
import threading

from pypi_manager.config import CFG, apply_args, parse_args
from pypi_manager.constants import APP_NAME, DEFAULT_PASSWORD, STOP, VERSION
from pypi_manager.data import database
from pypi_manager.data.repositories import user_repo
from pypi_manager.log import configure, get_logger
from pypi_manager.middleware.server import LimitedThreadingHTTPServer
from pypi_manager.routes.handler import Handler
from pypi_manager.services.auth_service import create_user
from pypi_manager.services.storage_service import (backup_now, list_backups,
                                                   reconcile_storage,
                                                   restore_backup, start_backup_loop,
                                                   start_trash_loop)
from pypi_manager.utils.naming import human_size

_log = get_logger("app")


def _print_backups():
    """打印备份点清单：时间、包文件数、占用、完整性。只读，不碰数据目录。"""
    root = CFG.get("backup_dir") or ""
    if not root:
        sys.exit("未配置备份目录（--backup-dir），无法列出备份")
    rows = list_backups()
    if not rows:
        print(f"备份目录 {os.path.abspath(root)} 下暂无备份")
        return
    print(f"备份目录 {os.path.abspath(root)}（共 {len(rows)} 份，自动保留 {CFG['backup_keep']} 份）")
    for r in rows:
        if r["ok"]:
            state = "完整"
        elif r["missing"]:
            state = f"缺 {len(r['missing'])} 个包（恢复会被拒绝，除非 --force）"
        else:
            state = f"库校验失败（{r['db_check']}）"
        print(f"  {r['name']}  创建 {r['created'][:19]}  "
              f"{r['files_present']} 个包  {human_size(r['size'])}  {state}")
    bad = sum(1 for r in rows if not r["ok"])
    if bad:
        print(f"\n其中 {bad} 份备份不完整，不建议作为回滚点。")


def main():
    args = parse_args()
    apply_args(args)
    configure(level=CFG["log_level"], json_mode=CFG["log_json"])

    if args.list_backups and args.restore:
        sys.exit("--list-backups 与 --restore 不能同时使用")
    if args.force and not args.restore:
        sys.exit("--force 只能与 --restore 一起使用")

    if args.list_backups:
        _print_backups()
        return

    if args.restore:
        try:
            result = restore_backup(args.restore, force=args.force)
        except Exception as e:
            sys.exit(f"恢复失败: {e}")
        print(f"恢复完成: {result['root']}（{result['copied']} 个文件，请检查后重新启动服务）")
        if result["archived"]:
            print(f"原数据已归档到 {result['archived']}（确认无误后可手动删除）")
        if result["missing"]:
            print(f"⚠ 备份缺 {len(result['missing'])} 个包文件，已按 --force 强制恢复；"
                  "服务启动后这些包会被标记为丢失")
        return

    if args.tls_cert and not os.path.isfile(args.tls_cert):
        sys.exit(f"TLS 证书不存在: {args.tls_cert}")
    if args.tls_key and not os.path.isfile(args.tls_key):
        sys.exit(f"TLS 私钥不存在: {args.tls_key}")

    try:
        database.init_storage()
    except OSError as e:
        sys.exit(f"无法初始化数据目录 {os.path.abspath(CFG['data'])}：{e}\n"
                 "请用 --data 指定当前用户可写的目录，例如：\n"
                 "  python3 backend/app.py --data ~/pypi-data ...\n"
                 "（注意：macOS 根文件系统为只读，不能使用 /data）")
    report = reconcile_storage()
    if (report["missing_marked"] or report["orphans_moved"] or report["tmp_removed"]
            or report["trash_removed"]):
        _log.info("启动对账：清理临时文件 %s，标记丢失 %s，隔离孤儿文件 %s，清理回收站 %s",
                  report["tmp_removed"], report["missing_marked"],
                  report["orphans_moved"], report["trash_removed"])
    if report["db_check"] != "ok":
        _log.warning("数据库完整性检查异常：%s", report["db_check"])

    if args.init_user:
        # 支持两种写法：NAME（用初始密码 12345678）与 NAME:PASS（指定密码）
        u, sep, p = args.init_user.partition(":")
        u = u.strip()
        try:
            create_user(u, p.strip() if sep else "", args.role, args.scope)
        except ValueError as e:
            sys.exit(str(e))
        # 不把明文密码写进 stdout/journald：账号信息与口令分离告知
        if p.strip():
            secret = "已设置为命令行指定的密码（不回显）"
        else:
            secret = f"初始密码 {DEFAULT_PASSWORD}（首次登录后请立即修改）"
        print(f"账号已就绪: {u}（角色 {args.role}，范围 {args.scope}），{secret}")
        return

    if args.backup_now:
        try:
            print(f"备份完成: {backup_now()}")
        except Exception as e:
            sys.exit(f"备份失败: {e}")
        return

    conn = database.db()
    n = user_repo.count_users(conn)
    conn.close()
    if n == 0:
        _log.warning("还没有任何账号，上传/删除会无法执行。"
                     "请加 --init-user 管理员名:密码 再启动。")

    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    if not args.tls_cert and not loopback and not args.insecure_http:
        sys.exit("安全启动检查未通过：未配置 TLS，却要监听非本机地址。\n"
                 "请使用 --tls-cert/--tls-key 启用 HTTPS；\n"
                 "若已在前置反向代理终止 TLS，请绑定 127.0.0.1；\n"
                 "确需明文监听请显式加 --insecure-http（不推荐）。")

    srv = LimitedThreadingHTTPServer((CFG["host"], CFG["port"]), Handler,
                                     max_workers=CFG["max_workers"])
    scheme = "http"
    if args.tls_cert:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(args.tls_cert, args.tls_key or None)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        CFG["tls"] = True
        CFG["tls_cert"] = args.tls_cert
        scheme = "https"
    else:
        _log.warning("当前为明文 HTTP（建议仅用于本机或前置反向代理场景）")

    start_backup_loop()
    start_trash_loop()

    def _stop(signum, frame):
        if STOP.is_set():
            return
        STOP.set()
        _log.info("收到停止信号，等待 %s 个在途请求完成…", srv.active())
        threading.Thread(target=srv.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    _log.info("%s v%s 已启动", APP_NAME, VERSION)
    _log.info("管理界面 %s://%s:%s/", scheme, CFG["host"], CFG["port"])
    _log.info("pip 源   %s://%s:%s/simple/", scheme, CFG["host"], CFG["port"])
    _log.info("数据目录 %s", os.path.abspath(CFG["data"]))
    if CFG["open_read"]:
        _log.info("读权限   免登录（pip 无需凭证，收紧请加 --require-auth-for-read）")
    else:
        _log.info("读权限   需登录（pip 需配置 .netrc）")
    _log.info("并发上限 %s，单文件上限 %s，日志级别 %s%s",
              CFG["max_workers"], human_size(CFG["max_upload"]),
              CFG["log_level"], "（JSON）" if CFG["log_json"] else "")
    if CFG["backup_dir"]:
        _log.info("备份目录 %s（间隔 %sh，保留 %s 份）",
                  os.path.abspath(CFG["backup_dir"]),
                  CFG["backup_interval_hours"], CFG["backup_keep"])
    try:
        srv.serve_forever(poll_interval=0.5)
    finally:
        srv.server_close()
        _log.info("已停止")


if __name__ == "__main__":
    main()
