"""明细账命令：report-arap-detail（往来明细账）、report-gl-detail（科目明细账）。由 u8co_cli_reports 并入。"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_reports import SIDES
from co.client.u8co_reports_detail import DETAIL_BASES, ArapDetailQuery, GlDetailQuery


def add_commands(sub: Any) -> None:
    arap = sub.add_parser("report-arap-detail")
    _add_auth(arap)
    arap.add_argument("--side", required=True, choices=SIDES)
    arap.add_argument("--partner", required=True, action="append", metavar="CODE", help="往来单位编码，可重复")
    arap.add_argument("--date-from", required=True)
    arap.add_argument("--date-to", default="")
    arap.add_argument("--basis", choices=DETAIL_BASES, default="", help="日期口径，缺省 register（登记日期）")
    arap.add_argument("--account", action="append", metavar="PREFIX", help="控制科目前缀，可重复")
    arap.add_argument("--exclude-account", action="append", metavar="PREFIX", help="排除的科目前缀，可重复")
    arap.add_argument("--dept", default="")
    arap.add_argument("--person", default="")
    arap.add_argument("--include-writeoff", action="store_true", help="列出核销行")
    arap.add_argument("--after", default="")
    arap.add_argument("--limit", type=int)
    gl = sub.add_parser("report-gl-detail")
    _add_auth(gl)
    gl.add_argument("--code", required=True)
    gl.add_argument("--period-from", type=int)
    gl.add_argument("--period-to", type=int)
    gl.add_argument("--date-from", default="")
    gl.add_argument("--date-to", default="")
    gl.add_argument("--fiscal-year", type=int)
    gl.add_argument("--exact", action="store_true", help="只查 code 本身，不含下级科目")
    gl.add_argument("--include-unposted", action="store_true")
    for key in ("customer", "vendor", "dept", "person", "project", "project-class"):
        gl.add_argument("--" + key, default="")
    gl.add_argument("--after", default="")
    gl.add_argument("--limit", type=int)


def _optional_tuple(values: list[str] | None) -> tuple[str, ...] | None:
    return None if values is None else tuple(values)


def arap_detail_query(parsed: argparse.Namespace) -> ArapDetailQuery:
    partners = tuple(parsed.partner)
    return ArapDetailQuery(
        side=parsed.side,
        partner=partners[0] if len(partners) == 1 else partners,
        date_from=parsed.date_from,
        date_to=parsed.date_to,
        basis=parsed.basis,
        accounts=_optional_tuple(parsed.account),
        exclude_accounts=_optional_tuple(parsed.exclude_account),
        dept=parsed.dept,
        person=parsed.person,
        include_writeoff=True if parsed.include_writeoff else None,
        after=parsed.after,
        limit=parsed.limit,
    )


def gl_detail_query(parsed: argparse.Namespace) -> GlDetailQuery:
    return GlDetailQuery(
        parsed.code,
        period_from=parsed.period_from,
        period_to=parsed.period_to,
        date_from=parsed.date_from,
        date_to=parsed.date_to,
        fiscal_year=parsed.fiscal_year,
        include_sub=False if parsed.exact else None,
        include_unposted=True if parsed.include_unposted else None,
        customer=parsed.customer,
        vendor=parsed.vendor,
        dept=parsed.dept,
        person=parsed.person,
        project=parsed.project,
        project_class=parsed.project_class,
        after=parsed.after,
        limit=parsed.limit,
    )


def _cmd_arap_detail(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.report_arap_detail(call, arap_detail_query(parsed))


def _cmd_gl_detail(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.report_gl_detail(call, gl_detail_query(parsed))


HANDLERS = {
    "report-arap-detail": _cmd_arap_detail,
    "report-gl-detail": _cmd_gl_detail,
}
