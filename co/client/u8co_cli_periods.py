"""月末结账命令：periods-close --fiscal-year Y --period P (--module M | --through) [--reopen] [--dry-run]
[--idempotency-key K]。由 u8co_cli_gl_arc 并入。

--dry-run、--idempotency-key 由 u8co_cli_assist.add_dry_flags 统一加（periods-close 在 WRITE_COMMANDS 里）。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_periods import PERIOD_MODULES, PeriodClose

COMMAND = "periods-close"


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser(COMMAND)
    _add_auth(cmd)
    target = cmd.add_mutually_exclusive_group(required=True)
    target.add_argument("--module", choices=PERIOD_MODULES, help="结账的模块")
    target.add_argument("--through", action="store_true", help="各已启用模块从最早的年度起逐月结账到 --period")
    cmd.add_argument("--fiscal-year", required=True, type=int)
    cmd.add_argument("--period", required=True, type=int, choices=range(1, 13), metavar="1-12")
    cmd.add_argument("--reopen", action="store_true", help="取消结账（只能取消最后一个已结账的期间；不能和 --through 一起用）")


def _cmd_close(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    if parsed.through and parsed.reopen:
        raise SystemExit("--through 不能和 --reopen 一起用")
    req = PeriodClose(
        parsed.module or "", parsed.fiscal_year, parsed.period, "reopen" if parsed.reopen else "close", parsed.through
    )
    return client.close_period(call, req)


HANDLERS = {COMMAND: _cmd_close}
