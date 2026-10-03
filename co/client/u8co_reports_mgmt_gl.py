"""经营管理报表（总账口径）：/v1/reports/mgmt/pnl（损益发生额）、mgmt/meta（数据水位）、mgmt/cash_stock（资金存货）。
混入 U8CoClient。

字段和取值范围与桥一致；桥还会再查一遍。三张都是聚合结果，不翻页。科目缺省按 2007 企业会计准则一级科目
（损益类 6、本年利润 4103、货币资金 1001 / 1002 / 1012、应收票据 1121），科目体系不同的账套自己传。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common, _int, _put_optional
from co.client.u8co_reports import _code_prefix, _fiscal, _periods, _put_flag

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

MGMT_PNL_ROUTE = "/v1/reports/mgmt/pnl"
MGMT_META_ROUTE = "/v1/reports/mgmt/meta"
MGMT_CASH_ROUTE = "/v1/reports/mgmt/cash_stock"
PNL_DETAILS = ("prefix4", "leaf")
PNL_DIMS = ("dept", "item")
PURCHASE_SOURCES = ("auto", "invoice", "receipt")


@dataclass(frozen=True)
class MgmtPnlQuery:
    """损益发生额（去掉期间损益结转凭证）。None 或空的字段不发送，由桥取缺省值。"""

    period_from: int
    period_to: int
    fiscal_year: int | None = None
    include_unposted: bool | None = None
    detail: str = ""
    dims: tuple[str, ...] | None = None
    pl_accounts: tuple[str, ...] | None = None
    profit_account: str = ""


@dataclass(frozen=True)
class MgmtCashQuery:
    """一个会计期间的资金、应收票据、存货、产量与采购。top 1 到 500（桥缺省 200）。"""

    period: int
    fiscal_year: int | None = None
    cash_accounts: tuple[str, ...] | None = None
    notes_accounts: tuple[str, ...] | None = None
    top: int | None = None
    purchase_source: str = ""
    include_unverified: bool | None = None


class U8CoMgmtGlMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def report_mgmt_pnl(self, call: U8Call, query: MgmtPnlQuery) -> dict[str, Any]:
        return self.call(MGMT_PNL_ROUTE, mgmt_pnl_fields(call, query))

    def report_mgmt_meta(self, call: U8Call, fiscal_year: int | None = None) -> dict[str, Any]:
        fields = _common(call)
        _put_optional(fields, "fiscal_year", _fiscal(fiscal_year))
        return self.call(MGMT_META_ROUTE, fields)

    def report_mgmt_cash_stock(self, call: U8Call, query: MgmtCashQuery) -> dict[str, Any]:
        return self.call(MGMT_CASH_ROUTE, mgmt_cash_fields(call, query))


def _codes(values: object, label: str) -> list[str] | None:
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 10:
        raise ValueError(f"{label} 必须是 1 到 10 个科目编码前缀")
    return [_code_prefix(item, label) for item in values]


def _dims(values: object) -> list[str] | None:
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or len(values) > 2:
        raise ValueError("dims 只能是 dept、item 组成的数组（不重复）")
    if any(item not in PNL_DIMS for item in values) or len(set(values)) != len(values):
        raise ValueError("dims 只能是 dept、item 组成的数组（不重复）")
    return list(values)


def _put_list(fields: dict[str, Any], key: str, value: list[str] | None) -> None:
    if value is not None:
        fields[key] = value


def mgmt_pnl_fields(call: U8Call, query: MgmtPnlQuery) -> dict[str, Any]:
    if query.detail and query.detail not in PNL_DETAILS:
        raise ValueError("detail 只能是 prefix4 或 leaf")
    fields = _common(call)
    _put_optional(fields, "fiscal_year", _fiscal(query.fiscal_year))
    _periods(fields, query.period_from, query.period_to)
    _put_flag(fields, "include_unposted", query.include_unposted)
    _put_optional(fields, "detail", query.detail)
    _put_list(fields, "dims", _dims(query.dims))
    _put_list(fields, "pl_accounts", _codes(query.pl_accounts, "pl_accounts"))
    _put_optional(fields, "profit_account", query.profit_account and _code_prefix(query.profit_account, "profit_account"))
    return fields


def mgmt_cash_fields(call: U8Call, query: MgmtCashQuery) -> dict[str, Any]:
    if query.purchase_source and query.purchase_source not in PURCHASE_SOURCES:
        raise ValueError("purchase_source 只能是 auto、invoice 或 receipt")
    fields = _common(call)
    _put_optional(fields, "fiscal_year", _fiscal(query.fiscal_year))
    fields["period"] = _int(query.period, "period", 1, 12)
    _put_list(fields, "cash_accounts", _codes(query.cash_accounts, "cash_accounts"))
    _put_list(fields, "notes_accounts", _codes(query.notes_accounts, "notes_accounts"))
    _put_optional(fields, "top", None if query.top is None else _int(query.top, "top", 1, 500))
    _put_optional(fields, "purchase_source", query.purchase_source)
    _put_flag(fields, "include_unverified", query.include_unverified)
    return fields
