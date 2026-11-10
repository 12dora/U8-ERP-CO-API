"""销售订单新增 / 修改、发货单参照订单生单的落库核对字段（r50 字段约定）在 API 层的形状检查。

只查值的类型和上限；列是否存在、实际列宽、编码是否在档案里、保存后回读，由桥判断（SalePins，400 / 409）。
- 销售订单表头 cgatheringplan、caddcode，表体 cunitid、cwhcode：非空字符串编码（修改时空串表示清空），最长 60。
- 销售订单表体 inum：大于 0 的有限数值（可以是数值字符串）；cbdefine5：字符串（最长 120）或有限数值。
- 发货单生单表头 caddcode 同上；chdefine1–4：字符串（最长 120）或有限数值。
键名不分大小写，与桥一致。
"""

from __future__ import annotations

import math

from u8co_api.co_doctext import section

SALE_PINS_HELP = section(
    "销售订单、发货单生单的落库核对字段",
    (
        "销售订单表头 cgatheringplan（收付款协议）、caddcode（收货地址，须属于表头客户）",
        "销售订单表体 cwhcode（预发货仓库）、cbdefine5（表体扩展自定义项）、cunitid、inum",
        "发货单参照订单生单表头 caddcode（须属于订单客户）、chdefine1–4（表头扩展自定义项）",
        "保存后桥在同一事务里回读这些字段及订单 / 发货单表头 cdefine1、cdefine2，与请求不同回滚 409",
    ),
)
_CODE_MAX = 60
_TEXT_MAX = 120
_NUM_MAX = 10**12
_ORDER_HEAD_CODES = frozenset({"cgatheringplan", "caddcode"})
_ORDER_LINE_CODES = frozenset({"cunitid", "cwhcode"})
_ORDER_LINE_TEXT = frozenset({"cbdefine5"})
_DISPATCH_HEAD_TEXT = frozenset({"chdefine1", "chdefine2", "chdefine3", "chdefine4"})


def _code(path: str, value: object, edit: bool) -> None:
    if type(value) is not str:
        raise ValueError(path + " 必须是字符串编码")
    text = value.strip()
    if not text and not edit:
        raise ValueError(path + " 不能为空")
    if len(text) > _CODE_MAX:
        raise ValueError(path + " 超长：最多 60 个字符")


def _number(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and abs(value) <= _NUM_MAX


def _text(path: str, value: object) -> None:
    if type(value) is str:
        if len(value.strip()) > _TEXT_MAX:
            raise ValueError(path + " 超长：最多 120 个字符")
        return
    if not _number(value):
        raise ValueError(path + " 必须是字符串或数值")


def _num(path: str, value: object) -> None:
    # 以前就收 inum，调用方可能送数值字符串，照收。
    if type(value) is str:
        try:
            value = float(value.strip())
        except ValueError:
            raise ValueError(path + " 必须是大于 0 的数值") from None
    if not _number(value) or value <= 0:
        raise ValueError(path + " 必须是大于 0 的数值")


def _row(at: str, row: dict | None, codes: frozenset[str], texts: frozenset[str], edit: bool) -> None:
    for key, value in (row or {}).items():
        low = key.lower()
        path = f"{at}.{low}"
        if low in codes:
            _code(path, value, edit)
        elif low in texts:
            _text(path, value)
        elif low == "inum" and at != "head":
            _num(path, value)


def check_sale_order_pins(head: dict | None, lines: list[dict] | None, edit: bool = False) -> None:
    """销售订单新增（edit=False）或修改（edit=True，lines 是 op 行）的约定字段。"""
    _row("head", head, _ORDER_HEAD_CODES, frozenset(), edit)
    for index, line in enumerate(lines or []):
        if isinstance(line, dict) and str(line.get("op", "")).lower() != "delete":
            _row(f"lines.{index}", line, _ORDER_LINE_CODES, _ORDER_LINE_TEXT, edit)


def check_dispatch_gen_pins(head: dict | None) -> None:
    """发货单参照销售订单生单的表头约定字段。"""
    _row("head", head, frozenset({"caddcode"}), _DISPATCH_HEAD_TEXT, False)
