"""经营管理报表（业务口径）：/v1/reports/mgmt/sales（销售收入、成本、毛利）、mgmt/arap_terms（往来余额、账龄、
账期与回款天数）。混入 U8CoClient。

字段和取值范围与桥一致；桥还会再查一遍。两张都是聚合结果，不翻页：按收入（往来按余额绝对值）取前 top 组，
其余并成 others，totals 是全部的合计。往来的 side、as_of、accounts、buckets、default_credit_days 与账龄分析
（basis=due）同一规则，buckets 桥缺省 [30, 60, 90, 180]。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common, _int, _put_optional, _ymd
from co.client.u8co_reports import SIDES, _buckets, _fiscal, _periods, _prefixes, _put_flag

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

MGMT_SALES_ROUTE = "/v1/reports/mgmt/sales"
MGMT_ARAP_ROUTE = "/v1/reports/mgmt/arap_terms"
SALES_DIMS = ("period", "customer", "inventory", "person", "department")


@dataclass(frozen=True)
class MgmtSalesQuery:
    """销售分析。group_by 为 None 时桥按客户分组，空元组只出合计；top 1 到 500（桥缺省 200）。"""

    period_from: int
    period_to: int
    fiscal_year: int | None = None
    group_by: tuple[str, ...] | None = None
    top: int | None = None
    include_unverified: bool | None = None


@dataclass(frozen=True)
class MgmtArapQuery:
    """往来账期。as_of 缺省为登录日期，accounts 缺省应收 1122 / 应付 2202。"""

    side: str
    as_of: str = ""
    accounts: tuple[str, ...] | None = None
    buckets: tuple[int, ...] | None = None
    default_credit_days: int | None = None
    top: int | None = None


class U8CoMgmtSaMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def report_mgmt_sales(self, call: U8Call, query: MgmtSalesQuery) -> dict[str, Any]:
        return self.call(MGMT_SALES_ROUTE, mgmt_sales_fields(call, query))

    def report_mgmt_arap_terms(self, call: U8Call, query: MgmtArapQuery) -> dict[str, Any]:
        return self.call(MGMT_ARAP_ROUTE, mgmt_arap_fields(call, query))


def _top(value: int | None) -> int | None:
    return None if value is None else _int(value, "top", 1, 500)


def _group_by(values: object) -> list[str] | None:
    if values is None:
        return None
    message = "group_by 只能是 " + "、".join(SALES_DIMS) + " 中不重复的若干项"
    if not isinstance(values, (list, tuple)) or len(set(values)) != len(values):
        raise ValueError(message)
    if any(item not in SALES_DIMS for item in values):
        raise ValueError(message)
    return list(values)


def mgmt_sales_fields(call: U8Call, query: MgmtSalesQuery) -> dict[str, Any]:
    fields = _common(call)
    _put_optional(fields, "fiscal_year", _fiscal(query.fiscal_year))
    _periods(fields, query.period_from, query.period_to)
    dims = _group_by(query.group_by)
    if dims is not None:
        fields["group_by"] = dims
    _put_optional(fields, "top", _top(query.top))
    _put_flag(fields, "include_unverified", query.include_unverified)
    return fields


def mgmt_arap_fields(call: U8Call, query: MgmtArapQuery) -> dict[str, Any]:
    if query.side not in SIDES:
        raise ValueError("side 只能是 ar 或 ap")
    fields = _common(call)
    fields["side"] = query.side
    _put_optional(fields, "as_of", query.as_of and _ymd(query.as_of, "as_of"))
    accounts = _prefixes(query.accounts, "accounts", 1)
    if accounts is not None:
        fields["accounts"] = accounts
    _put_optional(fields, "buckets", _buckets(query.buckets))
    days = query.default_credit_days
    _put_optional(fields, "default_credit_days", None if days is None else _int(days, "default_credit_days", 0, 3650))
    _put_optional(fields, "top", _top(query.top))
    return fields
