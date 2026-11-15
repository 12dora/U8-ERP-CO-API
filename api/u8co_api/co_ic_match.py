"""POST /v1/co/reports/intercompany_match：两个账套并行取明细，再按 co_ic_greedy 配对。

单据表头用 vouchers/search（往来单位 + 日期）。销售出库、采购入库的明细取库存台账（reports/stock_ledger，
只查对照里的存货，按表头 id 过滤），其余类型用 vouchers/load_many 逐张读。
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

from u8co_api.co_ic_core import allow_accs, call_acc, ic_map_of, paged, remember_ic, run_jobs, too_many
from u8co_api.co_ic_greedy import IcLine, clip, left_out, match_lines, match_months, number, rate
from u8co_api.co_ic_map import IcGroup
from u8co_api.co_models_ic import IcLogin, IcMatchIn
from u8co_api.errors import ApiError, conflict

SEARCH = "/v1/vouchers/search"
LEDGER = "/v1/reports/stock_ledger"
LOAD_MANY = "/v1/vouchers/load_many"
LEDGER_TYPES = frozenset({"sale_out", "purchase_in"})
# 按 SQL 读取的类型一次 20 张，U8 组件读取的一次 5 张（同 vouchers/load_many）。
SQL_LOAD = frozenset({"purchase_invoice"})
SEARCH_PAGE, SEARCH_PAGES = 200, 50
LEDGER_PAGE, LEDGER_PAGES = 1000, 20
MAX_LOAD_DOCS = 300
_LINE_ID_KEYS = ("autoid", "idlsid", "id")
_AMOUNT_KEYS = ("isum", "inatsum")


class _Side:
    """一方要取的数据：登录、单据类型、往来单位编码、日期区间、公司组。"""

    def __init__(self, login: IcLogin, kind: str, partner: str, body: IcMatchIn, group: IcGroup) -> None:
        self.login = login
        self.kind = kind
        self.partner = partner
        self.date_from = body.date_from
        self.date_to = body.date_to
        self.group = group
        self.warnings: list[str] = []

    @property
    def acc(self) -> str:
        return self.login.acc

    def line(self, head: dict, inv_code: str | None, row: dict) -> IcLine | None:
        day = _day(row.get("date") or head.get("doc_date"))
        if day is None:
            self.warnings.append(f"bad_date:{self.acc}:{head.get('id')}")
            return None
        inv = self.group.inventory_of(self.acc, inv_code) if inv_code else None
        return IcLine(
            acc=self.acc,
            type=self.kind,
            id=head.get("id") if isinstance(head.get("id"), int) else None,
            code=_str(head.get("code")),
            date=day,
            line_id=row.get("line_id"),
            inv_code=inv_code,
            qty=number(row.get("qty")),
            amount=number(row.get("amount")),
            inv=inv,
        )


def _day(value: object) -> dt.date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def _str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _lower(row: object) -> dict:
    return {str(key).lower(): value for key, value in row.items()} if isinstance(row, dict) else {}


def _first(row: dict, keys: tuple[str, ...]) -> object:
    for key in keys:
        if row.get(key) is not None:
            return row[key]
    return None


def side_lines(request, side: _Side) -> dict:
    """一方的全部明细（在工作线程里跑，按顺序调桥）。返回 {lines, warnings}。"""
    search = {"type": side.kind, "partner": side.partner, "date_from": side.date_from, "date_to": side.date_to}
    heads = paged(request, side.login, SEARCH, search, page_size=SEARCH_PAGE, max_pages=SEARCH_PAGES)
    by_id = {head["id"]: head for head in heads if isinstance(head.get("id"), int)}
    loader = _ledger_lines if side.kind in LEDGER_TYPES else _loaded_lines
    lines = loader(request, side, by_id) if by_id else []
    return {"lines": lines, "warnings": side.warnings}


def _ledger_lines(request, side: _Side, by_id: dict[int, dict]) -> list[IcLine]:
    codes = side.group.inventory_codes(side.acc)
    if not codes:
        side.warnings.append(f"no_inventory_map:{side.acc}")
        return []
    side.warnings.append(f"mapped_inventory_only:{side.acc}")
    qty_key = "out_qty" if side.kind == "sale_out" else "in_qty"
    body = {"date_from": side.date_from, "date_to": side.date_to, "include_unverified": True}
    found: list[IcLine] = []
    for code in codes:
        rows = paged(request, side.login, LEDGER, dict(body, inv=code), page_size=LEDGER_PAGE, max_pages=LEDGER_PAGES)
        for row in rows:
            head = by_id.get(row.get("id")) if row.get("type") == side.kind else None
            if head is None:
                continue
            line = side.line(head, code, {"date": row.get("date"), "line_id": row.get("line_id"), "qty": row.get(qty_key)})
            if line is not None:
                found.append(line)
    return found


def _loaded_lines(request, side: _Side, by_id: dict[int, dict]) -> list[IcLine]:
    ids = sorted(by_id)
    if len(ids) > MAX_LOAD_DOCS:
        raise too_many(side.acc, MAX_LOAD_DOCS)
    size = 20 if side.kind in SQL_LOAD else 5
    found: list[IcLine] = []
    for start in range(0, len(ids), size):
        reply = call_acc(request, side.login, LOAD_MANY, {"type": side.kind, "ids": ids[start : start + size]})
        for item in reply.get("items") or []:
            found += _doc_lines(side, by_id, item)
    return found


def _doc_lines(side: _Side, by_id: dict[int, dict], item: object) -> list[IcLine]:
    if not isinstance(item, dict):
        return []
    head = by_id.get(item.get("id"))
    if head is None or item.get("error") is not None:
        side.warnings.append(f"load_failed:{side.acc}:{item.get('id')}")
        return []
    found: list[IcLine] = []
    for raw in item.get("lines") or []:
        row = _lower(raw)
        inv_code = _str(row.get("cinvcode"))
        data = {"line_id": _first(row, _LINE_ID_KEYS), "qty": row.get("iquantity"), "amount": _first(row, _AMOUNT_KEYS)}
        line = side.line(head, inv_code, data)
        if line is not None:
            found.append(line)
    return found


def party_codes(group: IcGroup, body: IcMatchIn) -> tuple[str, str]:
    seller, buyer = body.seller.acc, body.buyer.acc
    customer = group.customer_code(seller, buyer)
    if customer is None:
        raise _unmapped(f"公司间对照里没有账套 {buyer} 在账套 {seller} 的客户编码（as_customer）")
    vendor = group.vendor_code(buyer, seller)
    if vendor is None:
        raise _unmapped(f"公司间对照里没有账套 {seller} 在账套 {buyer} 的供应商编码（as_vendor）")
    return customer, vendor


def _unmapped(message: str) -> ApiError:
    error = conflict(message, "ic_party_unmapped")
    error.hint = "管理员在 U8CO_IC_MAP_FILE 里补上往来单位编码"
    return error


def run_match(request, caller, action: str, _bridge_path: str, body: IcMatchIn) -> dict:
    request.state.ic_caller = caller
    remember_ic(request, action, body.logins, body.audit_ref())
    group = ic_map_of(request).group_of({login.acc for login in body.logins})
    allow_accs(request, [login.acc for login in body.logins], caller)
    customer, vendor = party_codes(group, body)
    logins = {login.acc: login for login in body.logins}
    sides = {
        body.seller.acc: _Side(logins[body.seller.acc], body.seller.type, customer, body, group),
        body.buyer.acc: _Side(logins[body.buyer.acc], body.buyer.type, vendor, body, group),
    }
    jobs: dict[str, Callable[[], dict]] = {acc: _job(request, side) for acc, side in sides.items()}
    results = run_jobs(request, jobs)
    for acc, result in results.items():
        if not result.ok:
            error = result.error or {}
            raise ApiError(result.status, error.get("code", "unavailable"), f"账套 {acc}：{error.get('message', '')}")
    seller = results[body.seller.acc].body or {}
    buyer = results[body.buyer.acc].body or {}
    out = compare(body, seller.get("lines", []), buyer.get("lines", []))
    out.update(
        group=group.id,
        seller={"acc": body.seller.acc, "type": body.seller.type, "partner": customer},
        buyer={"acc": body.buyer.acc, "type": body.buyer.type, "partner": vendor},
        warnings=list(dict.fromkeys(seller.get("warnings", []) + buyer.get("warnings", []))),
    )
    return out


def _job(request, side: _Side) -> Callable[[], dict]:
    return lambda: side_lines(request, side)


def compare(body: IcMatchIn, seller: list[IcLine], buyer: list[IcLine]) -> dict:
    """截到 clip_date 后配对。逐行和按月两种输出，rates、clipped 按条（逐行）或按月计。"""
    until = clip(seller, buyer)
    kept_s = [line for line in seller if until is None or line.date <= until]
    kept_b = [line for line in buyer if until is None or line.date <= until]
    out: dict = {
        "ok": True,
        "mode": body.mode(),
        "clip_date": until.isoformat() if until is not None else None,
        "window_days": body.window_days,
        "clipped": {"seller": len(seller) - len(kept_s), "buyer": len(buyer) - len(kept_b)},
    }
    if body.mode() == "month":
        matched, left_s, left_b = match_months(kept_s, kept_b)
        hit = len(matched)
        out.update(matched=matched, unmatched_seller=left_s, unmatched_buyer=left_b)
        out["rates"] = {"seller": rate(hit, hit + len(left_s)), "buyer": rate(hit, hit + len(left_b))}
        return out
    found = match_lines(kept_s, kept_b, body.window_days)
    out.update(
        matched=[pair.out() for pair in found.pairs],
        unmatched_seller=[left_out(line) for line in found.seller_left],
        unmatched_buyer=[left_out(line) for line in found.buyer_left],
        rates={"seller": rate(len(found.pairs), len(kept_s)), "buyer": rate(len(found.pairs), len(kept_b))},
    )
    return out
