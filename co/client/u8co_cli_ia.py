"""存货核算命令：ia-post --fiscal-year Y --period P [--unpost] [--on-uncosted refuse|skip] 和
ia-period-end --fiscal-year Y --period P [--cancel]，都可带 [--dry-run] [--idempotency-key K]。由 u8co_cli_gl_arc 并入。

--dry-run、--idempotency-key 由 u8co_cli_assist.add_dry_flags 统一加（两个命令都在 WRITE_COMMANDS 里）。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_ia import IA_UNCOSTED, IaMonth

POST_COMMAND = "ia-post"
PERIOD_END_COMMAND = "ia-period-end"


def _month_args(cmd: argparse.ArgumentParser) -> None:
    _add_auth(cmd)
    cmd.add_argument("--fiscal-year", required=True, type=int)
    cmd.add_argument("--period", required=True, type=int, choices=range(1, 13), metavar="1-12")


def add_commands(sub: Any) -> None:
    post = sub.add_parser(POST_COMMAND)
    _month_args(post)
    post.add_argument("--unpost", action="store_true", help="恢复记账（不能和 --on-uncosted 一起用）")
    post.add_argument(
        "--on-uncosted", choices=IA_UNCOSTED, help="U8 无法确定成本的存货：refuse 整笔拒绝（缺省），skip 这些存货不记账"
    )
    end = sub.add_parser(PERIOD_END_COMMAND)
    _month_args(end)
    end.add_argument("--cancel", action="store_true", help="取消期末处理")


def _cmd_post(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    if parsed.unpost and parsed.on_uncosted:
        raise SystemExit("--on-uncosted 不能和 --unpost 一起用")
    action = "unpost" if parsed.unpost else "post"
    return client.ia_post(call, IaMonth(parsed.fiscal_year, parsed.period, action, parsed.on_uncosted or ""))


def _cmd_period_end(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    action = "cancel" if parsed.cancel else "run"
    return client.ia_period_end(call, IaMonth(parsed.fiscal_year, parsed.period, action))


HANDLERS = {POST_COMMAND: _cmd_post, PERIOD_END_COMMAND: _cmd_period_end}
