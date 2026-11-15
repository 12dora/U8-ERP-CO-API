"""总账红字冲销命令：gl-reverse --period P --sign S --no N [--fiscal-year Y] [--voucher-date yyyy-MM-dd]。

由 u8co_cli_gl_arc 并入。--dry-run、--idempotency-key 由 u8co_cli_assist.add_dry_flags 统一加（命令在 WRITE_COMMANDS 里）。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_gl_reverse import GlReverseAsk
from co.client.u8co_gl_arc import GlKey

REVERSE_COMMAND = "gl-reverse"


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser(REVERSE_COMMAND)
    _add_auth(cmd)
    cmd.add_argument("--period", required=True, type=int, help="原凭证的会计期间")
    cmd.add_argument("--sign", required=True, help="原凭证的类别字")
    cmd.add_argument("--no", required=True, type=int, help="原凭证号")
    cmd.add_argument("--fiscal-year", type=int, help="原凭证的会计年度，缺省取登录日期的年份")
    cmd.add_argument("--voucher-date", help="红字凭证日期 yyyy-MM-dd，缺省取登录日期")


def _cmd_reverse(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    ask = GlReverseAsk(GlKey(parsed.period, parsed.sign, parsed.no), parsed.fiscal_year, parsed.voucher_date)
    return client.gl_reverse(call, ask)


HANDLERS = {REVERSE_COMMAND: _cmd_reverse}
