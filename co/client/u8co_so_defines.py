"""销售订单表头自定义项写入 voucher_so_defines（受控 SQL，不走单据修改）：把销售订单表头的文本型自定义项
cdefine1–16 设成请求值，已审核、已有下游单据的订单也能写；只写不清空。混入 U8CoClient。

fields 是列 → 值（键不分大小写，统一转成小写）；expect 的键集合必须与 fields 相同，值为 None 或字符串
（None、空串、空白和 empty_values 里的值都表示当前为空）。cascade=True 时桥在同一事务里连带更新下游单据表头，
响应带 cascade 列表；只在为 True 时放进正文，empty_values 只在非空时放进正文。
长度的细校验（自定义项档案的最大长度、列宽）、订单是否被别人锁定、expect 是否相符都在桥；这里只挡明显的错误。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common
from co.client.u8co_idem import with_key

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

SO_DEFINES_KINDS = ("sale_order",)
# 与 API 一致：请求值上限（实际长度由桥按自定义项档案核对）、列数、空值标记的个数与长度。
SO_DEFINES_LIMIT = 120
SO_DEFINES_MAX_FIELDS = 16
SO_DEFINES_MAX_EMPTY = 8
SO_DEFINES_EMPTY_LEN = 20
_KEY = re.compile(r"cdefine([1-9]|1[0-6])\Z")


@dataclass(frozen=True)
class SoDefines:
    """写一张销售订单（call.doc_id）表头的自定义项：kind 目前只有 sale_order。

    fields 为 cdefineN → 要写入的值；expect 为同一组键 → 调用方认为的当前值（None 表示当前为空）；
    empty_values 是除 NULL 与空白外另按空值处理的取值；cascade=True 时连带更新下游单据表头。
    """

    kind: str
    fields: dict[str, str]
    expect: dict[str, str | None]
    empty_values: tuple[str, ...] = ()
    cascade: bool = False


class U8CoSoDefinesMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def voucher_so_defines(
        self, call: U8Call, change: SoDefines, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        body = _head(call, change.kind)
        empty = _empty_values(change.empty_values)
        body["fields"] = _fields(change.fields, empty)
        body["expect"] = _expect(change.expect, tuple(body["fields"]))
        if empty:
            body["empty_values"] = list(empty)
        if type(change.cascade) is not bool:
            raise ValueError("cascade 必须是 True 或 False")
        if change.cascade:
            body["cascade"] = True
        return self.call("/v1/vouchers/so_defines", with_key(body, idempotency_key))


def _head(call: U8Call, kind: str) -> dict[str, Any]:
    if type(call.doc_id) is not int or call.doc_id <= 0:
        raise ValueError("单据 id 必须是正整数")
    if kind not in SO_DEFINES_KINDS:
        raise ValueError(f"type 目前只能是 {'、'.join(SO_DEFINES_KINDS)}")
    body = _common(call)
    body["type"] = kind
    body["id"] = call.doc_id
    return body


def _keys(values: object, label: str) -> dict[str, Any]:
    """键转成小写并核对：只收 cdefine1 … cdefine16，转换后不能重复。"""
    if not isinstance(values, dict):
        raise ValueError(f"{label} 必须是对象")
    out: dict[str, Any] = {}
    for key, value in values.items():
        low = key.lower() if isinstance(key, str) else None
        if low is None or _KEY.fullmatch(low) is None:
            raise ValueError(f"{label} 有不允许的键 {key}；只收 cdefine1 到 cdefine16")
        if low in out:
            raise ValueError(f"{label} 的键 {low} 重复（不分大小写）")
        out[low] = value
    return out


def _fields(values: object, empty: tuple[str, ...]) -> dict[str, str]:
    fields = _keys(values, "fields")
    if not 1 <= len(fields) <= SO_DEFINES_MAX_FIELDS:
        raise ValueError(f"fields 须有 1 到 {SO_DEFINES_MAX_FIELDS} 项")
    tokens = {item.strip() for item in empty}
    for key, value in fields.items():
        where = f"fields.{key}"
        if type(value) is not str or not value.strip():
            raise ValueError(f"{where} 必须是非空字符串（本路由只写不清空）")
        _text(value, where)
        if value != value.strip():
            raise ValueError(f"{where} 首尾不能有空白")
        if value in tokens:
            raise ValueError(f"{where} 的值在 empty_values 里，本路由只写不清空")
    return fields


def _expect(values: object, keys: tuple[str, ...]) -> dict[str, str | None]:
    expect = _keys(values, "expect")
    missing = [key for key in keys if key not in expect]
    if missing:
        raise ValueError(f"expect 必须给出 {'、'.join(missing)} 的当前值（None 或空串表示当前为空）")
    extra = sorted(set(expect) - set(keys))
    if extra:
        raise ValueError(f"expect 有不在 fields 里的键: {', '.join(extra)}")
    for key, value in expect.items():
        # 当前值只核对，不要求写法合规（库里的值可以有首尾空白）。
        if value is not None:
            if type(value) is not str:
                raise ValueError(f"expect.{key} 必须是 None 或字符串")
            _text(value, f"expect.{key}")
    return {key: expect[key] for key in keys}


def _empty_values(values: object) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (tuple, list)):
        raise ValueError("empty_values 必须是字符串列表")
    if len(values) > SO_DEFINES_MAX_EMPTY:
        raise ValueError(f"empty_values 最多 {SO_DEFINES_MAX_EMPTY} 项")
    for index, value in enumerate(values):
        where = f"empty_values[{index}]"
        if type(value) is not str or not 1 <= len(value.strip()) <= SO_DEFINES_EMPTY_LEN:
            raise ValueError(f"{where} 去首尾空白后须为 1 到 {SO_DEFINES_EMPTY_LEN} 个字符")
        _text(value, where)
    return tuple(values)


def _text(value: str, where: str) -> None:
    if len(value) > SO_DEFINES_LIMIT:
        raise ValueError(f"{where} 最长 {SO_DEFINES_LIMIT} 个字符")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{where} 不能含控制字符")
