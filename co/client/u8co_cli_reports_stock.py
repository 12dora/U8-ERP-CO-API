"""库存与销售支持报表命令：report-stock-ledger、report-stock-summary、report-position-stock、report-batch-stock、
report-customer-credit、report-price-list。由 u8co_cli_reports 并入。"""

from __future__ import annotations

import argparse
from typing import Any

from co.client.u8co_cli import _add_auth
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_reports_stock import (
    PRICE_KINDS,
    CustomerCreditQuery,
    PriceListQuery,
    StockLedgerQuery,
    StockPlaceQuery,
    StockSummaryQuery,
)


def _page(cmd: argparse.ArgumentParser) -> None:
    cmd.add_argument("--after", default="")
    cmd.add_argument("--limit", type=int)


def add_commands(sub: Any) -> None:
    ledger = sub.add_parser("report-stock-ledger")
    _add_auth(ledger)
    ledger.add_argument("--inv", required=True)
    ledger.add_argument("--wh", default="")
    ledger.add_argument("--batch", default="")
    summary = sub.add_parser("report-stock-summary")
    _add_auth(summary)
    summary.add_argument("--wh", default="")
    summary.add_argument("--inv", default="")
    summary.add_argument("--inv-class", default="", metavar="PREFIX", help="存货分类编码前缀")
    summary.add_argument("--no-wh", action="store_true", help="每个存货一行，不按仓库分")
    summary.add_argument("--include-zero", action="store_true", help="保留期初、入库、出库都为 0 的行")
    for cmd in (ledger, summary):
        cmd.add_argument("--date-from", required=True)
        cmd.add_argument("--date-to", default="")
        cmd.add_argument("--include-unverified", action="store_true", help="连未审核的收发记录一起算")
        _page(cmd)
    _place_commands(sub)
    _sales_commands(sub)


def _place_commands(sub: Any) -> None:
    for name in ("report-position-stock", "report-batch-stock"):
        cmd = sub.add_parser(name)
        _add_auth(cmd)
        cmd.add_argument("--wh", default="")
        cmd.add_argument("--inv", default="")
        cmd.add_argument("--batch", default="")
        cmd.add_argument("--include-zero", action="store_true", help="保留数量为 0 的行")
        if name == "report-position-stock":
            cmd.add_argument("--position", default="", metavar="PREFIX", help="货位编码前缀")
        else:
            cmd.add_argument("--expiring-before", default="", metavar="YYYY-MM-DD", help="失效日期不晚于这一天")
        _page(cmd)


def _sales_commands(sub: Any) -> None:
    credit = sub.add_parser("report-customer-credit")
    _add_auth(credit)
    credit.add_argument("--customer", action="append", metavar="CODE", help="客户编码，可重复")
    credit.add_argument("--controlled-only", action="store_true", help="只列受信用额度控制的客户")
    _page(credit)
    price = sub.add_parser("report-price-list")
    _add_auth(price)
    price.add_argument("--kind", required=True, choices=PRICE_KINDS)
    price.add_argument("--customer", default="")
    price.add_argument("--vendor", default="")
    price.add_argument("--inv", default="")
    price.add_argument("--as-of", default="")
    price.add_argument("--all-dates", action="store_true", help="不按日期过滤")
    _page(price)


def _true(value: bool) -> bool | None:
    # 没给开关就不发送，由桥取缺省值。
    return True if value else None


def _false(value: bool) -> bool | None:
    return False if value else None


def stock_ledger_query(parsed: argparse.Namespace) -> StockLedgerQuery:
    return StockLedgerQuery(
        parsed.inv,
        parsed.date_from,
        date_to=parsed.date_to,
        wh=parsed.wh,
        batch=parsed.batch,
        include_unverified=_true(parsed.include_unverified),
        after=parsed.after,
        limit=parsed.limit,
    )


def stock_summary_query(parsed: argparse.Namespace) -> StockSummaryQuery:
    return StockSummaryQuery(
        parsed.date_from,
        date_to=parsed.date_to,
        wh=parsed.wh,
        inv=parsed.inv,
        inv_class=parsed.inv_class,
        by_wh=_false(parsed.no_wh),
        include_unverified=_true(parsed.include_unverified),
        nonzero=_false(parsed.include_zero),
        after=parsed.after,
        limit=parsed.limit,
    )


def stock_place_query(parsed: argparse.Namespace) -> StockPlaceQuery:
    return StockPlaceQuery(
        wh=parsed.wh,
        inv=parsed.inv,
        batch=parsed.batch,
        position=getattr(parsed, "position", ""),
        expiring_before=getattr(parsed, "expiring_before", ""),
        nonzero=_false(parsed.include_zero),
        after=parsed.after,
        limit=parsed.limit,
    )


def _cmd_ledger(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.report_stock_ledger(call, stock_ledger_query(parsed))


def _cmd_summary(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    return client.report_stock_summary(call, stock_summary_query(parsed))


def _cmd_place(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    if parsed.command == "report-batch-stock":
        return client.report_batch_stock(call, stock_place_query(parsed))
    return client.report_position_stock(call, stock_place_query(parsed))


def _cmd_credit(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    customers = None if parsed.customer is None else tuple(parsed.customer)
    query = CustomerCreditQuery(customers, _true(parsed.controlled_only), parsed.after, parsed.limit)
    return client.report_customer_credit(call, query)


def _cmd_price(client: U8CoClient, call: U8Call, parsed: argparse.Namespace) -> dict[str, Any]:
    query = PriceListQuery(
        parsed.kind,
        customer=parsed.customer,
        vendor=parsed.vendor,
        inv=parsed.inv,
        as_of=parsed.as_of,
        all_dates=_true(parsed.all_dates),
        after=parsed.after,
        limit=parsed.limit,
    )
    return client.report_price_list(call, query)


HANDLERS = {
    "report-stock-ledger": _cmd_ledger,
    "report-stock-summary": _cmd_summary,
    "report-position-stock": _cmd_place,
    "report-batch-stock": _cmd_place,
    "report-customer-credit": _cmd_credit,
    "report-price-list": _cmd_price,
}
