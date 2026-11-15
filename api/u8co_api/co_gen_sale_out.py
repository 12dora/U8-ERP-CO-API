"""销售出库参照发货单按行生单的明细校验。与桥 StockGen.ParseOut 一致。

每行只收 source_line_id（发货单行 iDLsID）、quantity，另可带批号 cbatch、货位 cposition（字符串）。
同一发货行可以列多次（按批号 / 货位拆行），（行、批号、货位）不能重复；是否批次存货、货位仓库、可用量由桥查。
"""

from __future__ import annotations

import math

_ID_MAX = 2147483647
_QTY_MAX = 10**12
_TEXT_KEYS = {"cbatch": 60, "cposition": 20}
_LINE_KEYS = frozenset({"source_line_id", "quantity", *_TEXT_KEYS})

# 生单明细说明里「销售出库」小节的各条（co_models_edit_help）。
SALE_OUT_LINES_HELP = (
    "省略 lines 时按整张发货单生成",
    "带 lines 时收 source_line_id（发货单行 iDLsID）、quantity",
    "另可带字符串 cbatch（批号，最长 60）、cposition（货位，最长 20）",
    "同一发货行可以列多次、批号或货位不同（拆行），各次数量之和不超过发货数量减累计出库数量",
    "没列出的发货行不出库",
)


def _keys(row: dict) -> dict[str, object]:
    found: dict[str, object] = {}
    for key, value in row.items():
        low = key.lower()
        if low not in _LINE_KEYS:
            raise ValueError("不能设置字段 " + key)
        if low in found:
            raise ValueError("字段重复 " + key)
        found[low] = value
    return found


def _line_id(row: dict) -> int:
    raw = row.get("source_line_id")
    if type(raw) is not int or raw < 1 or raw > _ID_MAX:
        raise ValueError("source_line_id 无效")
    return raw


def _quantity(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("数量无效")
    number = float(value)
    if not math.isfinite(number) or number <= 0 or number > _QTY_MAX:
        raise ValueError("数量无效")


def _text(found: dict[str, object], name: str) -> str:
    value = found.get(name)
    if value is None:
        return ""
    if type(value) is not str:
        raise ValueError(name + " 必须是字符串")
    text = value.strip()
    if len(text) > _TEXT_KEYS[name]:
        raise ValueError(f"{name} 最长 {_TEXT_KEYS[name]} 个字符")
    return text.lower()


def check_sale_out(lines: list[dict]) -> None:
    """1 到 200 行；（source_line_id、cbatch、cposition）不能重复。"""
    if not lines:
        raise ValueError("必须指定明细")
    seen: set[tuple[int, str, str]] = set()
    for row in lines:
        found = _keys(row)
        key = (_line_id(row), _text(found, "cbatch"), _text(found, "cposition"))
        _quantity(row.get("quantity"))
        if key in seen:
            raise ValueError("明细行重复")
        seen.add(key)
