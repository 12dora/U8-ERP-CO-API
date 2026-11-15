"""无来源新增（/v1/co/vouchers/create）：发货单、先开票销售发票、到货单、材料出库单。与桥 SrcLessReq 一致。

账套打开「普通销售必有订单」（SA.bMustSO_ptxs）、「普通业务必有订单」（PU.bPTHavePO）或「领料必有订单」
（ST.ballowAddnewVouch）时，桥在登录后返回 409 state_mismatch，这里只做字段名单、必填和数量的校验。
"""

from __future__ import annotations

import math
import re
from decimal import Decimal

SRCLESS_CREATE = ("dispatch", "sale_invoice", "arrival", "material_out")
# 新增单据类型说明（co_models_types.CREATE_TYPE_HELP）里的「无来源新增」小节。
SRCLESS_HELP = (
    "无来源新增",
    (
        "发货单（不挂销售订单）、到货单（不挂采购订单）、材料出库单（来源库存）",
        "销售发票（先开票）：U8 另生成发货单，响应另给 dispatch_id",
        "账套打开对应的「必有订单」选项时 409",
        "销售出库单（sale_out，来源库存）：表头必填 cwhcode、ccuscode、cdepcode",
        "无来源销售出库单：账套已启用销售管理或库存选项设为由销售系统生成时 409，须参照发货单生成",
    ),
)
_SA_HEAD = frozenset(
    "ccuscode cstcode cdepcode cpersoncode cexch_name iexchrate itaxrate ddate cmemo cshipaddress cscode cpaycode".split()
)
_SA_LINE = frozenset(
    "cwhcode cinvcode iquantity inum cunitid iquotedprice iunitprice itaxunitprice itaxrate kl kl2 cbatch cmemo".split()
)
_PU_HEAD = frozenset("cvencode cdepcode cpersoncode cptcode ddate cmemo".split())
_PU_LINE = frozenset("cwhcode cinvcode iquantity ioricost ioritaxcost itaxrate".split())
_ST_HEAD = frozenset("cwhcode crdcode ddate cmemo cdepcode cpersoncode".split())
# 材料出库行另可带货位 cposition：仓库启用货位管理时必填、须为该仓库的末级货位，未启用时不能填（桥核对）。
_ST_LINE = frozenset("cinvcode iquantity cbatch cposition cbmemo".split())
_REQUIRED_HEAD = {
    "dispatch": ("ccuscode", "cstcode"),
    "sale_invoice": ("ccuscode", "cstcode"),
    "arrival": ("cvencode",),
    "material_out": ("cwhcode",),
}
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_QTY_MAX = 10**12


def _span(key: str, prefix: str, low: int, high: int) -> bool:
    tail = key[len(prefix) :]
    if not key.startswith(prefix) or not tail.isdigit() or tail != str(int(tail)):
        return False
    return low <= int(tail) <= high


def head_allowed(kind: str, key: str) -> bool:
    if kind in ("dispatch", "sale_invoice"):
        return key in _SA_HEAD or _span(key, "cdefine", 1, 16) or (kind == "sale_invoice" and key == "cvouchtype")
    if kind == "arrival":
        return key in _PU_HEAD
    return key in _ST_HEAD or _span(key, "cdefine", 1, 16)


def line_allowed(kind: str, key: str) -> bool:
    if kind in ("dispatch", "sale_invoice"):
        return key in _SA_LINE or _span(key, "cfree", 1, 10) or _span(key, "cdefine", 22, 37)
    if kind == "arrival":
        return key in _PU_LINE
    return key in _ST_LINE


def _text(row: dict, name: str) -> str:
    for key, value in row.items():
        if key.lower() == name:
            return "" if value is None else str(value).strip()
    return ""


def _check_side(kind: str, row: dict, head: bool) -> None:
    seen: set[str] = set()
    for key in row:
        low = key.lower()
        if not (head_allowed(kind, low) if head else line_allowed(kind, low)):
            raise ValueError("不能设置字段 " + key)
        if low in seen:
            raise ValueError("字段重复 " + key)
        seen.add(low)


def _quantity(row: dict) -> None:
    raw = next((value for key, value in row.items() if key.lower() == "iquantity"), None)
    try:
        number = float(raw) if type(raw) in (int, float, str) and raw != "" else math.nan
    except ValueError:
        number = math.nan
    if not math.isfinite(number) or number <= 0 or number > _QTY_MAX:
        raise ValueError("iquantity 必须大于 0 且不超过 1000000000000")
    # 与桥 SrcLessReq.Qty 一致：最多 6 位小数。
    exact = Decimal(str(raw))
    if exact != exact.quantize(Decimal("0.000001")):
        raise ValueError("iquantity 最多 6 位小数")


def _required_line(kind: str) -> tuple[str, ...]:
    if kind in ("dispatch", "sale_invoice"):
        return ("cwhcode", "cinvcode", "iquantity")
    return ("cinvcode", "iquantity")


def _check_head(kind: str, head: dict) -> None:
    _check_side(kind, head, True)
    for name in _REQUIRED_HEAD[kind]:
        if not _text(head, name):
            raise ValueError("必须填写 " + name)
    day = _text(head, "ddate")
    if day and _DATE.fullmatch(day) is None:
        raise ValueError("ddate 必须是 yyyy-MM-dd")
    if kind == "sale_invoice" and _text(head, "cvouchtype") not in ("", "26", "27"):
        raise ValueError("cvouchtype 只能是 26 或 27")


def check_srcless_create(kind: str, head: dict, lines: list[dict]) -> None:
    """表头、表体字段名单、必填、数量（大于 0）。"""
    if kind not in SRCLESS_CREATE:
        return
    _check_head(kind, head)
    for row in lines:
        _check_side(kind, row, False)
        for name in _required_line(kind):
            if not _text(row, name):
                raise ValueError("明细必须填写 " + name)
        _quantity(row)
