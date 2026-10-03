"""公司间对账的配对算法（纯函数，不访问桥）：逐行贪心 1:1，或发票按月比金额。

逐行：存货按对照换成同一个 id，数量按两位小数比较。第一轮日期、存货、数量完全相同；第二轮只对 match=code 的存货，
在 window_days 天内找日期最近的（同样近时取日期早、id 小的）。每一行最多配一次。
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict, deque
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from u8co_api.co_ic_map import MATCH_CODE, IcInventory

_CENT = Decimal("0.01")
MONTH_TOLERANCE = Decimal("0.05")


@dataclass(frozen=True, eq=False)
class IcLine:
    """一方的一条明细。inv 是对照里的存货（没有对照为 None），qty、amount 可能缺。"""

    acc: str
    type: str
    id: int | None
    code: str | None
    date: dt.date
    line_id: int | str | None
    inv_code: str | None
    qty: Decimal | None
    amount: Decimal | None
    inv: IcInventory | None

    def out(self) -> dict:
        return {
            "acc": self.acc,
            "type": self.type,
            "id": self.id,
            "code": self.code,
            "date": self.date.isoformat(),
            "line_id": self.line_id,
            "inv_code": self.inv_code,
            "qty": _text(self.qty),
            "amount": _text(self.amount),
        }

    def order(self) -> tuple:
        return (self.date, self.id or 0, str(self.line_id or ""))


@dataclass(frozen=True)
class Pair:
    seller: IcLine
    buyer: IcLine
    way: str

    def out(self) -> dict:
        inv = self.seller.inv
        return {
            "seller": self.seller.out(),
            "buyer": self.buyer.out(),
            "inv_id": inv.id if inv is not None else None,
            "qty": _text(cents(self.seller.qty)),
            "pass": self.way,
            "days": (self.buyer.date - self.seller.date).days,
        }


@dataclass(frozen=True)
class LineMatch:
    pairs: list[Pair]
    seller_left: list[IcLine]
    buyer_left: list[IcLine]


def number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        found = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return found if found.is_finite() else None


def cents(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(_CENT, rounding=ROUND_HALF_UP)


def _text(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def _money(value: Decimal) -> str:
    # 金额按两位小数给出（513.00），明细的数量、金额去掉末尾的 0。
    return format(value.quantize(_CENT, rounding=ROUND_HALF_UP), "f")


def _key(line: IcLine) -> tuple | None:
    if line.inv is None or line.qty is None:
        return None
    return (line.inv.id, line.date, cents(line.qty))


def match_lines(seller: list[IcLine], buyer: list[IcLine], window_days: int) -> LineMatch:
    used: set[int] = set()
    pairs = _exact(sorted(seller, key=IcLine.order), sorted(buyer, key=IcLine.order), used)
    done = {id(pair.seller) for pair in pairs}
    rest = [line for line in sorted(seller, key=IcLine.order) if id(line) not in done]
    if window_days > 0:
        pairs += _window(rest, [line for line in buyer if id(line) not in used], window_days, used)
        done = {id(pair.seller) for pair in pairs}
    seller_left = [line for line in sorted(seller, key=IcLine.order) if id(line) not in done]
    buyer_left = [line for line in sorted(buyer, key=IcLine.order) if id(line) not in used]
    return LineMatch(pairs, seller_left, buyer_left)


def _exact(seller: list[IcLine], buyer: list[IcLine], used: set[int]) -> list[Pair]:
    buckets: dict[tuple, deque[IcLine]] = defaultdict(deque)
    for line in buyer:
        key = _key(line)
        if key is not None:
            buckets[key].append(line)
    pairs: list[Pair] = []
    for line in seller:
        key = _key(line)
        found = buckets.get(key) if key is not None else None
        if found:
            other = found.popleft()
            used.add(id(other))
            pairs.append(Pair(line, other, "exact"))
    return pairs


def _window(seller: list[IcLine], buyer: list[IcLine], days: int, used: set[int]) -> list[Pair]:
    # 只有 match=code 的存货放宽日期；qty_date 的存货只认当天。
    pool: dict[tuple, list[IcLine]] = defaultdict(list)
    for line in sorted(buyer, key=IcLine.order):
        if line.inv is not None and line.qty is not None and line.inv.match == MATCH_CODE:
            pool[(line.inv.id, cents(line.qty))].append(line)
    pairs: list[Pair] = []
    for line in seller:
        if line.inv is None or line.qty is None or line.inv.match != MATCH_CODE:
            continue
        other = _nearest(line, pool.get((line.inv.id, cents(line.qty)), []), days, used)
        if other is not None:
            used.add(id(other))
            pairs.append(Pair(line, other, "window"))
    return pairs


def _nearest(line: IcLine, candidates: list[IcLine], days: int, used: set[int]) -> IcLine | None:
    best: tuple | None = None
    chosen: IcLine | None = None
    for other in candidates:
        if id(other) in used:
            continue
        gap = abs((other.date - line.date).days)
        if gap > days:
            continue
        rank = (gap, other.order())
        if best is None or rank < best:
            best, chosen = rank, other
    return chosen


def left_out(line: IcLine) -> dict:
    data = line.out()
    data["inv_id"] = line.inv.id if line.inv is not None else None
    data["reason"] = "unmapped" if line.inv is None else "no_counterpart"
    return data


def clip(seller: list[IcLine], buyer: list[IcLine]) -> dt.date | None:
    """两边最后一笔日期中较早的那天；有一边没有明细时为 None（不截）。"""
    if not seller or not buyer:
        return None
    return min(max(line.date for line in seller), max(line.date for line in buyer))


def rate(hit: int, total: int) -> float | None:
    return None if total == 0 else round(hit / total, 4)


@dataclass
class _Month:
    amount: Decimal
    docs: set


def _months(lines: list[IcLine]) -> dict[str, _Month]:
    found: dict[str, _Month] = {}
    for line in lines:
        month = line.date.strftime("%Y-%m")
        slot = found.setdefault(month, _Month(Decimal(0), set()))
        slot.amount += line.amount or Decimal(0)
        slot.docs.add(line.id)
    return found


def match_months(seller: list[IcLine], buyer: list[IcLine]) -> tuple[list[dict], list[dict], list[dict]]:
    """发票按月：两边当月价税合计差额不超过 0.05 算配上。返回（配上的月、卖方没配上的月、买方没配上的月）。"""
    sold, bought = _months(seller), _months(buyer)
    matched: list[dict] = []
    seller_left: list[dict] = []
    buyer_left: list[dict] = []
    for month in sorted(set(sold) | set(bought)):
        mine, theirs = sold.get(month), bought.get(month)
        if mine is not None and theirs is not None and abs(mine.amount - theirs.amount) <= MONTH_TOLERANCE:
            matched.append(_month_pair(month, mine, theirs))
            continue
        if mine is not None:
            seller_left.append(_month_row(month, mine, theirs))
        if theirs is not None:
            buyer_left.append(_month_row(month, theirs, mine))
    return matched, seller_left, buyer_left


def _month_pair(month: str, mine: _Month, theirs: _Month) -> dict:
    return {
        "month": month,
        "seller_amount": _money(mine.amount),
        "buyer_amount": _money(theirs.amount),
        "diff": _money(mine.amount - theirs.amount),
        "seller_docs": len(mine.docs),
        "buyer_docs": len(theirs.docs),
    }


def _month_row(month: str, mine: _Month, other: _Month | None) -> dict:
    return {
        "month": month,
        "amount": _money(mine.amount),
        "docs": len(mine.docs),
        "other_amount": None if other is None else _money(other.amount),
        "reason": "no_counterpart" if other is None else "amount_differs",
    }
