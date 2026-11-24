"""存货核算制单命令（测试账套）：
ia-voucher (--id TYPE:ID ... | --month M [--types 09,11]) [--merge per_bill|all] [--sign 转] [--digest 文本]
[--voucher-date yyyy-MM-dd]
ia-voucher-delete --pz-id 2026IA0000000000001

由 u8co_cli_gl_arc 并入。--dry-run、--idempotency-key 由 u8co_cli_assist.add_dry_flags 统一加（命令在 WRITE_COMMANDS 里）。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_ia_voucher import IA_VOUCHER_MERGES, IaVoucherAsk

VOUCHER_COMMAND = "ia-voucher"
DELETE_COMMAND = "ia-voucher-delete"


def add_commands(sub: Any) -> None:
    cmd = sub.add_parser(VOUCHER_COMMAND)
    _add_auth(cmd)
    cmd.add_argument(
        "--id", action="append", default=[], metavar="TYPE:ID", help="按单据选：存货核算单据类型:来源单据表头主键，可重复"
    )
    cmd.add_argument("--month", type=int, help="按月份选：登录年度该月已记账、未制单的单据")
    cmd.add_argument("--types", help="只和 --month 一起用：逗号分隔的单据类型，如 09,11")
    cmd.add_argument("--merge", choices=IA_VOUCHER_MERGES, help="per_bill 一张单据一张凭证（缺省），all 合成一张")
    cmd.add_argument("--sign", help="凭证类别，缺省取最近一张存货核算凭证的类别")
    cmd.add_argument("--digest", help="摘要，缺省取单据备注或单据类型名")
    cmd.add_argument("--voucher-date", help="制单日期 yyyy-MM-dd，缺省登录日期")
    drop = sub.add_parser(DELETE_COMMAND)
    _add_auth(drop)
    drop.add_argument("--pz-id", required=True, help="外部业务号（ia-voucher 响应里的 pz_id）")


def _ref(text: str) -> tuple[str, int]:
    kind, sep, raw = text.partition(":")
    if not sep or not raw.isdigit():
        raise SystemExit(f"--id 必须是 TYPE:ID，如 09:1001（收到 {text}）")
    return kind, int(raw)


def _ask(parsed: argparse.Namespace) -> IaVoucherAsk:
    types = tuple(t.strip() for t in parsed.types.split(",") if t.strip()) if parsed.types else ()
    return IaVoucherAsk(
        ids=tuple(_ref(text) for text in parsed.id),
        month=parsed.month,
        types=types,
        merge=parsed.merge or "",
        sign=parsed.sign or "",
        digest=parsed.digest or "",
        voucher_date=parsed.voucher_date or "",
    )


def _cmd_voucher(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.ia_voucher(call, _ask(parsed))


def _cmd_delete(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.ia_voucher_delete(call, parsed.pz_id)


HANDLERS = {VOUCHER_COMMAND: _cmd_voucher, DELETE_COMMAND: _cmd_delete}
