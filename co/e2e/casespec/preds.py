"""回读单元格的断言，以及把正数写进 bag。不连接服务器。"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,20}\Z")
_NUM = re.compile(r"^[0-9]+(\.[0-9]+)?\Z")
_TOL = Decimal("0.0001")


def store_cell(bag: dict[str, str], key: str, cell: str) -> None:
    if _KEY.fullmatch(key or "") is None:
        raise ValueError("键不合法")
    text = cell.strip()
    if _NUM.fullmatch(text) is None:
        raise ValueError("回读不能保存")
    bag[key] = text


def _gt0(cell: str, _arg: str, _bag: dict[str, str], _op: str) -> bool:
    return Decimal(cell) > 0


def _empty(cell: str, _arg: str, _bag: dict[str, str], _op: str) -> bool:
    return cell == ""


def _nonempty(cell: str, _arg: str, _bag: dict[str, str], _op: str) -> bool:
    return cell != ""


def _eq(cell: str, arg: str, _bag: dict[str, str], _op: str) -> bool:
    return arg != "" and cell == arg


def _near(cell: str, arg: str, bag: dict[str, str], shift: Decimal) -> bool:
    return abs(Decimal(cell) - Decimal(bag[arg]) - shift) <= _TOL


def _plus1(cell: str, arg: str, bag: dict[str, str], _op: str) -> bool:
    return _near(cell, arg, bag, Decimal(1))


def _same(cell: str, arg: str, bag: dict[str, str], _op: str) -> bool:
    return _near(cell, arg, bag, Decimal(0))


def _shift(cell: str, arg: str, bag: dict[str, str], _op: str) -> bool:
    # shift:<带符号的数>:<键>，例如 shift:-2:sr_sofh 表示比 bag 里记下的值少 2。
    step, sep, key = arg.partition(":")
    if sep != ":" or key == "":
        return False
    return _near(cell, key, bag, Decimal(step))


def _double(cell: str, arg: str, bag: dict[str, str], _op: str) -> bool:
    # 数量翻倍后含税金额按分舍入，允许差 0.05。
    new = Decimal(cell)
    old = Decimal(bag[arg])
    if old <= 0 or new <= 0:
        return False
    return abs(new - old * 2) <= Decimal("0.05")


def _pair(cell: str) -> tuple[str, str]:
    left, sep, right = cell.partition("|")
    if sep != "|":
        raise ValueError("回读缺分隔")
    return left.strip(), right.strip()


def _line_closed(cell: str, _arg: str, _bag: dict[str, str], _op: str) -> bool:
    left, right = _pair(cell)
    return left != "" and right == ""


def _line_open(cell: str, _arg: str, _bag: dict[str, str], _op: str) -> bool:
    left, right = _pair(cell)
    return left == "" and right == ""


def _added(cell: str, arg: str, bag: dict[str, str], _op: str) -> bool:
    left, sep, right = arg.partition("+")
    if sep != "+" or left == "" or right == "":
        return False
    return _near(cell, left, bag, Decimal(bag[right]))


def _who(cell: str, _arg: str, _bag: dict[str, str], opname: str) -> bool:
    name = opname.strip()
    return name != "" and cell.strip() == name


def _saleupd(cell: str, arg: str, bag: dict[str, str], opname: str) -> bool:
    bits = cell.split("|")
    if len(bits) != 4 or opname.strip() == "":
        return False
    count, qty, flag, who = bits
    if count != arg or flag != "1" or who.strip() != opname.strip():
        return False
    return abs(Decimal(qty) - Decimal(bag["so_qty"]) - 1) <= _TOL


_PREDS = {
    "gt0": _gt0,
    "empty": _empty,
    "nonempty": _nonempty,
    "eq": _eq,
    "plus1": _plus1,
    "same": _same,
    "shift": _shift,
    "double": _double,
    "lineclosed": _line_closed,
    "lineopen": _line_open,
    "saleupd": _saleupd,
    "added": _added,
    "opname": _who,
}


def judge_pred(cell: str, pred: str, bag: dict[str, str], opname: str) -> tuple[str, str]:
    kind, _, arg = pred.partition(":")
    checker = _PREDS.get(kind)
    if checker is None:
        return "FAIL", "未知断言"
    try:
        ok = checker(cell.strip(), arg, bag, opname)
    except (InvalidOperation, KeyError, ValueError):
        return "FAIL", "回读 " + (cell or "空")
    if ok:
        return "PASS", cell or "空"
    return "FAIL", "回读 " + (cell or "空") + " 期望 " + pred
