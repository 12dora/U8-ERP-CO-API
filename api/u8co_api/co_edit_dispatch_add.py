"""发货单修改新增行（vouchers/update 的 op=add）的专用校验。与桥 SaleEditMore.ReadAdd 一致。

新行参照本单来源销售订单的行：source_line_id 是订单行 iSOsID，必须有大于 0 的 iQuantity 和非空 cWhCode。
同一张订单、同一客户、订单与行未关闭、可发数量够，这些要查库，由桥判断（400 / 409）。
"""

from __future__ import annotations

import math
import re

DISPATCH_ADD_HELP = (
    "发货单可以 add：source_line_id 是本单来源销售订单的行 iSOsID（同一张订单、同一客户、未关闭、可发数量够），"
    "必须有大于 0 的 iQuantity 和 cWhCode，另可带 cBatch、cMemo、cFree1–10、cDefine22–37，不能带 line_id；"
    "同一订单行只能 add 一次"
)
_KEYS = frozenset({"op", "source_line_id", "iquantity", "cwhcode", "cbatch", "cmemo"})
_NUMBERED = re.compile(r"(cfree|cdefine)([1-9]\d?)")
_SPANS = {"cfree": (1, 10), "cdefine": (22, 37)}
_ID_MAX = 2147483647
_QTY_MAX = 10**12


def _allowed(key: str) -> bool:
    low = key.lower()
    if low in _KEYS:
        return True
    match = _NUMBERED.fullmatch(low)
    if match is None:
        return False
    first, last = _SPANS[match.group(1)]
    return first <= int(match.group(2)) <= last


def _value(row: dict, name: str) -> object:
    for key, value in row.items():
        if key.lower() == name:
            return value
    return None


def _source(row: dict, seen: set[int]) -> None:
    raw = _value(row, "source_line_id")
    if type(raw) is not int or raw < 1 or raw > _ID_MAX:
        raise ValueError("新增行必须有 source_line_id（来源订单行 iSOsID）")
    if raw in seen:
        raise ValueError("source_line_id 重复")
    seen.add(raw)


def _quantity(row: dict) -> None:
    raw = _value(row, "iquantity")
    if type(raw) not in (int, float):
        raise ValueError("新增行必须有大于 0 的 iQuantity")
    if not math.isfinite(raw) or raw <= 0 or raw > _QTY_MAX:
        raise ValueError("新增行必须有大于 0 的 iQuantity")


def _warehouse(row: dict) -> None:
    raw = _value(row, "cwhcode")
    if type(raw) is not str or not raw.strip():
        raise ValueError("新增行必须指定仓库 cWhCode")


def _check_add(row: dict, seen: set[int]) -> None:
    for key in row:
        if not _allowed(key):
            raise ValueError("新增行不能设置字段 " + key)
    _source(row, seen)
    _quantity(row)
    _warehouse(row)


def check_dispatch_adds(kind: str, lines: list[dict] | None) -> None:
    """发货单的 add 行：只收名单内的字段，source_line_id、iQuantity、cWhCode 必填，同一订单行不重复。其它类型不管。"""
    if kind != "dispatch" or not lines:
        return
    seen: set[int] = set()
    for row in lines:
        if row.get("op") == "add":
            _check_add(row, seen)
