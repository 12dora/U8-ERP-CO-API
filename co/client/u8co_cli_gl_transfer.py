"""总账自动转账命令（测试账套）：
gl-transfer-pnl --fiscal-year Y --period P [--voucher-date yyyy-MM-dd] [--exclude-existing]
gl-transfer-custom --fiscal-year Y --period P [--tran-id 0001] [--voucher-date yyyy-MM-dd] [--exclude-existing]

由 u8co_cli_gl_arc 并入。--dry-run、--idempotency-key 由 u8co_cli_assist.add_dry_flags 统一加（命令在 WRITE_COMMANDS 里）；
--exclude-existing 只能和 --dry-run 一起用（桥 400）。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_gl_transfer import GlTransferAsk

PNL_COMMAND = "gl-transfer-pnl"
CUSTOM_COMMAND = "gl-transfer-custom"


def add_commands(sub: Any) -> None:
    for name in (PNL_COMMAND, CUSTOM_COMMAND):
        cmd = sub.add_parser(name)
        _add_auth(cmd)
        cmd.add_argument("--fiscal-year", required=True, type=int, help="会计年度，必须是登录日期的年份")
        cmd.add_argument("--period", required=True, type=int, help="会计期间 1 到 12")
        cmd.add_argument("--voucher-date", help="凭证日期 yyyy-MM-dd，缺省取该期间最后一天")
        cmd.add_argument("--exclude-existing", action="store_true", help="核对模式：余额里去掉本期已生成的结转凭证（只能预演）")
        if name == CUSTOM_COMMAND:
            cmd.add_argument("--tran-id", help="只生成这一个转账序号，缺省全部")


def _ask(parsed: argparse.Namespace) -> GlTransferAsk:
    return GlTransferAsk(
        parsed.fiscal_year,
        parsed.period,
        parsed.voucher_date,
        getattr(parsed, "tran_id", None),
        bool(parsed.exclude_existing),
    )


def _cmd_pnl(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.gl_transfer_pnl(call, _ask(parsed))


def _cmd_custom(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.gl_transfer_custom(call, _ask(parsed))


HANDLERS = {PNL_COMMAND: _cmd_pnl, CUSTOM_COMMAND: _cmd_custom}
