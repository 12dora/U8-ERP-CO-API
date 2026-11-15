"""明细账报表的查询条件和正文：往来明细账 /v1/reports/arap_detail、科目明细账 /v1/reports/gl_detail。

方法在 U8CoReportsMixin（report_arap_detail、report_gl_detail）。字段和取值范围与桥的约定一致；桥还会再查一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common, _int, _put_optional, _text, _ymd
from co.client.u8co_reports import SIDES, _code_prefix, _fiscal, _paging, _prefixes, _put_flag

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

DETAIL_BASES = ("register",)
_AUX = (("customer", 60), ("vendor", 60), ("dept", 20), ("person", 20), ("project", 60), ("project_class", 20))


@dataclass(frozen=True)
class ArapDetailQuery:
    """往来明细账。partner 是一个编码或 1 到 20 个编码；date_to 空时桥取 date。"""

    side: str
    partner: str | tuple[str, ...]
    date_from: str
    date_to: str = ""
    basis: str = ""
    accounts: tuple[str, ...] | None = None
    exclude_accounts: tuple[str, ...] | None = None
    dept: str = ""
    person: str = ""
    include_writeoff: bool | None = None
    after: str = ""
    limit: int | None = None


@dataclass(frozen=True)
class GlDetailQuery:
    """科目明细账。期间（period_from / period_to）和日期（date_from / date_to）二选一。"""

    code: str
    period_from: int | None = None
    period_to: int | None = None
    date_from: str = ""
    date_to: str = ""
    fiscal_year: int | None = None
    include_sub: bool | None = None
    include_unposted: bool | None = None
    customer: str = ""
    vendor: str = ""
    dept: str = ""
    person: str = ""
    project: str = ""
    project_class: str = ""
    after: str = ""
    limit: int | None = None


def _partner(value: object) -> str | list[str]:
    if isinstance(value, str):
        return _text(value, "往来单位编码 partner", 60)
    if not isinstance(value, (list, tuple)) or not 1 <= len(value) <= 20:
        raise ValueError("partner 必须是往来单位编码或 1 到 20 个编码")
    return [_text(code, "往来单位编码 partner", 60) for code in value]


def _dates(fields: dict[str, Any], low: str, high: str) -> None:
    fields["date_from"] = _ymd(low, "date_from")
    _put_optional(fields, "date_to", high and _ymd(high, "date_to"))
    if high and low > high:
        raise ValueError("date_from 不能晚于 date_to")


def arap_detail_fields(call: U8Call, query: ArapDetailQuery) -> dict[str, Any]:
    if query.side not in SIDES:
        raise ValueError("side 只能是 ar 或 ap")
    if query.basis and query.basis not in DETAIL_BASES:
        raise ValueError("basis 目前只能是 register")
    fields = _common(call)
    fields["side"] = query.side
    fields["partner"] = _partner(query.partner)
    _dates(fields, query.date_from, query.date_to)
    _put_optional(fields, "basis", query.basis)
    accounts = _prefixes(query.accounts, "accounts", 1)
    if accounts is not None:
        fields["accounts"] = accounts
    excluded = _prefixes(query.exclude_accounts, "exclude_accounts", 0)
    if excluded is not None:
        fields["exclude_accounts"] = excluded
    _put_optional(fields, "dept", query.dept and _text(query.dept, "dept", 20))
    _put_optional(fields, "person", query.person and _text(query.person, "person", 20))
    _put_flag(fields, "include_writeoff", query.include_writeoff)
    _paging(fields, query.after, query.limit, 1000)
    return fields


def _gl_range(fields: dict[str, Any], query: GlDetailQuery) -> None:
    periods = query.period_from is not None or query.period_to is not None
    if periods == bool(query.date_from or query.date_to):
        raise ValueError("period_from / period_to 与 date_from / date_to 必须二选一")
    if periods:
        fields["period_from"] = _int(query.period_from, "period_from", 1, 12)
        fields["period_to"] = _int(query.period_to, "period_to", 1, 12)
        if fields["period_from"] > fields["period_to"]:
            raise ValueError("period_from 不能大于 period_to")
        return
    if not query.date_to:
        raise ValueError("date_from 和 date_to 必须同时给出")
    _dates(fields, query.date_from, query.date_to)
    if query.date_from[:4] != query.date_to[:4]:
        raise ValueError("date_from 和 date_to 必须在同一年度")


def gl_detail_fields(call: U8Call, query: GlDetailQuery) -> dict[str, Any]:
    fields = _common(call)
    fields["code"] = _code_prefix(query.code, "code")
    _put_optional(fields, "fiscal_year", _fiscal(query.fiscal_year))
    _gl_range(fields, query)
    _put_flag(fields, "include_sub", query.include_sub)
    _put_flag(fields, "include_unposted", query.include_unposted)
    for key, top in _AUX:
        value = getattr(query, key)
        _put_optional(fields, key, value and _text(value, key, top))
    if query.project_class and not query.project:
        raise ValueError("project_class 只能和 project 一起用")
    _paging(fields, query.after, query.limit, 1000)
    return fields
