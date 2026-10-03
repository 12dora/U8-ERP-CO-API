"""应收 / 应付处理记录（事件源，/v1/arap/process/list，只读）。混入 U8CoClient。

明细：ProcListQuery(flag, changed_since=上一轮 watermark, after=本轮上一页 next)，按 Auto_ID 增量读处理行；
往来明细没有 rowversion，下一轮要从 watermark 往回退 max(滞后量, ident - watermark) 重读（见 docs/api-reference.md）。
摘要：ProcDigestQuery(flag, fiscal_year, periods, after)，按（处理方式、处理号）汇总期间批次；periods 省略即未结账期间。
这里只查请求的形状；取值范围由桥再查一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

ARAP_PROC_LIST_ROUTE = "/v1/arap/process/list"
_FLAGS = ("AR", "AP")
_ID_MAX = 2147483647
LIMIT_MAX = 500


@dataclass(frozen=True)
class ProcListQuery:
    """明细。changed_since 是上一轮的 watermark（十进制字符串或整数）；open_only 缺省由桥定（增量时 true）。"""

    flag: str
    changed_since: str | int | None = None
    after: int | None = None
    limit: int | None = None
    keys_only: bool | None = None
    open_only: bool | None = None


@dataclass(frozen=True)
class ProcDigestQuery:
    """摘要。periods 是 1 到 12 个期间（1 到 12），省略即该侧未结账期间；after 是上一页的 next（字符串）。"""

    flag: str
    fiscal_year: int | None = None
    periods: Any = None
    after: str | None = None
    limit: int | None = None


def _auth(call: U8Call) -> dict[str, Any]:
    return {"acc": call.acc, "year": call.year, "operator": call.operator, "password": call.password, "date": call.date}


def _flag(flag: object) -> str:
    if flag not in _FLAGS:
        raise ValueError("flag 只能是 AR 或 AP")
    return str(flag)


def _int(value: object, name: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} 必须是 {low} 到 {high} 的整数")
    return value


def _bool(fields: dict[str, Any], name: str, value: object) -> None:
    if value is None:
        return
    if type(value) is not bool:
        raise ValueError(f"{name} 必须是布尔值")
    fields[name] = value


def _since(value: object) -> str | int:
    if isinstance(value, str):
        if not value.isascii() or not value.isdigit() or len(value) > 10 or int(value) > _ID_MAX:
            raise ValueError("changed_since 必须是 0 到 2147483647 的十进制数字串")
        return value
    return _int(value, "changed_since", 0, _ID_MAX)


def proc_list_fields(call: U8Call, query: ProcListQuery) -> dict[str, Any]:
    # 键顺序：公共字段、flag、changed_since、after、limit、keys_only、open_only。值为 None 的不发。
    fields = _auth(call)
    fields["flag"] = _flag(query.flag)
    if query.changed_since is not None:
        fields["changed_since"] = _since(query.changed_since)
    if query.after is not None:
        fields["after"] = _int(query.after, "after", 0, _ID_MAX)
    if query.limit is not None:
        fields["limit"] = _int(query.limit, "limit", 1, LIMIT_MAX)
    _bool(fields, "keys_only", query.keys_only)
    _bool(fields, "open_only", query.open_only)
    return fields


def _periods(values: object) -> list[int]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 12:
        raise ValueError("periods 必须是 1 到 12 个期间")
    out = [_int(value, "periods[]", 1, 12) for value in values]
    if len(set(out)) != len(out):
        raise ValueError("periods 有重复的期间")
    return out


def proc_digest_fields(call: U8Call, query: ProcDigestQuery) -> dict[str, Any]:
    # 键顺序：公共字段、flag、digest、fiscal_year、periods、after、limit。
    fields = _auth(call)
    fields["flag"] = _flag(query.flag)
    fields["digest"] = True
    if query.fiscal_year is not None:
        fields["fiscal_year"] = _int(query.fiscal_year, "fiscal_year", 2000, 2099)
    if query.periods is not None:
        fields["periods"] = _periods(query.periods)
    if query.after is not None:
        if type(query.after) is not str or query.after == "":
            raise ValueError("after 必须是上一页的 next（字符串）")
        fields["after"] = query.after
    if query.limit is not None:
        fields["limit"] = _int(query.limit, "limit", 1, LIMIT_MAX)
    return fields


class U8CoArapProcListMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def arap_process_list(self, call: U8Call, query: ProcListQuery) -> dict[str, Any]:
        """处理行明细一页：items、next、watermark、ident、open_periods、last_closed。"""
        return self.call(ARAP_PROC_LIST_ROUTE, proc_list_fields(call, query))

    def arap_process_digest(self, call: U8Call, query: ProcDigestQuery) -> dict[str, Any]:
        """期间批次摘要一页：items（每批 min_id、max_id、pz、sum_d_f、sum_c_f、rows、partners）、next、periods 等。"""
        return self.call(ARAP_PROC_LIST_ROUTE, proc_digest_fields(call, query))
