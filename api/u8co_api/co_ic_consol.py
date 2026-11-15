"""合并报表 /v1/co/reports/consolidation：各账套科目余额按对照映射成逻辑科目，按抵销对（ar_ap）抵销内部往来。

每个账套一个任务：科目余额表（已记账、非零，全部级次）加上该账套在抵销对里的辅助核算余额（应收按客户、应付按供应商）。
抵销额 = 应收、应付期末余额绝对值中较小的一个，方向相反时不抵销。未实现内部利润、收入成本不抵销。
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from u8co_api.co_ic_aggregate import ZERO, begin, by_account, dec, failures, gl_net, money, plain, split, unmapped_entry
from u8co_api.co_ic_core import IcGroup, paged, run_jobs
from u8co_api.co_ic_map import IcElimPair, IcSide
from u8co_api.co_models_ic import IcLogin
from u8co_api.co_models_ic_reports import IcConsolidationIn

GL_PATH = "/v1/reports/gl_balance"
AUX_PATH = "/v1/reports/gl_aux_balance"
_GL_PAGE, _GL_PAGES = 1000, 20
_AUX_PAGE, _AUX_PAGES = 1000, 5
NOTES = (
    "只抵销对照 elim 里配置的内部往来（ar_ap），按期末余额。",
    "未抵销未实现内部利润（期末存货中的内部销售毛利）。",
    "未抵销内部销售收入与采购成本。",
    "对照之外的科目只在各账套的试算里，不相加。",
)


def aux_key(dim: str, code: str, partner: str) -> str:
    return f"{dim}:{code}:{partner}"


def _side_need(group: IcGroup, side: IcSide, dim: str) -> tuple[str, str, str]:
    found = group.gl_of(side.logical)
    code = found.codes[side.acc] if found else ""
    return dim, code, side.partner


def pairs_of(group: IcGroup, accs: set[str]) -> list[tuple[str, IcElimPair]]:
    """两边账套都在本次请求里的抵销对。"""
    return [
        (elim.rule, pair) for elim in group.elim for pair in elim.pairs if pair.ar.acc in accs and pair.ap.acc in accs
    ]


def needs_of(group: IcGroup, pairs: list[tuple[str, IcElimPair]], acc: str) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for _rule, pair in pairs:
        if pair.ar.acc == acc:
            found.append(_side_need(group, pair.ar, "customer"))
        if pair.ap.acc == acc:
            found.append(_side_need(group, pair.ap, "vendor"))
    return list(dict.fromkeys(found))


def run_consolidation(request, caller, action: str, _bridge_path: str, body: IcConsolidationIn) -> dict[str, Any]:
    group = begin(request, caller, action, body.logins, body.audit_ref())
    accs = {login.acc for login in body.logins}
    pairs = pairs_of(group, accs)
    period = {"fiscal_year": body.fiscal_year, "period_from": body.period_from, "period_to": body.period_to}
    jobs = {login.acc: _job(request, login, period, needs_of(group, pairs, login.acc)) for login in body.logins}
    results = run_jobs(request, jobs)
    warnings = failures(results)
    bodies = {acc: result.body or {} for acc, result in results.items() if result.ok}
    trial, unmapped = _trials(group, bodies)
    elims, more = _eliminations(group, pairs, bodies)
    complete = len(bodies) == len(results)
    # 有账套失败时不给合并数（同 mgmt 的合并口径），各账套结果和抵销明细照常返回。
    consolidated = plain(_consolidated(group, bodies, elims)) if complete else None
    return {
        "ok": True,
        "group": group.id,
        **period,
        "complete": complete,
        "by_account": by_account(results, lambda _found: {"ok": True}),
        "trial": plain(trial),
        "eliminations": plain(elims),
        "consolidated": consolidated,
        "unmapped": unmapped,
        "warnings": warnings + more,
        "notes": list(NOTES),
    }


def _job(request, login: IcLogin, period: dict, needs: list[tuple[str, str, str]]) -> Callable[[], dict]:
    def job() -> dict:
        gl_body = dict(period, nonzero=True)
        items = paged(request, login, GL_PATH, gl_body, page_size=_GL_PAGE, max_pages=_GL_PAGES)
        aux: dict[str, dict[str, Decimal]] = {}
        for dim, code, partner in needs:
            aux_body = dict(period, dim=dim, code_prefix=code, dim_code=partner)
            rows = paged(request, login, AUX_PATH, aux_body, page_size=_AUX_PAGE, max_pages=_AUX_PAGES)
            aux[aux_key(dim, code, partner)] = _aux_sum(rows, partner)
        return {"items": items, "aux": aux}

    return job


def _aux_sum(rows: list[dict], partner: str) -> dict[str, Decimal]:
    debit = credit = ZERO
    for row in rows:
        if row.get("dim_code") == partner:
            debit += dec(row.get("close_debit"))
            credit += dec(row.get("close_credit"))
    return {"debit": debit, "credit": credit}


def _trials(group: IcGroup, bodies: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    trial: list[dict] = []
    unmapped: list[dict] = []
    for acc, body in bodies.items():
        items = [item for item in body.get("items") or [] if isinstance(item, dict)]
        leaves = [item for item in items if item.get("leaf") is True]
        debit = sum((dec(item.get("close_debit")) for item in leaves), ZERO)
        credit = sum((dec(item.get("close_credit")) for item in leaves), ZERO)
        trial.append(
            {
                "acc": acc,
                "name": group.name(acc),
                "balanced": debit == credit,
                "close_debit": money(debit),
                "close_credit": money(credit),
                "items": _mapped(group, acc, items),
            }
        )
        mapped = [found.codes[acc] for found in group.gl if acc in found.codes]
        missing = [str(item.get("code")) for item in leaves if not _covered(item.get("code"), mapped)]
        if missing:
            unmapped.append(unmapped_entry(acc, "subject", missing))
    return trial, unmapped


def _covered(code: object, mapped: list[str]) -> bool:
    return isinstance(code, str) and any(code.startswith(prefix) for prefix in mapped)


def _mapped(group: IcGroup, acc: str, items: list[dict]) -> list[dict]:
    by_code = {item.get("code"): item for item in items}
    rows = []
    for found in group.gl:
        code = found.codes.get(acc)
        if code is None:
            continue
        item = by_code.get(code) or {}
        rows.append(
            {
                "logical": found.logical,
                "code": code,
                "name": item.get("name"),
                "period_debit": dec(item.get("period_debit")),
                "period_credit": dec(item.get("period_credit")),
                "close_net": gl_net(item, "close"),
            }
        )
    return rows


def _eliminations(group: IcGroup, pairs: list[tuple[str, IcElimPair]], bodies: dict[str, dict]) -> tuple[list, list]:
    elims: list[dict] = []
    warnings: list[str] = []
    for rule, pair in pairs:
        if pair.ar.acc not in bodies or pair.ap.acc not in bodies:
            warnings.append(f"抵销对 {pair.ar.acc}→{pair.ap.acc} 有账套读取失败，未抵销")
            continue
        ar = _side(group, pair.ar, "customer", bodies, receivable=True)
        ap = _side(group, pair.ap, "vendor", bodies, receivable=False)
        elim = {"rule": rule, "ar": ar, "ap": ap, "difference": ar["balance"] - ap["balance"]}
        amount = _amount(ar["balance"], ap["balance"])
        if amount is None:
            elim["amount"] = ZERO
            elim["entries"] = []
            elim["skipped"] = "opposite_sign"
            warnings.append(f"抵销对 {pair.ar.acc}→{pair.ap.acc} 应收、应付方向相反，未抵销")
        else:
            elim["amount"] = amount
            elim["entries"] = _entries(pair, amount)
        elims.append(elim)
    return elims, warnings


def _entries(pair: IcElimPair, amount: Decimal) -> list[dict[str, Any]]:
    """借应付、贷应收；两边都是反向余额（amount 为负）时反过来。"""
    size = abs(amount)
    debit, credit = (pair.ap.logical, pair.ar.logical) if amount >= 0 else (pair.ar.logical, pair.ap.logical)
    return [
        {"logical": debit, "debit": size, "credit": ZERO},
        {"logical": credit, "debit": ZERO, "credit": size},
    ]


def _side(group: IcGroup, side: IcSide, dim: str, bodies: dict[str, dict], *, receivable: bool) -> dict[str, Any]:
    _dim, code, _partner = _side_need(group, side, dim)
    found = (bodies[side.acc].get("aux") or {}).get(aux_key(dim, code, side.partner)) or {}
    net = dec(found.get("debit")) - dec(found.get("credit"))
    balance = net if receivable else -net
    return {"acc": side.acc, "logical": side.logical, "code": code, "partner": side.partner, "balance": balance}


def _amount(ar: Decimal, ap: Decimal) -> Decimal | None:
    """抵销额：同号时取绝对值较小者（带符号，负数表示反向抵销），一边为 0 时为 0，异号为 None。"""
    if ar == ZERO or ap == ZERO:
        return ZERO
    if (ar > 0) != (ap > 0):
        return None
    smaller = min(abs(ar), abs(ap))
    return smaller if ar > 0 else -smaller


def _consolidated(group: IcGroup, bodies: dict[str, dict], elims: list[dict]) -> list[dict]:
    adjust: dict[str, Decimal] = {}
    for elim in elims:
        for entry in elim["entries"]:
            adjust[entry["logical"]] = adjust.get(entry["logical"], ZERO) + entry["debit"] - entry["credit"]
    rows = []
    for found in group.gl:
        row: dict[str, Any] = {"logical": found.logical, "codes": dict(found.codes), "by_account": {}}
        net = flow_debit = flow_credit = ZERO
        for acc, body in bodies.items():
            code = found.codes.get(acc)
            if code is None:
                continue
            item = _find(body, code)
            row["by_account"][acc] = gl_net(item, "close")
            net += gl_net(item, "close")
            flow_debit += dec(item.get("period_debit"))
            flow_credit += dec(item.get("period_credit"))
        change = adjust.get(found.logical, ZERO)
        parts = split(net + change)
        row.update(
            period_debit=flow_debit,
            period_credit=flow_credit,
            close_net=net,
            elim_debit=max(change, ZERO),
            elim_credit=max(-change, ZERO),
            after_net=net + change,
            close_dir=parts["dir"],
            close_debit=parts["debit"],
            close_credit=parts["credit"],
        )
        rows.append(row)
    return rows


def _find(body: dict, code: str) -> dict:
    for item in body.get("items") or []:
        if isinstance(item, dict) and item.get("code") == code:
            return item
    return {}
