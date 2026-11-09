# -*- coding: utf-8 -*-
"""运行配置：CFG、数据目录布局、命令行参数解析与应用。"""
from __future__ import annotations

import argparse
import os

from .constants import MAX_UPLOAD, MAX_WORKERS, ROLES, TRASH_MAX_MB
from .log import LEVELS

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PROJECT_ROOT = os.path.dirname(_BACKEND_DIR)
DEFAULT_FRONTEND_DIR = os.path.join(_PROJECT_ROOT, "frontend")

CFG = {
    "data": "/data/pypi",
    "open_read": True,      # 读操作（pip install / 列表）是否免登录
    "port": 8080,
    "host": "0.0.0.0",
    "tls": False,
    "tls_cert": "",
    "tls_key": "",
    "max_upload": MAX_UPLOAD,
    "max_workers": MAX_WORKERS,
    "min_free_mb": 512,     # 磁盘剩余低于该值时拒绝上传并让 /health 降级
    "backup_dir": "",
    "backup_interval_hours": 0,
    "backup_keep": 7,
    "trash_days": 30,          # 回收站文件保留天数，超期自动清理
    "trash_max_mb": TRASH_MAX_MB,   # 回收站容量上限，超期之外再按容量从最旧开始清理
    "audit_max_mb": 10,        # 审计日志单文件上限，超过后轮转
    "audit_keep": 5,           # 审计日志轮转保留份数
    "log_level": "INFO",       # 运行日志级别：DEBUG/INFO/WARNING/ERROR/CRITICAL
    "log_json": False,         # 输出单行 JSON，便于 Loki/ES 采集；默认人读文本
    "slow_ms": 1000,           # 超过该耗时的请求按 WARNING 记录，便于找慢接口
    "frontend_dir": DEFAULT_FRONTEND_DIR,   # 静态前端目录（默认项目根/frontend）
}


