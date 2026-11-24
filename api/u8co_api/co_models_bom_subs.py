"""物料清单（type=bom）明细行上的替代料（substitutes）和定位符（locators），以及物料清单校验共用的数值检查。

与桥 BomSubReq 一致：替代料每项 inv_code 必填，sequence（1 到 10000，缺省 1）、factor（大于 0，缺省 1）、
replace_flag（布尔，缺省 false）、eff_begin / eff_end（yyyy-MM-dd，缺省取子件行的起止日期）可省，同一行里存货不重复；
定位符每项 loc 必填（最长 255），同一行里不重复；各最多 50 项，空数组表示清空。
替代料每项的校验（check_subs）与生产订单子件的替代料共用（co_models_mo_subs，另收 defines）。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from u8co_api.co_models_mo import _date, _get, _only, _text

_QTY_MAX = Decimal(10**12)
ITEMS_MAX = 50
_SUB_KEYS = ("inv_code", "sequence", "factor", "replace_flag", "eff_begin", "eff_end")


def check_nested(row: dict) -> None:
    """明细行上带了 substitutes / locators 时校验形状（null 视同没带）。"""
    subs = _get(row, "substitutes")
    if subs is not None:
        check_subs(subs)
    locs = _get(row, "locators")
    if locs is not None:
        names: set[str] = set()
        for item in _items(locs, "locators"):
            _only(item, dict.fromkeys(("loc",)))
            _text(item, "loc", 255, required=True)
            loc = _get(item, "loc")
            if not isinstance(loc, str) or not loc.strip():
                raise ValueError("缺少字段 loc")
            if loc.strip() in names:
                raise ValueError("定位符重复：" + loc.strip())
            names.add(loc.strip())


def check_subs(value: object, defines: Callable[[object], None] | None = None) -> None:
    """一组替代料（最多 50 项，存货不重复）；defines 给出时每项另收 defines 并用它细查。"""
    seen: set[str] = set()
    for item in _items(value, "substitutes"):
        _sub(item, seen, defines)


def _items(value: object, key: str) -> list[dict]:
    if not isinstance(value, list):
        raise ValueError(key + " 必须是数组")
    if len(value) > ITEMS_MAX:
        raise ValueError(f"{key} 不能超过 {ITEMS_MAX} 项")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(key + " 的每一项必须是对象")
    return value


def _sub(item: dict, seen: set[str], defines: Callable[[object], None] | None) -> None:
    _only(item, dict.fromkeys(_SUB_KEYS + (("defines",) if defines else ())))
    if defines is not None and _get(item, "defines") is not None:
        defines(_get(item, "defines"))
    _text(item, "inv_code", 60, required=True)
    code = _get(item, "inv_code")
    if not isinstance(code, str) or not code.strip():
        raise ValueError("缺少字段 inv_code")
    if code.strip().lower() in seen:
        raise ValueError("替代料存货重复：" + code.strip())
    seen.add(code.strip().lower())
    if _get(item, "sequence") is not None:
        check_int(_get(item, "sequence"), "sequence", 10000)
    if _get(item, "factor") is not None:
        check_qty(_get(item, "factor"), "factor")
    if _get(item, "replace_flag") is not None and not isinstance(_get(item, "replace_flag"), bool):
        raise ValueError("replace_flag 必须是布尔值")
    for key in ("eff_begin", "eff_end"):
        if _get(item, key) is not None:
            _date(_get(item, key), key)
    begin, end = _get(item, "eff_begin"), _get(item, "eff_end")
    if begin is not None and end is not None and _date(begin, "eff_begin") > _date(end, "eff_end"):
        raise ValueError("eff_begin 不能晚于 eff_end")


def check_int(value: object, key: str, top: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(key + " 必须是整数")
    if not 1 <= value <= top:
        raise ValueError(f"{key} 必须是 1 到 {top} 的整数")
    return value


def _number(value: object, key: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(key + " 必须是数")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(key + " 必须是有限数")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(key + " 必须是数") from exc


def check_qty(value: object, key: str) -> None:
    qty = _number(value, key)
    if qty <= 0 or qty > _QTY_MAX:
        raise ValueError(key + " 必须大于 0 且不超过 1000000000000")
    if qty != qty.quantize(Decimal("0.000001")):
        raise ValueError(key + " 最多 6 位小数")


def check_rate(value: object, key: str) -> None:
    rate = _number(value, key)
    if rate < 0 or rate >= 100:
        raise ValueError(key + " 必须是 0 到小于 100 的数")
    if rate != rate.quantize(Decimal("0.001")):
        raise ValueError(key + " 最多 3 位小数")
