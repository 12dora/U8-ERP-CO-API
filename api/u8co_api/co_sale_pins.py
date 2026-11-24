"""销售订单新增 / 修改、发货单参照订单生单的落库核对字段在 API 层的形状检查。

只查值的类型和上限；列是否存在、实际列宽、编码是否在档案里、保存后回读，由桥判断（SalePins，400 / 409）。
- 销售订单表头 cgatheringplan、caddcode，表体 cunitid、cwhcode：非空字符串编码（修改时空串表示清空），最长 60。
- 销售订单表体 inum：大于 0 的有限数值（可以是数值字符串）。
- 表头自定义项 cdefine1–16、表头扩展自定义项 chdefineN、表体扩展自定义项 cbdefineN：字符串（最长 120）或有限数值。
- 发货单生单表头 caddcode 同上；发货单生单表体另收表体扩展自定义项 cbdefineN（DispatchLists_extradefine），同蓝字发票表体。
键名不分大小写，与桥一致。列须在账套里存在。
"""

from __future__ import annotations

import math
import re

from u8co_api.co_doctext import section
from u8co_api.errors import FieldValueError

SALE_PINS_HELP = section(
    "销售订单、发货单生单的落库核对字段",
    (
        "销售订单表头 cgatheringplan（收付款协议）、caddcode（收货地址，须属于表头客户）、表头自定义项 cdefine1–16",
        "销售订单表体 cwhcode（预发货仓库）、表体扩展自定义项 cbdefineN、cunitid、inum",
        "发货单参照订单生单表头 caddcode（须属于订单客户）、表头自定义项 cdefine1–16、表头扩展自定义项 chdefineN，"
        "表体扩展自定义项 cbdefineN（DispatchLists_extradefine）",
        "列须在账套里存在；只回读请求给出的字段，与请求不同回滚 409",
    ),
)
_CODE_MAX = 60
_TEXT_MAX = 120
_NUM_MAX = 10**12
_ORDER_HEAD_CODES = frozenset({"cgatheringplan", "caddcode"})
_ORDER_LINE_CODES = frozenset({"cunitid", "cwhcode"})
_EXTRA = re.compile(r"^c(h|b)define([1-9]|[1-9][0-9])$")
_HEAD_DEF = re.compile(r"^cdefine([1-9]|1[0-6])$")
_LOOKS_EXTRA = re.compile(r"^c([hb])define\d+$")
_LOOKS_HEAD = re.compile(r"^cdefine\d+$")


def _family(low: str, extra: str, head: bool) -> bool:
    """本侧落库核对的自定义项：表头 cdefine1–16，以及 extra 指定的 chdefineN / cbdefineN（N = 1–99，无前导零）。"""
    if head and _HEAD_DEF.fullmatch(low) is not None:
        return True
    match = _EXTRA.fullmatch(low)
    return bool(extra) and match is not None and match.group(1) == extra


def _bad_define(low: str, extra: str, head: bool) -> bool:
    if _family(low, extra, head):
        return False
    if head and _LOOKS_HEAD.fullmatch(low) is not None:
        return True
    match = _LOOKS_EXTRA.fullmatch(low)
    return bool(extra) and match is not None and match.group(1) == extra


def _code(path: str, value: object, edit: bool) -> None:
    if type(value) is not str:
        raise _bad(path, path + " 必须是字符串编码")
    text = value.strip()
    if not text and not edit:
        raise _bad(path, path + " 不能为空")
    if len(text) > _CODE_MAX:
        raise _bad(path, path + " 超长：最多 60 个字符")


def _bad(path: str, message: str) -> FieldValueError:
    """带字段路径的 400（http._validation 把 field 放进错误体）。"""
    return FieldValueError(path, message)


def _number(value: object) -> bool:
    # 先比范围再看是否有限：JSON 的大整数转浮点会 OverflowError（math.isfinite 也会转）。
    if type(value) is int:
        return abs(value) <= _NUM_MAX
    return type(value) is float and math.isfinite(value) and abs(value) <= _NUM_MAX


def _text(path: str, value: object) -> None:
    if type(value) is str:
        if len(value.strip()) > _TEXT_MAX:
            raise _bad(path, path + " 超长：最多 120 个字符")
        return
    if not _number(value):
        raise _bad(path, path + " 必须是字符串或数值")


def _num(path: str, value: object) -> None:
    # inum 可以是数值字符串。
    if type(value) is str:
        try:
            value = float(value.strip())
        except ValueError:
            raise _bad(path, path + " 必须是大于 0 的数值") from None
    if not _number(value) or value <= 0:
        raise _bad(path, path + " 必须是大于 0 的数值")


def _row(at: str, row: dict | None, codes: frozenset[str], extra: str, edit: bool) -> None:
    for key, value in (row or {}).items():
        if value is None:
            continue
        low = key.lower()
        path = f"{at}.{low}"
        if low in codes:
            _code(path, value, edit)
        elif _family(low, extra, at == "head"):
            _text(path, value)
        elif low == "inum" and at != "head":
            _num(path, value)
        elif _bad_define(low, extra, at == "head"):
            raise _bad(path, path + " 不是有效的自定义项")


def check_sale_order_pins(head: dict | None, lines: list[dict] | None, edit: bool = False) -> None:
    """销售订单新增（edit=False）或修改（edit=True，lines 是 op 行）的约定字段。"""
    _row("head", head, _ORDER_HEAD_CODES, "", edit)
    for index, line in enumerate(lines or []):
        if isinstance(line, dict) and str(line.get("op", "")).lower() != "delete":
            _row(f"lines.{index}", line, _ORDER_LINE_CODES, "b", edit)


def check_dispatch_gen_pins(head: dict | None, lines: list | None = None) -> None:
    """发货单参照销售订单生单的表头约定字段和表体扩展自定义项 cbdefineN（DispatchLists_extradefine）。"""
    _row("head", head, frozenset({"caddcode"}), "h", False)
    for index, line in enumerate(lines or []):
        if isinstance(line, dict):
            check_line_extra_pins(line, f"lines.{index}")


def check_invoice_line_pins(row: dict, at: str) -> None:
    """蓝字销售发票表体扩展自定义项 cbdefineN；at 是行路径（lines.<i> / sources.<s>.lines.<i>）。"""
    check_line_extra_pins(row, at)


def check_line_extra_pins(row: dict, at: str) -> None:
    """生单表体扩展自定义项 cbdefineN（蓝字发票、发货单共用）：字符串（最长 120）或有限数值，编号 1–99 无前导零。"""
    for key, value in row.items():
        if value is None:
            continue
        low = key.lower()
        path = f"{at}.{low}"
        if _family(low, "b", False):
            _text(path, value)
        elif _bad_define(low, "b", False):
            raise _bad(path, path + " 不是有效的自定义项")
