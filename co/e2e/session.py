"""测试账套调试会话。python -m co.e2e.session。结束时必须 close，用快照还原。"""

from __future__ import annotations

import argparse
import signal
from pathlib import Path

from co.e2e import daytime
from co.e2e.lanelock import command_lock
from co.e2e.plan import clamp_minutes, session_watchdog_default
from co.e2e.remote import RemoteError, release_all
from co.e2e.session_call import call_session
from co.e2e.session_extend import EXTEND_DEFAULT, extend_session
from co.e2e.session_io import audit_session, sql_session
from co.e2e.session_ops import close_session, deploy_session, open_session, status_session
from co.e2e.session_reset import reset_session
from co.e2e.settings import ConfigError, conf


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="测试账套调试会话。close 用快照把测试账套的两个库还原。")
    sub = parser.add_subparsers(dest="cmd", required=True)
    opened = sub.add_parser("open", help="预检、指纹、快照、看门狗，服务保持运行")
    opened.add_argument("--watchdog-min", type=int, default=None, help="缺省取配置 watchdog.session_minutes（120），最多 120；之后可用 extend 延长")
    opened.add_argument(daytime.FLAG, action="store_true", help="配置了 business_hours 时仍在业务时间内打开")
    sub.add_parser("status", help="标记、快照、看门狗和服务状态")
    extend = sub.add_parser("extend", help="把看门狗到点时刻往后推，会话不关；会话从打开起最长 240 分钟")
    extend.add_argument("--minutes", type=int, default=EXTEND_DEFAULT, help="每次延长的分钟数，10 到 120，缺省 60")
    deploy = sub.add_parser(
        "deploy",
        help="编译并换上已安装服务的程序。服务器上的桥与本机源码一致时跳过，不重新编译、也不重启桥",
    )
    deploy.add_argument("--force-deploy", action="store_true", help="源码一致也重新编译、换程序并重启桥")
    sub.add_parser("reset", help="把测试账套还原到本次会话的快照，会话保持打开")
    close = sub.add_parser("close", help="停服务、还原、再启动，核对指纹")
    close.add_argument("--keep", action="store_true", help="不还原，保留造数结果作为新基线（只限 account.seedable 的通道）")
    call = sub.add_parser("call", help="用服务密钥调用桥")
    call.add_argument("path", help="health 或 v1/...")
    group = call.add_mutually_exclusive_group()
    group.add_argument("--json", dest="body", help="JSON 对象。口令字段会被丢掉")
    group.add_argument("--file", help="JSON 文件")
    sql = sub.add_parser("sql", help="在打开的会话里对测试账套的库执行 SQL")
    sql.add_argument("--db", default="", help="缺省是配置里的 UFDATA 库")
    sql_group = sql.add_mutually_exclusive_group(required=True)
    sql_group.add_argument("--file")
    sql_group.add_argument("--query")
    audit = sub.add_parser("audit", help="本次会话以来的服务审计")
    audit.add_argument("--since", default="", help="UTC 时间 yyyy-MM-ddTHH:mm:ssZ")
    return parser


def _sql_text(args: argparse.Namespace) -> str:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    return str(args.query or "")


def _open(args: argparse.Namespace) -> int:
    minutes = session_watchdog_default() if args.watchdog_min is None else args.watchdog_min
    # 业务时间护栏在任何远端动作之前（co/e2e/daytime.py），看门狗时限内会跨进业务时间也拒绝。
    refused = daytime.refusal(conf().business_hours, args.allow_daytime, False, minutes=clamp_minutes(minutes))
    if refused:
        print(refused)
        return 2
    return open_session(minutes)


def _run(args: argparse.Namespace) -> int:
    if args.cmd == "open":
        return _open(args)
    if args.cmd == "status":
        return status_session()
    if args.cmd == "extend":
        return extend_session(args.minutes)
    if args.cmd == "deploy":
        return deploy_session(force=args.force_deploy)
    if args.cmd == "close":
        return close_session(keep=args.keep)
    if args.cmd == "reset":
        return reset_session()
    if args.cmd == "call":
        return call_session(args.path, args.body, args.file)
    if args.cmd == "sql":
        return sql_session(args.db or conf().data_db, _sql_text(args))
    if args.cmd == "audit":
        return audit_session(args.since)
    raise RuntimeError("未知命令")


def _on_term(signum, frame) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, _on_term)
    try:
        args = _parser().parse_args(argv)
        # 同一条通道：open、deploy、close、reset、extend 独占，call、sql、audit 可以彼此并行（co/e2e/lanelock.py）。
        with command_lock(args.cmd):
            return _run(args)
    except KeyboardInterrupt:
        print("收到中断")
        return 1
    except (ConfigError, RuntimeError, OSError, RemoteError) as exc:
        print(str(exc)[:300])
        return 1
    finally:
        release_all()


if __name__ == "__main__":
    raise SystemExit(main())
