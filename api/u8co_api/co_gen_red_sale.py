"""红字销售发票参照退货单或红冲蓝字发票生单的专用校验。与桥 SaleGen.RedInvoice / BlueRedInvoice 一致。

type=sale_invoice、source_type=sale_return，id 是已审核的退货单（红字发货单）DLID。表头可省略，只收 cVouchType
（26 专票 / 27 普票，省略时取原蓝字发票类型，没有则 26）、dDate（yyyy-MM-dd）、cMemo、cDefine1–16。
lines 可省略（全部有剩余的退货行按剩余数量生成）；给了就是 1 到 200 行，每行 source_line_id（退货单行 iDLsID）、
正数 quantity（桥写负数，不超过 |退货数量| − |累计开票数量|），另可带 cMemo、cDefine22–37。
"""

from __future__ import annotations

import math
import re

RED_SALE_HELP = (
    "红字销售发票（type=sale_invoice、source_type=sale_return）参照已审核、已开票退货（bneedbill=1）的退货单"
    "（id 是退货单 DLID）：表头只收 cVouchType（26 或 27，省略时同原蓝字发票类型，缺省 26）、dDate、cMemo、cDefine1–16；"
    "lines 可省略（全部有剩余的退货行按剩余数量生成），给了每行是 source_line_id（退货单行 iDLsID）和正数 quantity，"
    "另可带 cMemo、cDefine22–37。"
    "红冲蓝字销售发票（source_type=sale_invoice）同样的表头和行：id 是已复核、非现结的蓝字发票 SBVID，"
    "cVouchType 省略时同蓝字发票（给了必须相同），source_line_id 是蓝字发票行 AutoID，"
    "quantity 不超过该行数量减去已红冲数量；lines 省略时全部有剩余的行按剩余数量红冲"
)
_ID_MAX = 2147483647
_QTY_MAX = 10**12
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBERED = re.compile(r"cdefine([1-9]\d?)")
_HEAD_TEXT = frozenset({"cmemo"})
_LINE_KEYS = frozenset({"source_line_id", "quantity", "cmemo"})


def _define(low: str, first: int, last: int) -> bool:
    match = _NUMBERED.fullmatch(low)
    return match is not None and first <= int(match.group(1)) <= last


def _lowered(row: dict) -> dict:
    found: dict = {}
    for key, value in row.items():
        low = key.lower()
        if low in found:
            raise ValueError("字段重复 " + key)
        found[low] = value
    return found


def _head_field(low: str, value: object) -> None:
    if low == "cvouchtype":
        if value not in ("26", "27", 26, 27) or type(value) is bool:
            raise ValueError("cVouchType 只能是 26 或 27")
    elif low == "ddate":
        if type(value) is not str or _DATE.fullmatch(value) is None:
            raise ValueError("dDate 必须是 yyyy-MM-dd")
    elif low in _HEAD_TEXT:
        if type(value) is not str:
            raise ValueError(low + " 必须是字符串")
    elif not _define(low, 1, 16):
        raise ValueError("不能设置字段 " + low)


def _line(row: dict, seen: set[int]) -> None:
    found = _lowered(row)
    for key in found:
        if key not in _LINE_KEYS and not _define(key, 22, 37):
            raise ValueError("不能设置字段 " + key)
    raw = found.get("source_line_id")
    if type(raw) is not int or raw < 1 or raw > _ID_MAX:
        raise ValueError("source_line_id 无效")
    if raw in seen:
        raise ValueError("source_line_id 重复")
    seen.add(raw)
    qty = found.get("quantity")
    if type(qty) not in (int, float) or not math.isfinite(float(qty)) or not 0 < float(qty) <= _QTY_MAX:
        raise ValueError("数量无效")


def check_red_sale_invoice(head: dict | None, lines: list[dict] | None) -> None:
    """表头白名单；lines 省略表示整单，给了就不能是空列表。"""
    for low, value in _lowered(head or {}).items():
        _head_field(low, value)
    if lines is None:
        return
    if not lines:
        raise ValueError("lines 不能是空列表；整单生成请省略 lines")
    seen: set[int] = set()
    for row in lines:
        _line(row, seen)
