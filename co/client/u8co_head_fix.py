"""历史单据表头修正 voucher_head_fix（受控 SQL，不走单据修改）：只改已审核发货单的收货地址编码、业务员，
下游销售出库单、发票由桥按规则同步。混入 U8CoClient。

fields 的键只收 addr_code（0 到 30 个字符，空串清空）、person_code（1 到 20 个字符）；expect 必须覆盖 fields 里的每个键，
给出表头当前值（空串表示当前为空）。回滚就是把 fields 与 expect 对调，并把正向响应里的 undo 原样作为 undo 再调一次。细的校验（地址是否存在、业务员是否有效、
部门是否一致）在桥。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common
from co.client.u8co_idem import with_key

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

# 键 → 最大长度（DispatchList.caddcode nvarchar(30)、cPersonCode nvarchar(20)）。
HEAD_FIX_LIMIT = {"addr_code": 30, "person_code": 20}
HEAD_FIX_KINDS = ("dispatch",)
# 写入值不能为空的键；addr_code 写空串表示清空。
_NOT_EMPTY = ("person_code",)


# undo：字段 → 允许的下游表；Ar_Detail 只随 person_code。每个列表最多 500 个正整数主键，不重复。
UNDO_TABLES = {
    "addr_code": ("rdrecord32", "SaleBillVouch"),
    "person_code": ("rdrecord32", "SaleBillVouch", "Ar_Detail"),
}
UNDO_MAX = 500
_ID_MAX = 2147483647


@dataclass(frozen=True)
class HeadFix:
    """修正一张发货单（call.doc_id）的表头：kind 目前只有 dispatch。

    undo 是回滚用的下游记录（正向响应的 undo 原样放进来）：字段 → 表 → 主键列表；给了就只恢复列出的行。
    """

    kind: str
    fields: dict[str, str]
    expect: dict[str, str]
    undo: dict[str, dict[str, list[int]]] | None = None


class U8CoHeadFixMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def voucher_head_fix(self, call: U8Call, fix: HeadFix, idempotency_key: str | None = None) -> dict[str, Any]:
        if type(call.doc_id) is not int or call.doc_id <= 0:
            raise ValueError("单据 id 必须是正整数")
        if fix.kind not in HEAD_FIX_KINDS:
            raise ValueError(f"type 目前只能是 {'、'.join(HEAD_FIX_KINDS)}")
        fields = _values(fix.fields, "fields", tuple(HEAD_FIX_LIMIT), writing=True)
        expect = _values(fix.expect, "expect", tuple(fields), writing=False)
        missing = [key for key in fields if key not in expect]
        if missing:
            raise ValueError(f"fields 改 {'、'.join(missing)} 时必须在 expect 里给出当前值（空串表示当前为空）")
        body = _common(call)
        body["type"] = fix.kind
        body["id"] = call.doc_id
        body["fields"] = fields
        body["expect"] = expect
        if fix.undo is not None:
            body["undo"] = _undo(fix.undo, tuple(fields))
        return self.call("/v1/vouchers/head_fix", with_key(body, idempotency_key))


def _values(values: object, label: str, allowed: tuple[str, ...], writing: bool) -> dict[str, str]:
    if not isinstance(values, dict):
        raise ValueError(f"{label} 必须是对象")
    if writing and not values:
        raise ValueError(f"{label} 至少要有一项")
    unknown = sorted(str(key) for key in values if key not in allowed)
    if unknown:
        raise ValueError(f"{label} 有不允许的键: {', '.join(unknown)}；可用 {'、'.join(allowed)}")
    for key, value in values.items():
        _one(value, f"{label}.{key}", HEAD_FIX_LIMIT[key], writing and key in _NOT_EMPTY, writing)
    return dict(values)


def _one(value: object, where: str, limit: int, not_empty: bool, writing: bool) -> None:
    if type(value) is not str or len(value) > limit:
        raise ValueError(f"{where} 必须是不超过 {limit} 个字符的字符串")
    if not_empty and value == "":
        raise ValueError(f"{where} 不能为空")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{where} 不能含控制字符")
    # 写入值首尾不能有空白；expect 只核对当前值，不要求写法合规。
    if writing and value != value.strip():
        raise ValueError(f"{where} 首尾不能有空白")


def _undo(values: object, fields: tuple[str, ...]) -> dict[str, dict[str, list[int]]]:
    if not isinstance(values, dict):
        raise ValueError("undo 必须是对象")
    for key, tables in values.items():
        if key not in UNDO_TABLES:
            raise ValueError(f"undo 有不认识的键: {key}；可用 {'、'.join(UNDO_TABLES)}")
        if key not in fields:
            raise ValueError(f"undo 有不在 fields 里的键: {key}")
        _undo_tables(tables, key)
    return {key: {table: list(ids) for table, ids in tables.items()} for key, tables in values.items()}


def _undo_tables(tables: object, key: str) -> None:
    if not isinstance(tables, dict):
        raise ValueError(f"undo.{key} 必须是对象")
    for table, ids in tables.items():
        where = f"undo.{key}.{table}"
        if table not in UNDO_TABLES[key]:
            raise ValueError(f"undo.{key} 有不认识的表: {table}；可用 {'、'.join(UNDO_TABLES[key])}")
        if not isinstance(ids, list) or len(ids) > UNDO_MAX:
            raise ValueError(f"{where} 必须是不超过 {UNDO_MAX} 个主键的数组")
        if any(type(item) is not int or not 0 < item <= _ID_MAX for item in ids):
            raise ValueError(f"{where} 的主键必须是 1 到 {_ID_MAX} 的整数")
        if len(set(ids)) != len(ids):
            raise ValueError(f"{where} 有重复的主键")
