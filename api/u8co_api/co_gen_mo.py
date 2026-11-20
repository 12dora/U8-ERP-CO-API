"""生产订单参照销售订单生单（vouchers/generate，type=production_order）的请求校验。与桥 MoGenReq 一致。

表头只收字符串 mo_type（必填，最长 2）、mo_code、remark；空串和 null 与省略相同。表体 1 到 200 行，只收
source_line_id（销售订单行 iSOsID）、quantity、dept_code。quantity 规则同桥 MfgReq.LineQty。
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from u8co_api.co_doctext import section
from u8co_api.co_models_mo import _text

_HEAD_LEN = {"mo_type": 2, "mo_code": 30, "remark": 255}
_LINE_KEYS = frozenset({"source_line_id", "quantity", "dept_code"})
_ID_MAX = 2147483647
_DEPT = 12
_QTY_MAX = Decimal(10**12)
_QTY_BAD = "quantity 必须是大于 0 且不超过 1000000000000 的数"
LINES_MAX = 200
MO_GEN_HELP = section(
    "生产订单（type=production_order，source_type=sale_order，id 是销售订单 SO_SOMain.ID）",
    (
        "只收存货为 ATO 模型的销售订单行，全部行生成一张生产订单",
        "表头 mo_type 必填（生产订单类别编码，最长 2）",
        "可选 mo_code（最长 30，省略则 U8 编号）、remark（最长 255，省略时用销售订单行备注）",
        "明细 1 到 200 行，只收 source_line_id（销售订单行 iSOsID）、quantity、dept_code",
        "quantity 大于 0、最多 6 位小数、不超过 1000000000000",
        "dept_code 可选（最长 12，缺省取 U8 的生产部门缺省值）",
    ),
)


def check_mo_gen(head: dict | None, lines: list | None) -> None:
    _head(head)
    if not lines or len(lines) > LINES_MAX:
        raise ValueError("lines 必须是 1 到 200 行")
    seen: set[int] = set()
    for row in lines:
        _line(row, seen)


def _lowered(row: dict) -> dict:
    found: dict = {}
    for key, value in row.items():
        name = str(key).lower()
        if name in found:
            raise ValueError("字段重复 " + str(key))
        found[name] = value
    return found


def _head(head: dict | None) -> None:
    raw = head or {}
    found = _lowered(raw)
    for key in raw:
        if str(key).lower() not in _HEAD_LEN:
            raise ValueError("不能设置字段 " + str(key))
    _field(found, "mo_type", _HEAD_LEN["mo_type"], True)
    _field(found, "mo_code", _HEAD_LEN["mo_code"], False)
    _field(found, "remark", _HEAD_LEN["remark"], False)


def _field(found: dict, key: str, limit: int, required: bool) -> None:
    if required and found.get(key) is None:
        raise ValueError("缺少字段 " + key)
    _text(found, key, limit, required=required)


def _source_id(row: dict, seen: set[int]) -> None:
    raw = row.get("source_line_id")
    if type(raw) is not int:
        raise ValueError("source_line_id 必须是整数")
    if raw < 1 or raw > _ID_MAX:
        raise ValueError("source_line_id 无效")
    if raw in seen:
        raise ValueError("来源明细重复")
    seen.add(raw)


def _as_qty(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _quantity(value: object) -> None:
    qty = _as_qty(value)
    if qty is None or qty <= 0 or qty > _QTY_MAX:
        raise ValueError(_QTY_BAD)
    if qty != qty.quantize(Decimal("0.000001")):
        raise ValueError("quantity 最多 6 位小数")


def _line(raw: object, seen: set[int]) -> None:
    if not isinstance(raw, dict):
        raise ValueError("lines 的每一行必须是对象")
    row = _lowered(raw)
    for key in raw:
        if str(key).lower() not in _LINE_KEYS:
            raise ValueError("不能设置字段 " + str(key))
    _source_id(row, seen)
    _quantity(row.get("quantity"))
    _field(row, "dept_code", _DEPT, False)
