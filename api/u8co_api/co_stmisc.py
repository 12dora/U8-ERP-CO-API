"""形态转换单、调拨申请单、盘点单（另有货位调整单）的数量校验，与桥 StockDom.MiscQtyOk 同一规则。

数量是有限数，大于 0（盘点单的实盘、账面数量和调拨申请单的核准数量可以是 0），不超过 1000000000000，最多 6 位小数。
只查送来的数量字段（键不分大小写）；必填、成组等其余规则仍在桥里。
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

_QTY_MAX = Decimal(1000000000000)
# 类型 → （数量字段小写，是否可以为 0，可以为 0 时的出错说明）。
_QTY_KEYS = {
    "shape_change": (("iavquantity", False, ""),),
    "transfer_request": (("itvquantity", False, ""), ("itvchkquantity", True, "核准数量必须大于或等于 0")),
    "stock_check": (("icvcquantity", True, "盘点数量必须大于或等于 0"), ("icvquantity", True, "盘点数量必须大于或等于 0")),
    # 货位调整单：每行数量（货位不同、结存够由桥核对）。
    "position_adjust": (("iquantity", False, ""),),
}


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    try:
        num = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return num if num.is_finite() else None


def check_stmisc_qty(value: object, zero_ok: bool, label: str = "数量必须大于或等于 0") -> None:
    num = _decimal(value)
    if num is None or num < 0 or (num == 0 and not zero_ok) or num > _QTY_MAX:
        raise ValueError(label if zero_ok else "数量必须大于 0")
    if num != round(num, 6):
        raise ValueError("数量最多 6 位小数")


def check_stmisc_lines(kind: str, lines: list[dict] | None) -> None:
    keys = _QTY_KEYS.get(kind)
    if not keys or not lines:
        return
    for row in lines:
        low = {str(key).lower(): value for key, value in row.items()}
        for name, zero_ok, label in keys:
            if name in low:
                check_stmisc_qty(low[name], zero_ok, label)
        _chk_within(low)


def _chk_within(low: dict) -> None:
    # 调拨申请：同一行同时给了核准数量和申请数量时，核准不能大于申请（只给一边的由桥按整行复核）。
    chk = _decimal(low.get("itvchkquantity"))
    qty = _decimal(low.get("itvquantity"))
    if chk is not None and qty is not None and chk > qty:
        raise ValueError("核准数量不能大于申请数量")
