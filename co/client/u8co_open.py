"""期初余额报表 opening_balance（/v1/reports/opening_balance）和附件列表（/v1/gl/vouchers/attachments/list、
/v1/vouchers/attachments/list）的查询条件和正文。都是只读。

方法在 U8CoReportsMixin（report_opening_balance、gl_attachments、voucher_attachments）。字段和取值范围与桥的约定一致；
桥还会再查一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common, _put_optional, _text
from co.client.u8co_reports import AUX_DIMS, SIDES, _code_prefix, _fiscal, _paging, _put_flag

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

OPENING_MODULES = ("stock", "arap", "gl")
# 各 module 能带的条件（nonzero、after、limit 之外）。
_OWN = {
    "stock": ("wh", "inv", "batch"),
    "arap": ("side", "partner", "code_prefix"),
    "gl": ("fiscal_year", "code_prefix", "leaf_only", "dim"),
}
_EMPTY = ("", None)


@dataclass(frozen=True)
class OpeningQuery:
    """期初余额。module=arap 时 side 必填；各 module 只能带自己的条件（见 _OWN）。nonzero 缺省 true（不发送）。"""

    module: str
    side: str = ""
    fiscal_year: int | None = None
    wh: str = ""
    inv: str = ""
    batch: str = ""
    partner: str = ""
    code_prefix: str = ""
    leaf_only: bool | None = None
    dim: str = ""
    nonzero: bool | None = None
    after: str = ""
    limit: int | None = None


def _check_module(query: OpeningQuery) -> None:
    if query.module not in OPENING_MODULES:
        raise ValueError("module 只能是 " + "、".join(OPENING_MODULES))
    own = _OWN[query.module]
    for fields in _OWN.values():
        for name in fields:
            if name not in own and getattr(query, name) not in _EMPTY:
                raise ValueError(f"{name} 不能和 module={query.module} 一起用")
    if query.module == "arap" and query.side not in SIDES:
        raise ValueError("module=arap 时 side 只能是 ar 或 ap")
    if query.dim and query.dim not in AUX_DIMS:
        raise ValueError("dim 只能是 " + "、".join(AUX_DIMS))


def _word(fields: dict[str, Any], key: str, value: str) -> None:
    _put_optional(fields, key, value and _text(value, key, 60))


def opening_fields(call: U8Call, query: OpeningQuery) -> dict[str, Any]:
    _check_module(query)
    fields = _common(call)
    fields["module"] = query.module
    _put_optional(fields, "side", query.side or None)
    _put_optional(fields, "fiscal_year", _fiscal(query.fiscal_year))
    for key in ("wh", "inv", "batch", "partner"):
        _word(fields, key, getattr(query, key))
    _put_optional(fields, "code_prefix", query.code_prefix and _code_prefix(query.code_prefix, "code_prefix"))
    _put_flag(fields, "leaf_only", query.leaf_only)
    _put_optional(fields, "dim", query.dim or None)
    _put_flag(fields, "nonzero", query.nonzero)
    _paging(fields, query.after, query.limit, 1000)
    return fields
