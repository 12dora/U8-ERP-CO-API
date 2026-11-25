"""python -m co.e2e.seed plan|run [--only 步骤,…] [--skip 步骤,…]。只在打开的调试会话里、配置允许保留（account.seedable）的通道上运行。"""

from __future__ import annotations

import argparse
import signal
from pathlib import Path

from co.e2e.lanelock import session_lock
from co.e2e.markers import read_linux_marker
from co.e2e.remote import RemoteError, release_all
from co.e2e.seed import config, preflight
from co.e2e.seed.context import SeedContext, SeedError, live_link, refusal
from co.e2e.settings import ConfigError, conf


def _parser() -> argparse.ArgumentParser:
    # 这里不导入 runner：步骤模块导入时就取了编码，要等 seed 段的配置应用之后（config.apply）。
    parser = argparse.ArgumentParser(prog="python -m co.e2e.seed", description="经 API 给空的测试账套造基础档案和测试数据文件。")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd, text in (("plan", "只读：列出每一步已存在和待建的对象"), ("run", "按顺序执行，已存在的跳过")):
        item = sub.add_parser(cmd, help=text)
        item.add_argument("--only", default="", help="逗号分隔的步骤名（plan 列出全部步骤）")
        item.add_argument("--skip", default="", help="逗号分隔，跳过这些步骤（在 --only 之后再去掉）")
    return parser


def _start(args: argparse.Namespace) -> int:
    marker = read_linux_marker()
    refused = refusal(marker, conf())
    if refused or marker is None:
        print(refused or "没有打开的会话")
        return 1
    config.apply(conf().seed)
    from co.e2e.seed.runner import plan, run, select

    steps = select(args.only, args.skip)
    report = Path(str(marker["report_dir"]))
    ctx = SeedContext(report, live_link(report))
    # 和 session call 一样：先确认快照都在、看门狗还有时间。run 每一步前再查一次。
    ctx.link.window()
    # 编码方案和科目核对：只读，任何写入之前；不通过就停下（消息可能较长，不截断）。
    try:
        preflight.check(ctx)
    except SeedError as exc:
        print(str(exc))
        return 1
    if args.cmd == "plan":
        return plan(ctx, steps)
    return run(ctx, steps)


def _on_term(signum, frame) -> None:
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGTERM, _on_term)
    try:
        args = _parser().parse_args(argv)
        # 与 session call、sql 共享通道锁：可以并行，但不和 open、deploy、close 同时跑（co/e2e/lanelock.py）。
        with session_lock(False):
            return _start(args)
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
