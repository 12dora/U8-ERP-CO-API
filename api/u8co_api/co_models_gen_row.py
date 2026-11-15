"""参照生单明细行的公共校验：来源行 id 与数量（从 co_models_edit 移出，免得那个文件超长）。"""

from __future__ import annotations

import math

_ID_MAX = 2147483647
_QTY_MAX = 10**12


def _source_id(row: dict, seen: set[int]) -> None:
    raw = row.get("source_line_id")
    if type(raw) is not int:
        raise ValueError("source_line_id 无效")
    if raw < 1 or raw > _ID_MAX:
        raise ValueError("source_line_id 无效")
    if raw in seen:
        raise ValueError("source_line_id 重复")
    seen.add(raw)


def _quantity(value: object) -> None:
    if type(value) not in (int, float):
        raise ValueError("数量无效")
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > _QTY_MAX:
        raise ValueError("数量无效")


def check_gen_row(row: dict, seen: set[int]) -> None:
    """source_line_id 是不重复的 1 到 2147483647 的整数，quantity 是大于 0、不超过 10^12 的有限数。"""
    _source_id(row, seen)
    _quantity(row.get("quantity"))
