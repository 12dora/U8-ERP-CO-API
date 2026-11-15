"""固定资产只读报表：变动单 /v1/reports/fa_changes、折旧 /v1/reports/fa_depreciation 的条件和正文。

字段和取值范围与桥一致；桥还会再查一遍。after 是桥给的不透明游标，原样传回。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common, _int, _put_optional
from co.client.u8co_reports import _fiscal, _paging, _put_flag

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

_CARD = re.compile(r"^[0-9A-Za-z.\-]{1,20}\Z")
_CHANGE = re.compile(r"^[0-9A-Za-z.\-]{1,10}\Z")


@dataclass(frozen=True)
class FaChangesQuery:
    """变动单。None 或空串的字段不发送；fiscal_year 缺省为登录年度。"""

    fiscal_year: int | None = None
    period: int | None = None
    card: str = ""
    code: str = ""
    change_type: int | None = None
    after: str = ""
    limit: int | None = None


@dataclass(frozen=True)
class FaDeprQuery:
    """折旧：只含该年度已计提的期间。nonzero 为 True 时去掉当月折旧为 0 的行。"""

    fiscal_year: int | None = None
    period: int | None = None
    card: str = ""
    nonzero: bool | None = None
    after: str = ""
    limit: int | None = None


def _code(value: str, label: str, pattern: re.Pattern[str]) -> str:
    if not value:
        return ""
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ValueError(f"{label} 只能是字母、数字、点和减号")
    return value


def _head(call: U8Call, fiscal_year: int | None, period: int | None, card: str) -> dict[str, Any]:
    fields = _common(call)
    _put_optional(fields, "fiscal_year", _fiscal(fiscal_year))
    _put_optional(fields, "period", None if period is None else _int(period, "period", 1, 12))
    _put_optional(fields, "card", _code(card, "card", _CARD))
    return fields


def fa_changes_fields(call: U8Call, query: FaChangesQuery) -> dict[str, Any]:
    fields = _head(call, query.fiscal_year, query.period, query.card)
    _put_optional(fields, "code", _code(query.code, "code", _CHANGE))
    change = query.change_type
    _put_optional(fields, "change_type", None if change is None else _int(change, "change_type", 1, 99))
    _paging(fields, query.after, query.limit, 1000)
    return fields


def fa_depr_fields(call: U8Call, query: FaDeprQuery) -> dict[str, Any]:
    fields = _head(call, query.fiscal_year, query.period, query.card)
    _put_flag(fields, "nonzero", query.nonzero)
    _paging(fields, query.after, query.limit, 1000)
    return fields
