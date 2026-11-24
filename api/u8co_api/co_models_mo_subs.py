"""生产订单修改明细行上的 allocations（子件的替代料）。与桥 MoUpdateSubsReq 一致，字段名不分大小写。

每项 {sort_seq, inv_code, substitutes}：按（行、子件行号、子件存货）定位子件；substitutes 整组替换该子件现有的替代料，
空数组清空。替代料每项同物料清单（co_models_bom_subs.check_subs），另收 defines（define22–define37）。
"""

from __future__ import annotations

import datetime as dt
import math
import re

from u8co_api.co_models_bom_subs import check_subs
from u8co_api.co_models_mo import _get, _only, _text, check_min_datetime

ALLOCS_MAX = 200
_ALLOC_KEYS = dict.fromkeys(("sort_seq", "inv_code", "substitutes"))
# 替代料表体自定义项的类型（s 文本、f float、i int、d 日期时间），两张替代料表相同。
# 文本 Define22–25 最长 60，28–33 最长 120。
_DEFINE_TYPES = {
    **{n: "s" for n in (22, 23, 24, 25, 28, 29, 30, 31, 32, 33)},
    26: "f",
    27: "f",
    34: "i",
    35: "i",
    36: "d",
    37: "d",
}
_INT_MIN, _INT_MAX = -(2**31), 2**31 - 1
# 与桥 TryParseExact 的两种形状一致，只收 ASCII 数字（strptime 的 %Y 等还收全角数字）。
_DATETIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}( [0-9]{2}:[0-9]{2}:[0-9]{2})?")


def check_allocations(value: object) -> None:
    """一行的 allocations：1 到 200 项，同一行里（行号、存货）不重复。"""
    if not isinstance(value, list):
        raise ValueError("allocations 必须是数组")
    if not 1 <= len(value) <= ALLOCS_MAX:
        raise ValueError(f"allocations 必须是 1 到 {ALLOCS_MAX} 项")
    seen: set[tuple[int, str]] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("allocations 的每一项必须是对象")
        _only(item, _ALLOC_KEYS)
        seq = _get(item, "sort_seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or not 0 <= seq <= _INT_MAX:
            raise ValueError("sort_seq 必须是 0 到 2147483647 的整数")
        _text(item, "inv_code", 60, required=True)
        inv = _get(item, "inv_code")
        if not isinstance(inv, str) or not inv.strip():
            raise ValueError("缺少字段 inv_code")
        if (seq, inv.strip().upper()) in seen:
            raise ValueError(f"子件重复：行号 {seq}、存货 {inv.strip()}")
        seen.add((seq, inv.strip().upper()))
        subs = _get(item, "substitutes")
        if subs is None:
            raise ValueError("缺少字段 substitutes（空数组表示清空）")
        check_subs(subs, _defines)


def _defines(value: object) -> None:
    if not isinstance(value, dict):
        raise ValueError("defines 必须是对象")
    _only(value, {f"define{n}": None for n in _DEFINE_TYPES})
    for n, kind in _DEFINE_TYPES.items():
        key = f"define{n}"
        cell = _get(value, key)
        if cell is not None:
            _define(value, key, cell, kind, 60 if n < 28 else 120)


def _define(row: dict, key: str, cell: object, kind: str, limit: int) -> None:
    if kind == "s":
        _text(row, key, limit, required=False)
    elif kind == "i":
        if isinstance(cell, bool) or not isinstance(cell, int) or not _INT_MIN <= cell <= _INT_MAX:
            raise ValueError(key + " 必须是整数")
    elif kind == "f":
        if isinstance(cell, bool) or not isinstance(cell, int | float) or not math.isfinite(cell):
            raise ValueError(key + " 必须是数")
    else:
        _datetime(cell, key)


def _datetime(cell: object, key: str) -> None:
    text = cell.strip() if isinstance(cell, str) else ""
    fmt = "%Y-%m-%d %H:%M:%S" if len(text) == 19 else "%Y-%m-%d"
    try:
        if _DATETIME.fullmatch(text) is None:
            raise ValueError(text)
        parsed = dt.datetime.strptime(text, fmt)
    except ValueError as exc:
        raise ValueError(key + " 必须是 yyyy-MM-dd 或 yyyy-MM-dd HH:mm:ss") from exc
    check_min_datetime(parsed, key)
