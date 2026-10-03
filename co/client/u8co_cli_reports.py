"""命令行：只读报表 report-*。"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_cli_open import HANDLERS as OPEN_HANDLERS
from co.client.u8co_cli_open import add_commands as add_open_commands
from co.client.u8co_cli_reports_detail import HANDLERS as DETAIL_HANDLERS
from co.client.u8co_cli_reports_detail import add_commands as add_detail_commands
from co.client.u8co_cli_reports_trace import HANDLERS as TRACE_HANDLERS
from co.client.u8co_cli_reports_trace import add_commands as add_trace_commands
from co.client.u8co_cli_reports_stock import HANDLERS as STOCK_HANDLERS
from co.client.u8co_cli_reports_stock import add_commands as add_stock_commands
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_reports import (
    AGING_BASES,
    AGING_GROUPS,
    AUX_DIMS,
    SIDES,
    ArapQuery,
    BomQuery,
    GlAuxQuery,
    GlBalanceQuery,
)


def add_commands(sub: Any) -> None:
    close = sub.add_parser("report-close-status")
    _add_auth(close)
    close.add_argument("--fiscal-year", type=int)
    _gl_commands(sub)
    _arap_commands(sub)
    bom = sub.add_parser("report-bom")
    _add_auth(bom)
    bom.add_argument("--parent", required=True)
    bom.add_argument("--as-of", default="")
    bom.add_argument("--levels", type=int)
    bom.add_argument("--limit", type=int)
    add_detail_commands(sub)
    add_trace_commands(sub)
    add_stock_commands(sub)
    add_open_commands(sub)


def _period_args(cmd: argparse.ArgumentParser) -> None:
    _add_auth(cmd)
    cmd.add_argument("--fiscal-year", type=int)
    cmd.add_argument("--period-from", required=True, type=int)
    cmd.add_argument("--period-to", required=True, type=int)
    cmd.add_argument("--code-prefix", default="")
    cmd.add_argument("--nonzero", action="store_true")
    cmd.add_argument("--after", default="")
    cmd.add_argument("--limit", type=int)


def _gl_commands(sub: Any) -> None:
    balance = sub.add_parser("report-gl-balance")
    _period_args(balance)
    balance.add_argument("--grade-from", type=int)
    balance.add_argument("--grade-to", type=int)
    balance.add_argument("--leaf-only", action="store_true")
    balance.add_argument("--include-unposted", action="store_true")
    aux = sub.add_parser("report-gl-aux")
    _period_args(aux)
    aux.add_argument("--dim", required=True, choices=AUX_DIMS)
    aux.add_argument("--dim-code", default="")
    aux.add_argument("--project-class", default="")


def _arap_commands(sub: Any) -> None:
    for name in ("report-arap-balance", "report-arap-aging"):
        cmd = sub.add_parser(name)
        _add_auth(cmd)
        cmd.add_argument("--side", required=True, choices=SIDES)
        cmd.add_argument("--as-of", default="")
        cmd.add_argument("--account", action="append", metavar="PREFIX", help="控制科目前缀，可重复")
        cmd.add_argument("--exclude-account", action="append", metavar="PREFIX", help="排除的科目前缀，可重复")
        cmd.add_argument("--partner", default="")
        cmd.add_argument("--include-zero", action="store_true", help="保留余额为 0 的往来单位")
        cmd.add_argument("--after", default="")
        cmd.add_argument("--limit", type=int)
        if name == "report-arap-aging":
            cmd.add_argument("--basis", choices=AGING_BASES, default="")
            cmd.add_argument("--buckets", metavar="30,60,90", help="账龄区间上限天数，逗号分隔，严格递增")
            cmd.add_argument("--group-by", choices=AGING_GROUPS, default="", help="分组，缺省 partner")
            cmd.add_argument("--person", action="append", metavar="CODE", help="业务员编码，可重复")
            cmd.add_argument("--overdue-only", action="store_true", help="只留有逾期金额且余额大于 0 的行（要 --basis due）")
            cmd.add_argument(
                "--default-credit-days", type=int, metavar="DAYS",
                help="没有收款日期、信用期为 0 的单据按起算日加这么多天算到期日（0 到 3650，要 --basis due）",
            )


def parse_buckets(text: str | None) -> tuple[int, ...] | None:
    if text is None:
        return None
    parts = [part.strip() for part in text.split(",")]
    if not parts or not all(part.isascii() and part.isdecimal() for part in parts):
        raise SystemExit("--buckets 要写成逗号分隔的天数，例如 30,60,90")
    return tuple(int(part) for part in parts)


def _optional_tuple(values: list[str] | None) -> tuple[str, ...] | None:
    return None if values is None else tuple(values)


def _flag(value: bool) -> bool | None:
    # 没给开关就不发送，由桥取缺省值。
    return True if value else None


def _aging_args(parsed: argparse.Namespace) -> dict[str, Any]:
    if parsed.command != "report-arap-aging":
        return {}
    return {
        "basis": parsed.basis,
        "buckets": parse_buckets(parsed.buckets),
        "group_by": parsed.group_by,
        "person": _optional_tuple(parsed.person),
        "overdue_only": _flag(parsed.overdue_only),
        "default_credit_days": parsed.default_credit_days,
    }


def arap_query(parsed: argparse.Namespace) -> ArapQuery:
    return ArapQuery(
        side=parsed.side,
        as_of=parsed.as_of,
        accounts=_optional_tuple(parsed.account),
        exclude_accounts=_optional_tuple(parsed.exclude_account),
        partner=parsed.partner,
        nonzero=False if parsed.include_zero else None,
        after=parsed.after,
        limit=parsed.limit,
        **_aging_args(parsed),
    )


def _cmd_close(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.report_close_status(call, parsed.fiscal_year)


def _cmd_gl_balance(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    query = GlBalanceQuery(
        parsed.period_from,
        parsed.period_to,
        fiscal_year=parsed.fiscal_year,
        grade_from=parsed.grade_from,
        grade_to=parsed.grade_to,
        code_prefix=parsed.code_prefix,
        leaf_only=_flag(parsed.leaf_only),
        include_unposted=_flag(parsed.include_unposted),
        nonzero=_flag(parsed.nonzero),
        after=parsed.after,
        limit=parsed.limit,
    )
    return client.report_gl_balance(call, query)


def _cmd_gl_aux(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    query = GlAuxQuery(
        parsed.dim,
        parsed.period_from,
        parsed.period_to,
        fiscal_year=parsed.fiscal_year,
        code_prefix=parsed.code_prefix,
        dim_code=parsed.dim_code,
        project_class=parsed.project_class,
        nonzero=_flag(parsed.nonzero),
        after=parsed.after,
        limit=parsed.limit,
    )
    return client.report_gl_aux_balance(call, query)


def _cmd_arap(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    query = arap_query(parsed)
    if parsed.command == "report-arap-aging":
        return client.report_arap_aging(call, query)
    return client.report_arap_balance(call, query)


def _cmd_bom(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    query = BomQuery(parsed.parent, parsed.as_of, parsed.levels, parsed.limit)
    return client.report_bom(call, query)


HANDLERS = {
    "report-close-status": _cmd_close,
    "report-gl-balance": _cmd_gl_balance,
    "report-gl-aux": _cmd_gl_aux,
    "report-arap-balance": _cmd_arap,
    "report-arap-aging": _cmd_arap,
    "report-bom": _cmd_bom,
}

# 明细账命令（report-arap-detail、report-gl-detail）在 u8co_cli_reports_detail.py。
HANDLERS.update(DETAIL_HANDLERS)
# 订单执行、单据追溯命令（report-order-exec、report-doc-trace）在 u8co_cli_reports_trace.py。
HANDLERS.update(TRACE_HANDLERS)
# 库存与销售支持报表命令（report-stock-ledger 等）在 u8co_cli_reports_stock.py。
HANDLERS.update(STOCK_HANDLERS)
# 期初余额、账套体检与附件列表命令（report-opening-balance、report-account-readiness 等）在 u8co_cli_open.py。
HANDLERS.update(OPEN_HANDLERS)