def paths():
    d = os.path.abspath(CFG["data"])
    return {
        "root": d,
        "packages": os.path.join(d, "packages"),
        "trash": os.path.join(d, ".trash"),
        "tmp": os.path.join(d, ".tmp"),
        "orphans": os.path.join(d, ".orphans"),
        "db": os.path.join(d, "pypi.db"),
        "audit": os.path.join(d, "audit.jsonl"),
    }


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="内网 wheel 包管理器 + PEP 503 源（零依赖）")
    ap.add_argument("--data", default=CFG["data"], help="数据目录（默认 /data/pypi）")
    ap.add_argument("--host", default=CFG["host"])
    ap.add_argument("--port", type=int, default=CFG["port"])
    ap.add_argument("--init-user", metavar="USER[:PASS]",
                    help="创建或重置账号；省略密码时使用初始密码 12345678")
    ap.add_argument("--role", default="admin", choices=sorted(ROLES),
                    help="配合 --init-user 指定角色（默认 admin）")
    ap.add_argument("--scope", default="*",
                    help="配合 --init-user 指定项目范围，逗号分隔，支持通配符，如 'team-*'")
    # 读权限是一对互斥开关：默认免登录（pip 开箱即用），需要保密时用后者收紧。
    # 两者写入同一个 dest，apply_args 只认 open_read，避免出现“声明了却不生效”的开关。
    read_policy = ap.add_mutually_exclusive_group()
    read_policy.add_argument(
        "--allow-anonymous-read", dest="open_read", action="store_true", default=True,
        help="索引与下载免登录（pip 无需凭证），默认行为；与 --require-auth-for-read 二选一")
    read_policy.add_argument(
        "--require-auth-for-read", dest="open_read", action="store_false",
        help="读操作也需要登录（pip 需配 .netrc）")
    ap.add_argument("--tls-cert", help="TLS 证书路径（PEM）；配置后启用 HTTPS")
    ap.add_argument("--tls-key", help="TLS 私钥路径（PEM，可与证书同文件）")
    ap.add_argument("--insecure-http", action="store_true",
                    help="显式允许明文 HTTP 监听非本机地址（不推荐）")
    ap.add_argument("--max-upload-mb", type=int, default=MAX_UPLOAD // (1024 * 1024),
                    help="单文件上传上限（MB，默认 512）")
    ap.add_argument("--max-workers", type=int, default=MAX_WORKERS,
                    help="并发请求上限（默认 64，超出返回 503）")
    ap.add_argument("--min-free-mb", type=int, default=512,
                    help="磁盘剩余空间低于该值时拒绝上传（默认 512MB）")
    ap.add_argument("--backup-dir", default="", help="备份目录（启用备份功能）")
    ap.add_argument("--backup-interval-hours", type=int, default=0,
                    help="自动备份间隔小时数（0 表示只支持手动/API 触发）")
    ap.add_argument("--backup-keep", type=int, default=7, help="保留最近几份备份")
    ap.add_argument("--backup-now", action="store_true", help="立即执行一次备份后退出")
    ap.add_argument("--list-backups", action="store_true",
                    help="列出 --backup-dir 下各备份点的时间、大小与完整性后退出")
    ap.add_argument("--restore", metavar="BACKUP_DIR",
                    help="从备份目录恢复到 --data 指定目录（须先停止服务）")
    ap.add_argument("--force", action="store_true",
                    help="与 --restore 配合：目标已有数据时先归档再恢复；备份缺包时强行恢复")
    ap.add_argument("--trash-days", type=int, default=30,
                    help="回收站文件保留天数（启动对账时清理，0 表示不按时间清理）")
    ap.add_argument("--trash-max-mb", type=int, default=TRASH_MAX_MB,
                    help="回收站占用上限 MB，超出时从最旧开始清理（0 表示不限）")
    ap.add_argument("--audit-max-mb", type=int, default=10,
                    help="审计日志单文件上限 MB（超过后轮转，0 表示不轮转）")
    ap.add_argument("--audit-keep", type=int, default=5, help="审计日志轮转保留份数")
    ap.add_argument("--log-level", default="INFO", choices=sorted(LEVELS),
                    help="运行日志级别（默认 INFO）")
    ap.add_argument("--log-json", action="store_true",
                    help="运行日志输出单行 JSON（便于日志采集），默认人类可读文本")
    ap.add_argument("--slow-ms", type=int, default=1000,
                    help="请求耗时超过该毫秒数时按 WARNING 记录（默认 1000）")
    ap.add_argument("--frontend-dir", default=DEFAULT_FRONTEND_DIR,
                    help="前端静态资源目录（默认项目根/frontend）")
    return ap.parse_args(argv)


def apply_args(args):
    """把命令行参数写入 CFG，默认值与原单文件实现保持一致。"""
    CFG["data"] = args.data
    CFG["host"] = args.host
    CFG["port"] = args.port
    CFG["open_read"] = bool(args.open_read)
    CFG["max_upload"] = max(1, args.max_upload_mb) * 1024 * 1024
    CFG["max_workers"] = max(1, args.max_workers)
    CFG["min_free_mb"] = max(0, args.min_free_mb)
    CFG["backup_dir"] = args.backup_dir
    CFG["backup_interval_hours"] = max(0, args.backup_interval_hours)
    CFG["backup_keep"] = max(1, args.backup_keep)
    CFG["trash_days"] = max(0, args.trash_days)
    CFG["trash_max_mb"] = max(0, args.trash_max_mb)
    CFG["audit_max_mb"] = max(0, args.audit_max_mb)
    CFG["audit_keep"] = max(1, args.audit_keep)
    CFG["log_level"] = str(args.log_level or "INFO").upper()
    CFG["log_json"] = bool(args.log_json)
    CFG["slow_ms"] = max(0, args.slow_ms)
    CFG["frontend_dir"] = args.frontend_dir
