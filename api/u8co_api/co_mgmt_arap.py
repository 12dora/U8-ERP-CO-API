"""经营管理应收应付与账期 /v1/co/mgmt/arap：各账套调桥 reports/mgmt/arap_terms（应收、应付各一次）。

周转天数 DSO / DPO = 期末余额 ×天数 ÷ 近 12 个月单据金额（天数取桥给的 history_from 到 history_to）。
合并时往来单位是本次所选其他公司（as_customer / as_vendor）的余额、账龄和单据金额从合计里去掉，周转天数按抵销后重算；
各账套的往来单位编码不跨账套合并。金额一律用 Decimal 计算，出参才转成数字。

很多账套收款（付款）不核销，桥给的账龄、逾期是毛额（未核销的预收付单列在 prepaid）。这里按单位把 prepaid
按先进先出冲抵最老的账龄段（aging_net、overdue_net，分段与 aging 相同），原 aging、overdue 保留；
合计的净额是各单位（含 others，others 整体冲抵）净额之和，不跨单位冲抵。余额 ≤ 0 时不算周转天数。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from u8co_api.co_ic_aggregate import plain
from u8co_api.co_ic_core import FanResult, IcGroup
from u8co_api.co_mgmt_core import (
    MGMT_BRIDGE,
    ZERO,
    MgmtRun,
    account_error,
    dec,
    ratio,
    report_failures,
    run_mgmt,
    serve,
)
from u8co_api.co_mgmt_sales import ic_partners
from u8co_api.co_models_mgmt_reports import MgmtArapIn

ARAP_PATH = MGMT_BRIDGE + "arap_terms"
AMOUNTS = ("balance", "prepaid", "overdue", "open_notes", "invoice_amount", "paid_amount", "unpaid_amount")
DAYS_KEY = {"ar": "dso_days", "ap": "dpo_days"}
SIDE_NAME = {"ar": "应收", "ap": "应付"}
_YEAR_DAYS = 365
_ONE = Decimal(1)
NOTE_DAYS = "合并后的回款（付款）天数不重算（各账套只给单位级的平均数），见各账套"
NOTE_NEGATIVE = "余额为负（预收/预付大于欠款），不计算周转天数"
PAY_NAME = {"ar": "收款", "ap": "付款"}
_WARN_SHARE = Decimal("0.1")


def run_arap(request, caller, action: str, _bridge_path: str, body: MgmtArapIn) -> dict[str, Any]:
    return serve(request, caller, action, body, lambda run: compute_arap(run, body))


def arap_params(body: MgmtArapIn, side: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "side": side,
        "as_of": body.effective_as_of(),
        "buckets": list(body.buckets),
        "top": body.top,
    }
    if body.default_credit_days is not None:
        params["default_credit_days"] = body.default_credit_days
    return params


def fetch_sides(run: MgmtRun, body: MgmtArapIn) -> dict[str, dict[str, FanResult]]:
    """每个方向（ar / ap）各调一次各账套的桥。"""
    found: dict[str, dict[str, FanResult]] = {}
    for side in body.sides():
        params = arap_params(body, side)
        found[side] = run_mgmt(run.request, body, ARAP_PATH, lambda _login, p=params: dict(p), run.ok_accs)
    return found


def compute_arap(run: MgmtRun, body: MgmtArapIn) -> dict[str, Any]:
    sides = fetch_sides(run, body)
    warnings: list[str] = []
    for side, results in sides.items():
        warnings += [f"（{SIDE_NAME[side]}）{line}" for line in report_failures(results)]
    by_account: dict[str, dict] = {}
    bodies: dict[str, dict[str, dict]] = {}
    notes: list[str] = []
    for acc in run.accs:
        view, failed = _account(acc, sides)
        if view is None:
            if failed is not None:
                by_account[acc] = account_error(failed)
            continue
        view = {side: net_side(found) for side, found in view.items()}
        by_account[acc] = {"ok": True, **{side: plain(_side_view(found)) for side, found in view.items()}}
        bodies[acc] = view
        warnings += _account_warnings(acc, view)
        notes += [NOTE_NEGATIVE for found in view.values() if _balance(found) < ZERO]
    called = {acc for results in sides.values() for acc in results}
    out: dict[str, Any] = {"by_account": by_account, "warnings": warnings, "complete": len(bodies) == len(called)}
    if run.consolidating and out["complete"] and run.group is not None:
        merged = consolidate_arap(run.group, body, bodies)
        out.update(merged)
        out["warnings"] = warnings + merged["warnings"]
        notes += merged["notes"]
    out["notes"] = list(dict.fromkeys(notes))
    return out


def _account_warnings(acc: str, view: dict[str, dict]) -> list[str]:
    warnings: list[str] = []
    for side, found in view.items():
        if found.get("enabled") is False:
            warnings.append(f"账套 {acc} 没有启用{SIDE_NAME[side]}款管理，{SIDE_NAME[side]}为空")
        totals = _totals(found)
        prepaid = dec(totals.get("prepaid"))
        aging = sum((dec(value) for value in totals.get("aging") or []), ZERO)
        if prepaid > ZERO and aging > ZERO and prepaid > aging * _WARN_SHARE:
            warnings.append(
                f"账套 {acc} 有 {prepaid:,.2f} 元{PAY_NAME.get(side, '收款')}未核销，"
                "已按先进先出冲抵账龄；逾期以净额为准"
            )
    return warnings


def net_aging(aging: list, prepaid: Decimal) -> list[Decimal]:
    """未核销的预收付按先进先出冲抵：从最老的一段往前冲，直到冲完；负数段不动。"""
    left = max(prepaid, ZERO)
    net = [dec(value) for value in aging]
    for index in range(len(net) - 1, -1, -1):
        if left <= ZERO:
            break
        used = min(max(net[index], ZERO), left)
        net[index] -= used
        left -= used
    return net


def _net_row(row: dict) -> dict:
    """单位（或 others）加 aging_net、overdue_net（第一段是未到期，其余各段之和为逾期）。"""
    aging = row.get("aging")
    if not isinstance(aging, list):
        return dict(row)
    net = net_aging(aging, dec(row.get("prepaid")))
    return {**row, "aging_net": net, "overdue_net": sum(net[1:], ZERO)}


def _sum_lists(lists: list[list[Decimal]]) -> list[Decimal]:
    return [sum(column, ZERO) for column in zip(*lists)]


def net_side(found: dict) -> dict:
    """一个账套一个方向的桥结果加上净额账龄：单位、others 各自冲抵，合计取其和。"""
    out = dict(found)
    partners = [_net_row(row) for row in partners_of(found)]
    out["partners"] = partners
    rows = list(partners)
    if isinstance(found.get("others"), dict):
        out["others"] = _net_row(found["others"])
        rows.append(out["others"])
    totals = _totals(found)
    size = len(totals.get("aging") or [])
    nets = [row["aging_net"] for row in rows if "aging_net" in row]
    if nets and len(nets) == len(rows) and all(len(net) == size for net in nets):
        aging_net = _sum_lists(nets)
        totals = {**totals, "aging_net": aging_net, "overdue_net": sum(aging_net[1:], ZERO)}
    else:
        totals = _net_row(totals)
    out["totals"] = totals
    return out


def _totals(found: dict) -> dict:
    return found.get("totals") if isinstance(found.get("totals"), dict) else {}


def _balance(found: dict) -> Decimal:
    return dec(_totals(found).get("balance"))


def _account(acc: str, sides: dict[str, dict[str, FanResult]]) -> tuple[dict[str, dict] | None, FanResult | None]:
    view: dict[str, dict] = {}
    for side, results in sides.items():
        result = results.get(acc)
        if result is None:
            return None, None
        if not result.ok:
            return None, result
        view[side] = result.body or {}
    return view, None


def history_days(found: dict) -> int:
    """近 12 个月窗口的天数（含两端）；桥没给或不合法时按 365 天。"""
    try:
        start = dt.date.fromisoformat(str(found.get("history_from")))
        end = dt.date.fromisoformat(str(found.get("history_to")))
    except ValueError:
        return _YEAR_DAYS
    days = (end - start).days + 1
    return days if days > 0 else _YEAR_DAYS


def turnover_days(balance: Decimal, amount: Decimal, days: int) -> float | None:
    """余额 ×天数 ÷ 单据金额（两位小数）；单据金额为 0 或余额 ≤ 0（预收付大于欠款）时 None。"""
    if balance <= ZERO:
        return None
    return ratio(balance * days, amount, _ONE)


def _side_view(found: dict) -> dict[str, Any]:
    side = str(found.get("side") or "ar")
    out = {key: value for key, value in found.items() if key != "ok"}
    totals = _totals(found)
    out[DAYS_KEY.get(side, "dso_days")] = turnover_days(
        dec(totals.get("balance")), dec(totals.get("invoice_amount")), history_days(found)
    )
    return out


def partners_of(found: dict) -> list[dict]:
    return [row for row in found.get("partners") or [] if isinstance(row, dict)]


def _add(target: dict, item: dict, sign: int = 1) -> None:
    for key in (*AMOUNTS, "overdue_net"):
        target[key] = target.get(key, ZERO) + dec(item.get(key)) * sign
    for key in ("aging", "aging_net"):
        values = item.get(key)
        if isinstance(values, list):
            have = target.get(key) or [ZERO] * len(values)
            if len(have) == len(values):
                target[key] = [left + dec(right) * sign for left, right in zip(have, values)]


def consolidate_arap(group: IcGroup, body: MgmtArapIn, bodies: dict[str, dict[str, dict]]) -> dict[str, Any]:
    consolidated: dict[str, Any] = {}
    eliminations: list[dict] = []
    warnings: list[str] = []
    for side in body.sides():
        merged, elims, more = _side_merge(group, side, body.top, {acc: view[side] for acc, view in bodies.items()})
        consolidated[side] = merged
        eliminations += elims
        warnings += more
    negative = any(_balance(found) < ZERO for found in consolidated.values())
    return {
        "consolidated": plain(consolidated),
        "eliminations": plain(eliminations),
        "notes": [NOTE_DAYS] + ([NOTE_NEGATIVE] if negative else []),
        "warnings": warnings,
    }


def _side_merge(group: IcGroup, side: str, top: int, found: dict[str, dict]) -> tuple[dict, list[dict], list[str]]:
    lookup = group.customer_code if side == "ar" else group.vendor_code
    accs = list(found)
    totals: dict[str, Any] = {}
    rows: list[dict] = []
    elims: list[dict] = []
    warnings: list[str] = []
    days = _YEAR_DAYS
    for acc, data in found.items():
        days = history_days(data)
        _add(totals, _totals(data))
        partners = ic_partners(group, acc, accs, lookup)
        seen: set[str] = set()
        for row in partners_of(data):
            code = row.get("code")
            if code in partners:
                seen.add(code)
                _add(totals, row, -1)
                elims.append(_elim(side, acc, partners[code], row))
            else:
                rows.append({"acc": acc, **row})
        if isinstance(data.get("others"), dict) and dec(data["others"].get("balance")) != ZERO:
            warnings += [
                f"账套 {acc} 的内部往来单位 {code}（代表账套 {company}）不在列出的单位里，可能并在 others 中，没有抵销"
                for code, company in partners.items()
                if code not in seen
            ]
    rows.sort(key=lambda row: -abs(dec(row.get("balance"))))
    totals[DAYS_KEY[side]] = turnover_days(totals.get("balance", ZERO), totals.get("invoice_amount", ZERO), days)
    return {"totals": totals, "partners": rows[:top]}, elims, warnings


def _elim(side: str, acc: str, company: str, row: dict) -> dict[str, Any]:
    out: dict[str, Any] = {"rule": "ic_arap", "side": side, "acc": acc, "company": company, "partner": row.get("code")}
    out.update({key: dec(row.get(key)) for key in ("balance", "overdue", "overdue_net", "invoice_amount")})
    return out
