"""经营管理销售与毛利 /v1/co/mgmt/sales：各账套调桥 reports/mgmt/sales，合并时去掉公司间内部销售。

内部销售：客户编码在公司间对照 as_customer 里代表本次所选的另一个公司的行。合计 = 各账套合计 − 内部销售；
按存货分组时只按对照的存货 id 相加；不按存货分组时各账套去掉内部客户的行原样并列（带 acc）。
金额一律用 Decimal 计算，出参才转成数字。
账套里被字段权限置空的度量（如 cogs）保持 null，含它的合计、毛利、毛利率也为 null，字段名记入 consolidated.masked_fields。
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import Any

from u8co_api.co_ic_aggregate import plain, unmapped_entry
from u8co_api.co_ic_core import FanResult, IcGroup
from u8co_api.co_mgmt_core import (
    MASKED,
    MGMT_BRIDGE,
    ZERO,
    MgmtRun,
    account_error,
    dec,
    madd,
    masked_union,
    mdec,
    ratio,
    report_failures,
    run_mgmt,
    serve,
)
from u8co_api.co_models_mgmt_reports import MgmtSalesIn

SALES_PATH = MGMT_BRIDGE + "sales"
MEASURES = ("qty", "revenue", "revenue_tax_incl", "cost_qty", "cogs")
# 只为找出内部客户而补读时每个账套取的组数（桥的上限）。
_IC_TOP = 500
NOTE_ITEM_IC = "按存货合并的行没有按客户拆分，含公司间内部销售；内部销售只从合计里去掉"
NOTE_CODES = "客户、业务员、部门编码各账套自成体系，合并时不跨账套相加"

Sums = dict[str, Decimal | None]


def run_sales(request, caller, action: str, _bridge_path: str, body: MgmtSalesIn) -> dict[str, Any]:
    return serve(request, caller, action, body, lambda run: compute_sales(run, body))


def sales_params(body: MgmtSalesIn, group_by: list[str] | None = None, top: int | None = None) -> dict[str, Any]:
    return {
        "fiscal_year": body.fiscal_year,
        "period_from": body.period_from,
        "period_to": body.period_to,
        "group_by": list(body.group_by if group_by is None else group_by),
        "top": body.top if top is None else top,
        "include_unverified": body.include_unverified,
    }


def compute_sales(run: MgmtRun, body: MgmtSalesIn) -> dict[str, Any]:
    params = sales_params(body)
    results = run_mgmt(run.request, body, SALES_PATH, lambda _login: dict(params), run.ok_accs)
    warnings = report_failures(results)
    by_account: dict[str, dict] = {}
    bodies: dict[str, dict] = {}
    for acc, result in results.items():
        if not result.ok:
            by_account[acc] = account_error(result)
            continue
        found = result.body or {}
        bodies[acc] = found
        by_account[acc] = {"ok": True, **{key: value for key, value in found.items() if key != "ok"}}
        if found.get("sa_enabled") is False:
            warnings.append(f"账套 {acc} 没有启用销售管理，销售统计为空")
    out: dict[str, Any] = {"by_account": by_account, "warnings": warnings, "complete": len(bodies) == len(results)}
    if run.consolidating and out["complete"] and run.group is not None:
        merged = consolidate_sales(run, body, bodies)
        out.update(merged)
        out["warnings"] = warnings + merged["warnings"]
    return out


def rows_of(body: dict) -> list[dict]:
    raw = body.get("rows")
    if raw is None:
        raw = body.get("items")
    return [row for row in raw or [] if isinstance(row, dict)]


def customer_of(row: dict) -> str | None:
    value = row.get("customer_code", row.get("customer"))
    if isinstance(value, dict):
        value = value.get("code")
    return value if isinstance(value, str) else None


def sums_of(item: dict | None) -> Sums:
    # 被遮蔽的度量是 None（mdec），不当 0。
    return {key: mdec(item or {}, key) for key in MEASURES}


def add_sums(into: Sums, other: Sums, sign: int = 1) -> None:
    for key in MEASURES:
        into[key] = madd(into.get(key, ZERO), other.get(key, ZERO), sign)


def measures(sums: Sums) -> dict[str, Any]:
    """度量加毛利、毛利率（%，两位小数；收入为 0 时 null）。收入或成本被遮蔽时毛利、毛利率为 null。"""
    revenue, cogs = sums.get("revenue", ZERO), sums.get("cogs", ZERO)
    out: dict[str, Any] = {key: sums.get(key, ZERO) for key in MEASURES}
    gross = None if revenue is None or cogs is None else revenue - cogs
    out["gross"] = gross
    out["gross_pct"] = None if gross is None or revenue is None else ratio(gross, revenue)
    return out


def _masked_names(totals: dict[str, Any]) -> list[str]:
    return [key for key in (*MEASURES, "gross") if totals.get(key) is None] + (
        ["gross_pct"] if totals.get("gross") is None else []
    )


def ic_partners(group: IcGroup, acc: str, accs: list[str], lookup: Callable[[str, str], str | None]) -> dict[str, str]:
    """账套 acc 里代表本次所选其他公司的往来单位：编码 → 公司账套。lookup 是 group.customer_code 或 vendor_code。"""
    found: dict[str, str] = {}
    for other in accs:
        if other == acc:
            continue
        code = lookup(acc, other)
        if code is not None:
            found[code] = other
    return found


def consolidate_sales(run: MgmtRun, body: MgmtSalesIn, bodies: dict[str, dict]) -> dict[str, Any]:
    group: IcGroup = run.group  # type: ignore[assignment]
    accs = list(bodies)
    ic_rows, problem = _ic_rows(run, body, bodies)
    if ic_rows is None:
        # 抵销取数失败：不出合并数，结果不完整（不缓存）。
        return {"consolidated": None, "eliminations": [], "notes": [], "warnings": [problem], "complete": False}
    totals: Sums = {}
    eliminations: list[dict] = []
    missing: list[str] = []
    for acc, found in bodies.items():
        add_sums(totals, sums_of(found.get("totals")))
        partners = ic_partners(group, acc, accs, group.customer_code)
        rows, others = ic_rows.get(acc, ([], None))
        elims, lost = _eliminate(acc, partners, rows, others)
        for item in elims:
            add_sums(totals, item["_sums"], -1)
            eliminations.append(_public(item))
        missing += lost
    if missing:
        # 内部客户可能并在 others 里，内部销售去不干净：不出合并数，结果不完整（不缓存）。
        return {"consolidated": None, "eliminations": [], "notes": [], "warnings": missing, "complete": False}
    notes = [NOTE_CODES]
    if "inventory" in body.group_by:
        rows, unmapped = _item_rows(group, body, bodies)
        if "customer" not in body.group_by:
            notes.append(NOTE_ITEM_IC)
    else:
        rows, unmapped = _plain_rows(group, body, bodies), []
    merged = measures(totals)
    consolidated = {"totals": plain(merged), "rows": plain(rows), "unmapped": unmapped}
    masked = masked_union(bodies.values(), *_masked_names(merged))
    if masked:
        consolidated[MASKED] = masked
    return {"consolidated": consolidated, "eliminations": eliminations, "notes": notes, "warnings": []}


IcRows = dict[str, tuple[list[dict], object]]


def _ic_rows(run: MgmtRun, body: MgmtSalesIn, bodies: dict[str, dict]) -> tuple[IcRows | None, str]:
    """各账套按客户的行和 others（用来找内部销售）。

    主查询只按客户分组且没有并入 others 的组时直接用；否则按客户补读一次（不分期间，取桥上限 500 组），
    避免内部客户被挤进 others 或被其他分组维度拆散后漏抵销。
    """
    if list(body.group_by) == ["customer"] and not any(_has_others(found.get("others")) for found in bodies.values()):
        return {acc: (rows_of(found), found.get("others")) for acc, found in bodies.items()}, ""
    params = sales_params(body, ["customer"], _IC_TOP)
    extra: dict[str, FanResult] = run_mgmt(run.request, body, SALES_PATH, lambda _login: dict(params), bodies)
    out: IcRows = {}
    for acc, result in extra.items():
        if not result.ok:
            message = (result.error or {}).get("message")
            return None, f"账套 {acc} 按客户的销售统计读取失败（{message}），合并数没有出具"
        out[acc] = (rows_of(result.body or {}), (result.body or {}).get("others"))
    return out, ""


def _eliminate(acc: str, partners: dict[str, str], rows: list[dict], others: object) -> tuple[list[dict], list[str]]:
    sums: dict[str, Sums] = {}
    for row in rows:
        code = customer_of(row)
        if code in partners:
            add_sums(sums.setdefault(code, {}), sums_of(row))
    elims = [
        {"rule": "ic_sales", "seller": acc, "buyer": partners[code], "customer": code, "_sums": found}
        for code, found in sums.items()
    ]
    hidden = _has_others(others)
    missing = [
        f"账套 {acc} 的内部客户 {code}（代表账套 {company}）不在列出的组里，可能并在 others 中，合并数没有出具"
        for code, company in partners.items()
        if code not in sums and hidden
    ]
    return elims, missing


def _has_others(others: object) -> bool:
    return isinstance(others, dict) and any(sums_of(others).values())


def _public(item: dict) -> dict[str, Any]:
    out = {key: value for key, value in item.items() if not key.startswith("_")}
    out.update(plain(measures(item["_sums"])))
    return out


def _is_ic(group: IcGroup, acc: str, row: dict, accs: list[str]) -> bool:
    code = customer_of(row)
    if code is None:
        return False
    company = group.company_of_customer(acc, code)
    return company is not None and company != acc and company in accs


def _item_rows(group: IcGroup, body: MgmtSalesIn, bodies: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """按对照的存货 id（和期间）相加；按客户分组时先去掉内部客户的行。"""
    accs = list(bodies)
    merged: dict[tuple, dict] = {}
    unmapped: list[dict] = []
    for acc, found in bodies.items():
        missing: list[str] = []
        outside = ZERO
        for row in rows_of(found):
            if _is_ic(group, acc, row, accs):
                continue
            code = row.get("inventory_code")
            inv = group.inventory_of(acc, code) if isinstance(code, str) else None
            if inv is None:
                missing.append(str(code))
                outside += dec(row.get("revenue"))
                continue
            key = (row.get("period") if "period" in body.group_by else None, inv.id)
            slot = merged.setdefault(
                key, {"inventory": inv.id, "codes": dict(inv.codes), "_sums": {}, "by_account": {}}
            )
            if key[0] is not None:
                slot["period"] = key[0]
            add_sums(slot["_sums"], sums_of(row))
            part = slot["by_account"].setdefault(acc, {"inventory_code": code, "_sums": {}})
            add_sums(part["_sums"], sums_of(row))
        if missing:
            unmapped.append(unmapped_entry(acc, "inventory", missing, revenue=float(outside)))
    rows = [_finish(slot) for slot in merged.values()]
    rows.sort(key=lambda row: (-(row["revenue"] or ZERO), row.get("period") or 0, row["inventory"]))
    return rows[: body.top], unmapped


def _finish(slot: dict) -> dict:
    out = {key: value for key, value in slot.items() if not key.startswith("_") and key != "by_account"}
    out.update(measures(slot["_sums"]))
    out["by_account"] = {
        acc: {"inventory_code": part["inventory_code"], **measures(part["_sums"])}
        for acc, part in slot["by_account"].items()
    }
    return out


def _plain_rows(group: IcGroup, body: MgmtSalesIn, bodies: dict[str, dict]) -> list[dict]:
    accs = list(bodies)
    rows = [
        {"acc": acc, **row}
        for acc, found in bodies.items()
        for row in rows_of(found)
        if not _is_ic(group, acc, row, accs)
    ]
    rows.sort(key=lambda row: -dec(row.get("revenue")))
    return rows[: body.top]
