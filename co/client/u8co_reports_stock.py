"""库存与销售支持报表的查询条件和正文：库存台账 stock_ledger、收发存汇总表 stock_summary、货位存量 position_stock、
批次存量 batch_stock、客户信用 customer_credit、价格表 price_list（都在 /v1/reports/ 下）。

方法在 U8CoReportsMixin（report_stock_ledger 等）。字段和取值范围与桥的约定一致；桥还会再查一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common, _put_optional, _text, _ymd
from co.client.u8co_reports import _code_prefix, _paging, _put_flag

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

PRICE_KINDS = ("customer", "inventory", "vendor")


@dataclass(frozen=True)
class StockLedgerQuery:
    """库存台账。date_to 空时桥取 date。"""

    inv: str
    date_from: str
    date_to: str = ""
    wh: str = ""
    batch: str = ""
    include_unverified: bool | None = None
    after: str = ""
    limit: int | None = None


@dataclass(frozen=True)
class StockSummaryQuery:
    """收发存汇总表。by_wh、nonzero 缺省 true（不发送）。"""

    date_from: str
    date_to: str = ""
    wh: str = ""
    inv: str = ""
    inv_class: str = ""
    by_wh: bool | None = None
    include_unverified: bool | None = None
    nonzero: bool | None = None
    after: str = ""
    limit: int | None = None


@dataclass(frozen=True)
class StockPlaceQuery:
    """货位存量（position）和批次存量（expiring_before）。position 只用于货位存量，expiring_before 只用于批次存量。"""

    wh: str = ""
    inv: str = ""
    batch: str = ""
    position: str = ""
    expiring_before: str = ""
    nonzero: bool | None = None
    after: str = ""
    limit: int | None = None


@dataclass(frozen=True)
class CustomerCreditQuery:
    customer: tuple[str, ...] | None = None
    controlled_only: bool | None = None
    after: str = ""
    limit: int | None = None


@dataclass(frozen=True)
class PriceListQuery:
    """价格表。customer 只能和 kind=customer、vendor 只能和 kind=vendor 一起用；as_of 与 all_dates 二选一。"""

    kind: str
    customer: str = ""
    vendor: str = ""
    inv: str = ""
    as_of: str = ""
    all_dates: bool | None = None
    after: str = ""
    limit: int | None = None


def _range(fields: dict[str, Any], low: str, high: str) -> None:
    fields["date_from"] = _ymd(low, "date_from")
    _put_optional(fields, "date_to", high and _ymd(high, "date_to"))
    if high and low > high:
        raise ValueError("date_from 不能晚于 date_to")


def _word(fields: dict[str, Any], key: str, value: str) -> None:
    _put_optional(fields, key, value and _text(value, key, 60))


def stock_ledger_fields(call: U8Call, query: StockLedgerQuery) -> dict[str, Any]:
    fields = _common(call)
    fields["inv"] = _text(query.inv, "存货编码 inv", 60)
    _word(fields, "wh", query.wh)
    _word(fields, "batch", query.batch)
    _range(fields, query.date_from, query.date_to)
    _put_flag(fields, "include_unverified", query.include_unverified)
    _paging(fields, query.after, query.limit, 1000)
    return fields


def stock_summary_fields(call: U8Call, query: StockSummaryQuery) -> dict[str, Any]:
    fields = _common(call)
    _range(fields, query.date_from, query.date_to)
    _word(fields, "wh", query.wh)
    _word(fields, "inv", query.inv)
    _put_optional(fields, "inv_class", query.inv_class and _code_prefix(query.inv_class, "inv_class"))
    _put_flag(fields, "by_wh", query.by_wh)
    _put_flag(fields, "include_unverified", query.include_unverified)
    _put_flag(fields, "nonzero", query.nonzero)
    _paging(fields, query.after, query.limit, 1000)
    return fields


def stock_place_fields(call: U8Call, query: StockPlaceQuery, batch: bool) -> dict[str, Any]:
    if batch and query.position:
        raise ValueError("批次存量不能带 position")
    if not batch and query.expiring_before:
        raise ValueError("货位存量不能带 expiring_before")
    fields = _common(call)
    _word(fields, "wh", query.wh)
    _word(fields, "inv", query.inv)
    _word(fields, "batch", query.batch)
    _put_optional(fields, "position", query.position and _code_prefix(query.position, "position"))
    _put_optional(fields, "expiring_before", query.expiring_before and _ymd(query.expiring_before, "expiring_before"))
    _put_flag(fields, "nonzero", query.nonzero)
    _paging(fields, query.after, query.limit, 1000)
    return fields


def customer_credit_fields(call: U8Call, query: CustomerCreditQuery) -> dict[str, Any]:
    fields = _common(call)
    if query.customer is not None:
        if not isinstance(query.customer, (list, tuple)) or not 1 <= len(query.customer) <= 20:
            raise ValueError("customer 必须是 1 到 20 个客户编码")
        fields["customer"] = [_text(code, "客户编码 customer", 60) for code in query.customer]
    _put_flag(fields, "controlled_only", query.controlled_only)
    _paging(fields, query.after, query.limit, 200)
    return fields


def price_list_fields(call: U8Call, query: PriceListQuery) -> dict[str, Any]:
    if query.kind not in PRICE_KINDS:
        raise ValueError("kind 只能是 " + "、".join(PRICE_KINDS))
    if query.customer and query.kind != "customer":
        raise ValueError("customer 只能和 kind=customer 一起用")
    if query.vendor and query.kind != "vendor":
        raise ValueError("vendor 只能和 kind=vendor 一起用")
    if query.as_of and query.all_dates:
        raise ValueError("as_of 不能和 all_dates 一起用")
    fields = _common(call)
    fields["kind"] = query.kind
    _word(fields, "customer", query.customer)
    _word(fields, "vendor", query.vendor)
    _word(fields, "inv", query.inv)
    _put_optional(fields, "as_of", query.as_of and _ymd(query.as_of, "as_of"))
    _put_flag(fields, "all_dates", query.all_dates)
    _paging(fields, query.after, query.limit, 1000)
    return fields
