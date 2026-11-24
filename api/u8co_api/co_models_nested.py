"""请求里值可以是数组的字段，按单据类型登记，分表头和明细行（与桥 NestedFields、客户端 u8co_rows 的表一致）：
检验单（来料、产品、其他检验单）表头的 items（检验项目）；物料清单明细行的 substitutes（替代料）、locators（定位符）。
其余字段的值只能是字符串、数字或布尔；数组里每项的形状由各类型自己的校验细查。
"""

from __future__ import annotations

from u8co_api.co_gen_qm import CHECK_KINDS
from u8co_api.co_models_types import Cell

NESTED_HEAD: dict[str, tuple[str, ...]] = {kind: ("items",) for kind in CHECK_KINDS}
NESTED_LINES: dict[str, tuple[str, ...]] = {"bom": ("substitutes", "locators")}
# 明细的值：标量，或（登记过的字段）对象数组。
LineCell = Cell | list[dict[str, Cell]]


def _nested(kind: str, row: dict, table: dict[str, tuple[str, ...]], where: str) -> None:
    allowed = table.get(kind, ())
    for key, value in row.items():
        if isinstance(value, list) and key.lower() not in allowed:
            raise ValueError(where + "的值不能嵌套：" + key)


def nested_head(kind: str, head: dict | None) -> None:
    """表头只有登记过的字段（检验单的 items）可以是数组。"""
    _nested(kind, head or {}, NESTED_HEAD, "表头")


def nested_lines(kind: str, lines: list[dict] | None) -> None:
    """明细行只有登记过的字段（物料清单的 substitutes、locators）可以是数组。"""
    for row in lines or []:
        _nested(kind, row, NESTED_LINES, "明细")


def nested_cells(kind: str, head: dict | None, lines: list[dict] | None) -> None:
    nested_head(kind, head)
    nested_lines(kind, lines)
