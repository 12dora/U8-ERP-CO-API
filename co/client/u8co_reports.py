"""客户端方法：只读报表 /v1/reports/*。混入 U8CoClient。

字段和取值范围与桥的约定一致；桥还会再查一遍。after 是桥给的不透明游标，原样传回。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common, _int, _limit, _put_optional, _text, _ymd

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call
    from co.client.u8co_open import OpeningQuery
    from co.client.u8co_gl_arc import GlKey
    from co.client.u8co_reports_detail import ArapDetailQuery, GlDetailQuery
    from co.client.u8co_reports_fa import FaChangesQuery, FaDeprQuery
    from co.client.u8co_reports_trace import DocTraceQuery, OrderExecQuery
    from co.client.u8co_reports_stock import (
        CustomerCreditQuery,
        PriceListQuery,
        StockLedgerQuery,
        StockPlaceQuery,
        StockSummaryQuery,
    )

AUX_DIMS = ("customer", "vendor", "dept", "person", "project")
SIDES = ("ar", "ap")
AGING_BASES = ("document", "due")
AGING_GROUPS = ("partner", "person", "partner_person")

_CODE_PREFIX = re.compile(r"^[0-9A-Za-z.\-]{1,40}\Z")
_PROJECT_CLASS = re.compile(r"^[0-9A-Za-z]{1,20}\Z")


@dataclass(frozen=True)
class GlBalanceQuery:
    """科目余额表。None 的字段不发送，由桥取缺省值。"""

    period_from: int
    period_to: int
    fiscal_year: int | None = None
    grade_from: int | None = None
    grade_to: int | None = None
    code_prefix: str = ""
    leaf_only: bool | None = None
    include_unposted: bool | None = None
    nonzero: bool | None = None
    after: str = ""
    limit: int | None = None


@dataclass(frozen=True)
class GlAuxQuery:
    """辅助核算余额表。project_class 只能和 dim=project 一起用。"""

    dim: str
    period_from: int
    period_to: int
    fiscal_year: int | None = None
    code_prefix: str = ""
    dim_code: str = ""
    project_class: str = ""
    nonzero: bool | None = None
    after: str = ""
    limit: int | None = None


@dataclass(frozen=True)
class ArapQuery:
    """往来余额和账龄分析。basis、buckets、group_by、person、overdue_only、default_credit_days 只用于账龄分析；
    default_credit_days（0 到 3650）要 basis=due：没有收款日期、信用期为 0 的单据按起算日加这么多天算到期日。"""

    side: str
    as_of: str = ""
    accounts: tuple[str, ...] | None = None
    exclude_accounts: tuple[str, ...] | None = None
    partner: str = ""
    nonzero: bool | None = None
    after: str = ""
    limit: int | None = None
    basis: str = ""
    buckets: tuple[int, ...] | None = None
    group_by: str = ""
    person: tuple[str, ...] | None = None
    overdue_only: bool | None = None
    default_credit_days: int | None = None


@dataclass(frozen=True)
class BomQuery:
    parent: str
    as_of: str = ""
    levels: int | None = None
    limit: int | None = None


class U8CoReportsMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def report_close_status(self, call: U8Call, fiscal_year: int | None = None) -> dict[str, Any]:
        fields = _common(call)
        _put_optional(fields, "fiscal_year", _fiscal(fiscal_year))
        return self.call("/v1/reports/close_status", fields)

    def report_gl_balance(self, call: U8Call, query: GlBalanceQuery) -> dict[str, Any]:
        return self.call("/v1/reports/gl_balance", _gl_balance_fields(call, query))

    def report_gl_aux_balance(self, call: U8Call, query: GlAuxQuery) -> dict[str, Any]:
        return self.call("/v1/reports/gl_aux_balance", _gl_aux_fields(call, query))

    def report_arap_balance(self, call: U8Call, query: ArapQuery) -> dict[str, Any]:
        if query.basis or query.buckets is not None or _aging_only(query):
            raise ValueError("往来余额不能带 basis、buckets、group_by、person、overdue_only 或 default_credit_days")
        return self.call("/v1/reports/arap_balance", _arap_fields(call, query))

    def report_arap_aging(self, call: U8Call, query: ArapQuery) -> dict[str, Any]:
        fields = _arap_fields(call, query)
        if query.basis and query.basis not in AGING_BASES:
            raise ValueError("basis 只能是 document 或 due")
        _put_optional(fields, "basis", query.basis)
        _put_optional(fields, "buckets", _buckets(query.buckets))
        _aging_keys(fields, query)
        return self.call("/v1/reports/arap_aging", fields)

    def report_bom(self, call: U8Call, query: BomQuery) -> dict[str, Any]:
        fields = _common(call)
        fields["parent"] = _text(query.parent, "母件编码 parent", 60)
        _put_optional(fields, "as_of", query.as_of and _ymd(query.as_of, "as_of"))
        _put_optional(fields, "levels", None if query.levels is None else _int(query.levels, "levels", 1, 10))
        _put_optional(fields, "limit", _limit(query.limit, 5000))
        return self.call("/v1/reports/bom", fields)

    # 明细账：条件和正文在 u8co_reports_detail（那里要用本模块的校验函数，所以在这里延迟导入）。
    def report_arap_detail(self, call: U8Call, query: ArapDetailQuery) -> dict[str, Any]:
        from co.client.u8co_reports_detail import arap_detail_fields

        return self.call("/v1/reports/arap_detail", arap_detail_fields(call, query))

    def report_gl_detail(self, call: U8Call, query: GlDetailQuery) -> dict[str, Any]:
        from co.client.u8co_reports_detail import gl_detail_fields

        return self.call("/v1/reports/gl_detail", gl_detail_fields(call, query))

    # 订单执行、单据追溯：条件和正文在 u8co_reports_trace（同明细账，延迟导入）。
    def report_order_execution(self, call: U8Call, query: OrderExecQuery) -> dict[str, Any]:
        from co.client.u8co_reports_trace import order_exec_fields

        return self.call("/v1/reports/order_execution", order_exec_fields(call, query))

    def report_doc_trace(self, call: U8Call, query: DocTraceQuery) -> dict[str, Any]:
        from co.client.u8co_reports_trace import doc_trace_fields

        return self.call("/v1/reports/doc_trace", doc_trace_fields(call, query))

    # 库存与销售支持报表：条件和正文在 u8co_reports_stock（同明细账，延迟导入）。
    def report_stock_ledger(self, call: U8Call, query: StockLedgerQuery) -> dict[str, Any]:
        from co.client.u8co_reports_stock import stock_ledger_fields

        return self.call("/v1/reports/stock_ledger", stock_ledger_fields(call, query))

    def report_stock_summary(self, call: U8Call, query: StockSummaryQuery) -> dict[str, Any]:
        from co.client.u8co_reports_stock import stock_summary_fields

        return self.call("/v1/reports/stock_summary", stock_summary_fields(call, query))

    def report_position_stock(self, call: U8Call, query: StockPlaceQuery) -> dict[str, Any]:
        from co.client.u8co_reports_stock import stock_place_fields

        return self.call("/v1/reports/position_stock", stock_place_fields(call, query, False))

    def report_batch_stock(self, call: U8Call, query: StockPlaceQuery) -> dict[str, Any]:
        from co.client.u8co_reports_stock import stock_place_fields

        return self.call("/v1/reports/batch_stock", stock_place_fields(call, query, True))

    def report_customer_credit(self, call: U8Call, query: CustomerCreditQuery) -> dict[str, Any]:
        from co.client.u8co_reports_stock import customer_credit_fields

        return self.call("/v1/reports/customer_credit", customer_credit_fields(call, query))

    def report_price_list(self, call: U8Call, query: PriceListQuery) -> dict[str, Any]:
        from co.client.u8co_reports_stock import price_list_fields

        return self.call("/v1/reports/price_list", price_list_fields(call, query))

    # 固定资产变动单、折旧：条件和正文在 u8co_reports_fa（延迟导入同上）。
    def report_fa_changes(self, call: U8Call, query: FaChangesQuery) -> dict[str, Any]:
        from co.client.u8co_reports_fa import fa_changes_fields

        return self.call("/v1/reports/fa_changes", fa_changes_fields(call, query))

    def report_fa_depreciation(self, call: U8Call, query: FaDeprQuery) -> dict[str, Any]:
        from co.client.u8co_reports_fa import fa_depr_fields

        return self.call("/v1/reports/fa_depreciation", fa_depr_fields(call, query))

    # 期初余额（条件和正文在 u8co_open）与附件列表，都是只读，延迟导入同上。
    def report_opening_balance(self, call: U8Call, query: OpeningQuery) -> dict[str, Any]:
        from co.client.u8co_open import opening_fields

        return self.call("/v1/reports/opening_balance", opening_fields(call, query))

    # 账套体检：十项只读检查，问题在 checks 里（HTTP 200）。as_of 缺省为登录日期。
    def report_account_readiness(self, call: U8Call, as_of: str = "") -> dict[str, Any]:
        fields = _common(call)
        _put_optional(fields, "as_of", as_of and _ymd(as_of, "as_of"))
        return self.call("/v1/reports/account_readiness", fields)

    def gl_attachments(self, call: U8Call, key: GlKey) -> dict[str, Any]:
        from co.client.u8co_gl_arc import _gl_keyed

        return self.call("/v1/gl/vouchers/attachments/list", _gl_keyed(call, key))

    def voucher_attachments(self, call: U8Call, kind: str) -> dict[str, Any]:
        from co.client.u8co_client import _typed
        from co.client.u8co_kinds import _ALL_KINDS

        return self.call("/v1/vouchers/attachments/list", _typed(call, kind, _ALL_KINDS))


def _fiscal(value: int | None) -> int | None:
    return None if value is None else _int(value, "fiscal_year", 2000, 2099)


def _flag(value: bool | None, label: str) -> bool | None:
    if value is not None and type(value) is not bool:
        raise ValueError(f"{label} 必须是布尔")
    return value


def _put_flag(fields: dict[str, Any], key: str, value: bool | None) -> None:
    # False 也要发送：arap 的 nonzero 缺省是 true。
    if _flag(value, key) is not None:
        fields[key] = value


def _code_prefix(value: object, label: str) -> str:
    if type(value) is not str or _CODE_PREFIX.fullmatch(value) is None:
        raise ValueError(f"{label} 格式无效")
    return value


def _periods(fields: dict[str, Any], low: int, high: int) -> None:
    fields["period_from"] = _int(low, "period_from", 1, 12)
    fields["period_to"] = _int(high, "period_to", 1, 12)
    if low > high:
        raise ValueError("period_from 不能大于 period_to")


def _grades(fields: dict[str, Any], query: GlBalanceQuery) -> None:
    low = 1 if query.grade_from is None else _int(query.grade_from, "grade_from", 1, 9)
    high = 9 if query.grade_to is None else _int(query.grade_to, "grade_to", 1, 9)
    if low > high:
        raise ValueError("grade_from 不能大于 grade_to")
    _put_optional(fields, "grade_from", query.grade_from)
    _put_optional(fields, "grade_to", query.grade_to)


def _paging(fields: dict[str, Any], after: str, limit: int | None, top: int) -> None:
    _put_optional(fields, "after", after and _text(after, "after", 200))
    _put_optional(fields, "limit", _limit(limit, top))


def _gl_balance_fields(call: U8Call, query: GlBalanceQuery) -> dict[str, Any]:
    fields = _common(call)
    _put_optional(fields, "fiscal_year", _fiscal(query.fiscal_year))
    _periods(fields, query.period_from, query.period_to)
    _grades(fields, query)
    _put_optional(fields, "code_prefix", query.code_prefix and _code_prefix(query.code_prefix, "code_prefix"))
    _put_flag(fields, "leaf_only", query.leaf_only)
    _put_flag(fields, "include_unposted", query.include_unposted)
    _put_flag(fields, "nonzero", query.nonzero)
    _paging(fields, query.after, query.limit, 1000)
    return fields


def _gl_aux_fields(call: U8Call, query: GlAuxQuery) -> dict[str, Any]:
    if query.dim not in AUX_DIMS:
        raise ValueError("dim 只能是 " + "、".join(AUX_DIMS))
    if query.project_class and query.dim != "project":
        raise ValueError("project_class 只能和 dim=project 一起用")
    if query.project_class and _PROJECT_CLASS.fullmatch(query.project_class) is None:
        raise ValueError("project_class 格式无效")
    fields = _common(call)
    fields["dim"] = query.dim
    _put_optional(fields, "fiscal_year", _fiscal(query.fiscal_year))
    _periods(fields, query.period_from, query.period_to)
    _put_optional(fields, "code_prefix", query.code_prefix and _code_prefix(query.code_prefix, "code_prefix"))
    _put_optional(fields, "dim_code", query.dim_code and _text(query.dim_code, "dim_code", 60))
    _put_optional(fields, "project_class", query.project_class)
    _put_flag(fields, "nonzero", query.nonzero)
    _paging(fields, query.after, query.limit, 1000)
    return fields


def _prefixes(values: object, label: str, low: int) -> list[str] | None:
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or not low <= len(values) <= 20:
        raise ValueError(f"{label} 必须是 {low} 到 20 个科目编码前缀")
    return [_code_prefix(item, label) for item in values]


def _arap_fields(call: U8Call, query: ArapQuery) -> dict[str, Any]:
    if query.side not in SIDES:
        raise ValueError("side 只能是 ar 或 ap")
    fields = _common(call)
    fields["side"] = query.side
    _put_optional(fields, "as_of", query.as_of and _ymd(query.as_of, "as_of"))
    accounts = _prefixes(query.accounts, "accounts", 1)
    if accounts is not None:
        fields["accounts"] = accounts
    excluded = _prefixes(query.exclude_accounts, "exclude_accounts", 0)
    if excluded is not None:
        fields["exclude_accounts"] = excluded
    _put_optional(fields, "partner", query.partner and _text(query.partner, "partner", 60))
    _put_flag(fields, "nonzero", query.nonzero)
    _paging(fields, query.after, query.limit, 1000)
    return fields


def _buckets(values: tuple[int, ...] | None) -> list[int] | None:
    if values is None:
        return None
    message = "buckets 必须是 1 到 10 个递增的天数"
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 10:
        raise ValueError(message)
    days = [_int(day, "buckets", 1, 3650) for day in values]
    if any(a >= b for a, b in zip(days, days[1:])):
        raise ValueError(message)
    return days


def _aging_only(query: ArapQuery) -> bool:
    return (
        bool(query.group_by)
        or query.person is not None
        or query.overdue_only is not None
        or query.default_credit_days is not None
    )


def _aging_keys(fields: dict[str, Any], query: ArapQuery) -> None:
    if query.group_by and query.group_by not in AGING_GROUPS:
        raise ValueError("group_by 只能是 " + "、".join(AGING_GROUPS))
    _put_optional(fields, "group_by", query.group_by)
    if query.person is not None:
        if not isinstance(query.person, (list, tuple)) or not 1 <= len(query.person) <= 20:
            raise ValueError("person 必须是 1 到 20 个业务员编码")
        fields["person"] = [_text(code, "业务员编码 person", 20) for code in query.person]
    if _flag(query.overdue_only, "overdue_only") and query.basis != "due":
        raise ValueError("overdue_only 只能和 basis=due 一起用")
    _put_flag(fields, "overdue_only", query.overdue_only)
    _credit_days(fields, query)


def _credit_days(fields: dict[str, Any], query: ArapQuery) -> None:
    if query.default_credit_days is None:
        return
    if query.basis != "due":
        raise ValueError("default_credit_days 只能和 basis=due 一起用")
    fields["default_credit_days"] = _int(query.default_credit_days, "default_credit_days", 0, 3650)
