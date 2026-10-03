"""退货申请单（sale_return_apply，销售管理卡片 SA31）新增、修改的专用校验。与桥 ReturnsApplyReq 一致。

新增：每行参照一条已审核、未关闭的蓝字发货单行（source_line_id = 发货单行 iDLsID），quantity 填正数（桥写负数）；
表头只收 dDate（yyyy-MM-dd）、cMemo、cDepCode、cPersonCode、cDefine1–16，表体另收 cWhCode、cMemo、cReasonCode、cDefine22–37。
修改：只改已有行（op=update，line_id 是申请单行 AutoID），可改 iQuantity（正数）、cMemo、cReasonCode、cDefine22–37；
表头只收 dDate、cMemo、cDefine1–16。不能新增、删除行。
"""

from __future__ import annotations

import math
import re

KIND = "sale_return_apply"
CREATE_HELP = (
    "；退货申请单（sale_return_apply）：每行 source_line_id 是已审核蓝字发货单的行 iDLsID、quantity 填正数（桥写负数，"
    "不超过发货数量减累计退货数量和其他未完成的申请），另可带 cWhCode、cMemo、cReasonCode、cDefine22–37；"
    "表头只收 dDate、cMemo、cDepCode、cPersonCode、cDefine1–16，客户、币种等照发货单；所有行须同一客户、同一币种"
)
APPLY_UPDATE_HELP = (
    "退货申请单只改已有行（op=update，line_id 是 AutoID）：iQuantity 填正数、cMemo、cReasonCode、cDefine22–37；"
    "表头只收 dDate、cMemo、cDefine1–16；只改未审核、没有退货单的申请单"
)
APPLY_GENERATE_HELP = (
    "；参照退货申请单（source_type=sale_return_apply）时 source_line_id 是申请单行 AutoID（所选行须指向同一张蓝字发货单，"
    "不超过申请数量减已退数量），没给 cWhCode 时用申请行的仓库"
)
_ID_MAX = 2147483647
_QTY_MAX = 10**12
_CREATE_HEAD = frozenset({"ddate", "cmemo", "cdepcode", "cpersoncode"})
_EDIT_HEAD = frozenset({"ddate", "cmemo"})
_CREATE_LINE = frozenset({"cwhcode", "cmemo", "creasoncode"})
_EDIT_LINE = frozenset({"iquantity", "cmemo", "creasoncode"})
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUMBERED = re.compile(r"cdefine([1-9]\d?)")


def _allowed(key: str, listed: frozenset[str], first: int, last: int) -> bool:
    low = key.lower()
    if low in listed:
        return True
    match = _NUMBERED.fullmatch(low)
    return match is not None and first <= int(match.group(1)) <= last


def _check_head(head: dict | None, listed: frozenset[str]) -> None:
    for key, value in (head or {}).items():
        if not _allowed(key, listed, 1, 16):
            raise ValueError("不能设置字段 " + key)
        if key.lower() == "ddate" and (type(value) is not str or _DATE.fullmatch(value) is None):
            raise ValueError("dDate 必须是 yyyy-MM-dd")


def _strict_id(row: dict, name: str, seen: set[int]) -> None:
    raw = row.get(name)
    if type(raw) is not int or raw < 1 or raw > _ID_MAX:
        raise ValueError(name + " 无效")
    if raw in seen:
        raise ValueError(name + " 重复")
    seen.add(raw)


def _quantity(value: object, name: str) -> None:
    if type(value) not in (int, float):
        raise ValueError(name + " 无效")
    number = float(str(value))
    if not math.isfinite(number) or number <= 0 or number > _QTY_MAX or round(number, 6) != number:
        raise ValueError(name + " 必须是大于 0、不超过 1000000000000、最多 6 位小数的数")


def check_return_apply_create(kind: str, head: dict | None, lines: list[dict]) -> None:
    if kind != KIND:
        return
    _check_head(head, _CREATE_HEAD)
    seen: set[int] = set()
    for row in lines:
        if "source_line_id" not in row:
            raise ValueError("退货申请单新增的每一行都要参照蓝字发货单行（source_line_id）")
        _strict_id(row, "source_line_id", seen)
        _quantity(row.get("quantity"), "quantity")
        for key in row:
            if key not in ("source_line_id", "quantity") and not _allowed(key, _CREATE_LINE, 22, 37):
                raise ValueError("不能设置字段 " + key)


def _edit_row(row: dict, seen: set[int]) -> None:
    if row.get("op") != "update":
        raise ValueError("退货申请单修改只能改已有行（op=update），不能新增或删除行")
    _strict_id(row, "line_id", seen)
    fields = [key for key in row if key not in ("op", "line_id")]
    if not fields:
        raise ValueError("修改行至少再改一个字段")
    for key in fields:
        if not _allowed(key, _EDIT_LINE, 22, 37):
            raise ValueError("不能设置字段 " + key)
        if key.lower() == "iquantity":
            _quantity(row[key], key)


def check_apply_update(kind: str, head: dict | None, lines: list[dict] | None) -> bool:
    """退货申请单返回 True（已按自己的名单校验完）；其它类型返回 False。"""
    if kind != KIND:
        return False
    _check_head(head, _EDIT_HEAD)
    seen: set[int] = set()
    for row in lines or []:
        _edit_row(row, seen)
    return True
