"""多账套汇总 /v1/co/reports/aggregate：每个账套读完同一张报表，再只按公司间对照合计。

现存量按对照的存货 id，往来按往来单位所代表的公司，科目余额按对照的逻辑科目；对照之外的不相加，计数列在 unmapped。
部分账套失败照常返回（失败的不计入合计），全部失败 503。
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from typing import Any

from u8co_api.co_ic_core import FanResult, IcGroup, allow_accs, ic_map_of, paged, remember_ic, run_jobs
from u8co_api.co_models_ic import IcLogin
from u8co_api.co_models_ic_reports import INNER, IcAggregateIn, inner_body
from u8co_api.errors import unavailable

ZERO = Decimal(0)
_CENT = Decimal("0.01")
_SAMPLE = 50
_STOCK_KEYS = ("qty", "qty_available")
_ARAP_KEYS = ("debit", "credit", "balance")
_AGING_KEYS = ("balance", "prepaid", "overdue")
GL_NET = ("open", "close")
GL_FLOW = ("period_debit", "period_credit", "ytd_debit", "ytd_credit")


def dec(value: object) -> Decimal:
    """桥给的数字（或数字字符串）转 Decimal；已是 Decimal 的原样返回；其它一律按 0。"""
    if isinstance(value, bool) or value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value)) if isinstance(value, (int, float, str)) else ZERO
    except InvalidOperation:
        return ZERO


def money(value: Decimal) -> float:
    return float(value.quantize(_CENT))


def split(net: Decimal) -> dict[str, Any]:
    """借减贷的净额拆成方向和借、贷两列（与报表的余额方向口径相同）。"""
    direction = "借" if net > 0 else ("贷" if net < 0 else "平")
    return {"dir": direction, "debit": money(max(net, ZERO)), "credit": money(max(-net, ZERO))}


def plain(node: Any) -> Any:
    """Decimal 转成 JSON 数字（两位小数以内的金额原样，数量不截断）。"""
    if isinstance(node, Decimal):
        return float(node)
    if isinstance(node, dict):
        return {key: plain(value) for key, value in node.items()}
    if isinstance(node, list):
        return [plain(item) for item in node]
    return node


def begin(request, caller, action: str, logins: list[IcLogin], ref: str) -> IcGroup:
    """审计（一行、全部账套），取对照，检查每个账套的授权（全有或全无），确认同一公司组。"""
    remember_ic(request, action, logins, ref)
    found = ic_map_of(request)
    accs = [login.acc for login in logins]
    allow_accs(request, accs, caller)
    return found.group_of(set(accs))


def by_account(results: dict[str, FanResult], ok_body: Callable[[dict], dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for acc, result in results.items():
        if result.ok:
            out[acc] = ok_body(result.body or {})
        else:
            out[acc] = {"ok": False, "status": result.status, "error": result.error}
    return out


def failures(results: dict[str, FanResult]) -> list[str]:
    """失败账套的提醒；全部失败时 503。"""
    lines = [
        f"账套 {acc} 读取失败（{(result.error or {}).get('code')}）：{(result.error or {}).get('message')}，未计入合计"
        for acc, result in results.items()
        if not result.ok
    ]
    if len(lines) == len(results):
        codes = "、".join(f"{acc}={(r.error or {}).get('code')}" for acc, r in results.items())
        raise unavailable(f"全部账套都读取失败：{codes}")
    return lines


def unmapped_entry(acc: str, kind: str, codes: list[str], **extra: Any) -> dict[str, Any]:
    distinct = sorted(set(codes))
    return {"acc": acc, "kind": kind, "count": len(distinct), "codes": distinct[:_SAMPLE], **extra}


def run_aggregate(request, caller, action: str, _bridge_path: str, body: IcAggregateIn) -> dict[str, Any]:
    group = begin(request, caller, action, body.logins, body.report)
    inner = INNER[body.report]
    jobs = {}
    for login in body.logins:
        request_body = inner_body(body.report, login, body.params)
        jobs[login.acc] = _pages(request, login, inner, request_body)
    results = run_jobs(request, jobs)
    warnings = failures(results)
    bodies = {acc: result.body or {} for acc, result in results.items() if result.ok}
    totals, unmapped, more = _TOTALS[body.report](group, bodies, body.params)
    return {
        "ok": True,
        "report": body.report,
        "group": group.id,
        "by_account": by_account(results, lambda found: {"ok": True, "items": found.get("items", [])}),
        "totals": plain(totals),
        "unmapped": unmapped,
        "warnings": warnings + more,
    }


def _pages(request, login: IcLogin, inner, request_body: dict) -> Callable[[], dict]:
    def job() -> dict:
        rows = paged(request, login, inner.path, request_body, page_size=inner.page_size, max_pages=inner.max_pages)
        return {"items": rows}

    return job


def _items(body: dict) -> list[dict]:
    return [item for item in body.get("items") or [] if isinstance(item, dict)]


def _add(target: dict, item: dict, keys: tuple[str, ...]) -> None:
    for key in keys:
        target[key] = target.get(key, ZERO) + dec(item.get(key))


def _stock_totals(group: IcGroup, bodies: dict[str, dict], _params: dict) -> tuple[list, list, list]:
    sums: dict[str, dict] = {}
    unmapped = []
    for acc, body in bodies.items():
        missing: list[str] = []
        for item in _items(body):
            code = item.get("inv_code")
            inv = group.inventory_of(acc, code) if isinstance(code, str) else None
            if inv is None:
                missing.append(str(code))
                continue
            row = sums.setdefault(inv.id, {"inventory": inv.id, "codes": dict(inv.codes), "by_account": {}})
            part = row["by_account"].setdefault(acc, {"inv_code": code})
            _add(row, item, _STOCK_KEYS)
            _add(part, item, _STOCK_KEYS)
        if missing:
            unmapped.append(unmapped_entry(acc, "inventory", missing))
    return list(sums.values()), unmapped, []


def _arap_totals(group: IcGroup, bodies: dict[str, dict], params: dict) -> tuple[list, list, list]:
    if params.get("group_by") == "person":
        return [], [], ["按业务员分组（group_by=person）时没有往来单位，不按公司合计"]
    side = params.get("side")
    owner = group.company_of_customer if side == "ar" else group.company_of_vendor
    sums: dict[str, dict] = {}
    unmapped = []
    for acc, body in bodies.items():
        missing: list[str] = []
        outside = ZERO
        for item in _items(body):
            partner = item.get("partner")
            company = owner(acc, partner) if isinstance(partner, str) else None
            if company is None:
                missing.append(str(partner))
                outside += dec(item.get("balance"))
                continue
            row = sums.setdefault(
                company, {"company": company, "name": group.name(company), "side": side, "by_account": {}}
            )
            part = row["by_account"].setdefault(acc, {"partner": partner})
            _arap_add(row, item)
            _arap_add(part, item)
        if missing:
            unmapped.append(unmapped_entry(acc, "partner", missing, balance=money(outside)))
    return list(sums.values()), unmapped, []


def _arap_add(target: dict, item: dict) -> None:
    if "aging" not in item:
        _add(target, item, _ARAP_KEYS)
        return
    _add(target, item, _AGING_KEYS)
    found = item.get("aging")
    if isinstance(found, list):
        have = target.get("aging") or [ZERO] * len(found)
        if len(have) == len(found):
            target["aging"] = [left + dec(right) for left, right in zip(have, found)]


def _gl_totals(group: IcGroup, bodies: dict[str, dict], _params: dict) -> tuple[list, list, list]:
    sums: dict[str, dict] = {}
    unmapped = []
    for acc, body in bodies.items():
        missing: list[str] = []
        for item in _items(body):
            code = item.get("code")
            logical = group.gl_logical(acc, code) if isinstance(code, str) else None
            if logical is None:
                if item.get("leaf") is True:
                    missing.append(str(code))
                continue
            row = sums.setdefault(logical, _gl_row(group, logical))
            row["by_account"][acc] = {"code": code, "name": item.get("name"), "close_net": gl_net(item, "close")}
            _gl_add(row, item)
        if missing:
            unmapped.append(unmapped_entry(acc, "subject", missing))
    return [_gl_finish(row) for row in sums.values()], unmapped, []


def _gl_row(group: IcGroup, logical: str) -> dict:
    found = group.gl_of(logical)
    return {"logical": logical, "codes": dict(found.codes) if found else {}, "by_account": {}}


def gl_net(item: dict, prefix: str) -> Decimal:
    return dec(item.get(f"{prefix}_debit")) - dec(item.get(f"{prefix}_credit"))


def _gl_add(row: dict, item: dict) -> None:
    for prefix in GL_NET:
        row[f"_{prefix}"] = row.get(f"_{prefix}", ZERO) + gl_net(item, prefix)
    _add(row, item, GL_FLOW)


def _gl_finish(row: dict) -> dict:
    out = {key: value for key, value in row.items() if not key.startswith("_")}
    for prefix in GL_NET:
        parts = split(row.pop(f"_{prefix}", ZERO))
        out[f"{prefix}_dir"] = parts["dir"]
        out[f"{prefix}_debit"] = parts["debit"]
        out[f"{prefix}_credit"] = parts["credit"]
    return out


_TOTALS = {
    "stock_current": _stock_totals,
    "arap_balance": _arap_totals,
    "arap_aging": _arap_totals,
    "gl_balance": _gl_totals,
}
