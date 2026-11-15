"""期初余额、账套体检与附件列表命令：report-opening-balance、report-account-readiness、gl-attachments、attachments。
由 u8co_cli_reports 并入。
"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_kinds import KIND_NAMES
from co.client.u8co_open import OPENING_MODULES, OpeningQuery
from co.client.u8co_gl_arc import GlKey
from co.client.u8co_reports import AUX_DIMS, SIDES


def add_commands(sub: Any) -> None:
    opening = sub.add_parser("report-opening-balance")
    _add_auth(opening)
    opening.add_argument("--module", required=True, choices=OPENING_MODULES)
    opening.add_argument("--side", default="", choices=("", *SIDES), help="module=arap 时必填")
    opening.add_argument("--fiscal-year", type=int, help="只用于 module=gl")
    opening.add_argument("--wh", default="")
    opening.add_argument("--inv", default="")
    opening.add_argument("--batch", default="")
    opening.add_argument("--partner", default="")
    opening.add_argument("--code-prefix", default="")
    opening.add_argument("--leaf-only", action="store_true")
    opening.add_argument("--dim", default="", choices=("", *AUX_DIMS), help="module=gl 按辅助项列期初")
    opening.add_argument("--include-zero", action="store_true", help="保留期初为 0 的行")
    opening.add_argument("--after", default="")
    opening.add_argument("--limit", type=int)
    ready = sub.add_parser("report-account-readiness")
    _add_auth(ready)
    ready.add_argument("--as-of", default="", help="检查年度和工作日历用的日期 yyyy-MM-dd，缺省为登录日期")
    gl = sub.add_parser("gl-attachments")
    _add_auth(gl)
    gl.add_argument("--period", required=True, type=int)
    gl.add_argument("--sign", required=True)
    gl.add_argument("--no", required=True, type=int)
    files = sub.add_parser("attachments")
    _add_auth(files)
    files.add_argument("--type", required=True, choices=KIND_NAMES)
    files.add_argument("--id", required=True, type=int)


def opening_query(parsed: argparse.Namespace) -> OpeningQuery:
    return OpeningQuery(
        parsed.module,
        side=parsed.side,
        fiscal_year=parsed.fiscal_year,
        wh=parsed.wh,
        inv=parsed.inv,
        batch=parsed.batch,
        partner=parsed.partner,
        code_prefix=parsed.code_prefix,
        leaf_only=True if parsed.leaf_only else None,
        dim=parsed.dim,
        nonzero=False if parsed.include_zero else None,
        after=parsed.after,
        limit=parsed.limit,
    )


def _cmd_opening(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.report_opening_balance(call, opening_query(parsed))


def _cmd_ready(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.report_account_readiness(call, parsed.as_of)


def _cmd_gl_files(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.gl_attachments(call, GlKey(parsed.period, parsed.sign, parsed.no))


def _cmd_files(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.voucher_attachments(call, parsed.type)


HANDLERS = {
    "report-opening-balance": _cmd_opening,
    "report-account-readiness": _cmd_ready,
    "gl-attachments": _cmd_gl_files,
    "attachments": _cmd_files,
}
