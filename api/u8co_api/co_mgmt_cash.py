"""经营管理资金与存货 /v1/co/mgmt/cash_stock：各账套调桥 reports/mgmt/cash_stock（按 period_to 一个会计期间）。

合并：货币资金、应收票据、存货金额按账套相加（存货金额含未实现内部利润，不抵销）；产量只按公司间对照的存货 id 相加；
采购去掉供应商是本次所选其他公司（as_vendor）的部分。金额一律用 Decimal 计算，出参才转成数字。
存货、采购金额被字段权限置空（null）的账套，相应合计保持 null，字段名记入 consolidated.masked_fields。

sections 只出请求的段：桥只算这几段，合并也只合这几段。只要 production 时不读 mgmt/meta（MgmtCashStockIn.meta_mode），桥按产成品入库单
查询权限放行，没有总账权限的车间、仓库操作员也能用。
"""

from __future__ import annotations

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
    rejected_field,
    report_failures,
    run_mgmt,
    serve,
)
from u8co_api.co_mgmt_sales import ic_partners
from u8co_api.co_models_mgmt_reports import MgmtCashStockIn

CASH_PATH = MGMT_BRIDGE + "cash_stock"
NOTE_UNREALIZED = "未抵销存货中未实现的内部利润（合并存货金额偏高）"
NOTE_ONE_PERIOD = "资金与存货按第 {period} 期（period_to）出具，产量、采购只含该期间"
NOTE_QTY = "存货结存数量是各账套全部存货的数量合计（单位不同也相加），只作参考"


def run_cash_stock(request, caller, action: str, _bridge_path: str, body: MgmtCashStockIn) -> dict[str, Any]:
    return serve(request, caller, action, body, lambda run: compute_cash_stock(run, body))


def cash_params(body: MgmtCashStockIn) -> dict[str, Any]:
    params: dict[str, Any] = {
        "fiscal_year": body.fiscal_year,
        "period": body.period_to,
        "top": body.top,
        "purchase_source": body.purchase_source,
        "include_unverified": body.include_unverified,
    }
    if body.class_grade is not None:
        params["class_grade"] = body.class_grade
    if body.inv_class is not None:
        params["inv_class"] = body.inv_class
    if body.sections is not None:
        params["sections"] = list(body.sections)
    return params


SECTION_KEY = {
    "cash": "cash",
    "notes": "notes_receivable",
    "inventory": "inventory",
    "production": "production",
    "purchases": "purchases",
}


def _only_sections(result: FanResult, wanted: tuple[str, ...]) -> FanResult:
    """旧桥不认识 sections，取回的是全部段：去掉没要的段（旧桥按总账规则放行，有总账权限的才会走到这里）。"""
    if not result.ok:
        return result
    keep = {SECTION_KEY[name] for name in wanted}
    drop = set(SECTION_KEY.values()) - keep
    return FanResult(True, result.status, {k: v for k, v in (result.body or {}).items() if k not in drop}, None)


def _read_cash(run: MgmtRun, body: MgmtCashStockIn) -> tuple[dict[str, FanResult], list[str]]:
    """各账套读桥。带 sections 的请求被旧桥拒绝（400 含未知字段）时，对这些账套去掉它重试一次再筛出要的段，提醒先升级桥；
    旧桥按总账规则放行，所以没有总账权限的操作员在旧桥上照样 403，不会多看到数据。"""
    params = cash_params(body)
    results = run_mgmt(run.request, body, CASH_PATH, lambda _login: dict(params), run.ok_accs)
    old = [acc for acc, result in results.items() if "sections" in params and rejected_field(result)]
    if not old:
        return results, []
    plain_params = {key: value for key, value in params.items() if key != "sections"}
    retried = run_mgmt(run.request, body, CASH_PATH, lambda _login: dict(plain_params), old)
    results.update({acc: _only_sections(result, body.wanted()) for acc, result in retried.items()})
    return results, [f"账套 {acc} 的桥不认识 sections（请先升级桥），已读全部段再筛选" for acc in old]


def fetch_cash(run: MgmtRun, body: MgmtCashStockIn) -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    """各账套的桥结果：（成功的 acc → 响应体，by_account，warnings）。"""
    results, warnings = _read_cash(run, body)
    warnings += report_failures(results)
    bodies: dict[str, dict] = {}
    by_account: dict[str, dict] = {}
    for acc, result in results.items():
        if not result.ok:
            by_account[acc] = account_error(result)
            continue
        bodies[acc] = result.body or {}
        by_account[acc] = {"ok": True, **{key: value for key, value in bodies[acc].items() if key != "ok"}}
    return bodies, by_account, warnings


def compute_cash_stock(run: MgmtRun, body: MgmtCashStockIn) -> dict[str, Any]:
    bodies, by_account, warnings = fetch_cash(run, body)
    out: dict[str, Any] = {"by_account": by_account, "warnings": warnings, "complete": len(bodies) == len(by_account)}
    if body.period_from < body.period_to:
        out["notes"] = [NOTE_ONE_PERIOD.format(period=body.period_to)]
    if run.consolidating and out["complete"] and run.group is not None:
        merged = consolidate_cash(run.group, body, bodies)
        merged["notes"] = list(out.get("notes") or []) + merged["notes"]
        out.update(merged)
        out["warnings"] = warnings + merged["warnings"]
    return out


def section(found: dict, name: str) -> dict:
    value = found.get(name)
    return value if isinstance(value, dict) else {}


def consolidate_cash(group: IcGroup, body: MgmtCashStockIn, bodies: dict[str, dict]) -> dict[str, Any]:
    wants = body.wanted()
    consolidated: dict[str, Any] = _finance(bodies, wants)
    consolidated["unmapped"] = []
    eliminations: list[dict] = []
    warnings: list[str] = []
    if "production" in wants:
        consolidated["production"], consolidated["unmapped"] = _production(group, body.top, bodies)
    if "purchases" in wants:
        consolidated["purchases"], eliminations, warnings = _purchases(group, body.top, bodies)
    masked = masked_union(bodies.values(), *_masked_names(consolidated))
    if masked:
        consolidated[MASKED] = masked
    return {
        "consolidated": plain(consolidated),
        "eliminations": plain(eliminations),
        "notes": [NOTE_UNREALIZED, NOTE_QTY] if "inventory" in wants else [],
        "warnings": warnings,
    }


def _finance(bodies: dict[str, dict], wants: tuple[str, ...]) -> dict[str, Any]:
    """资金、应收票据、存货三段的合计（只含请求的段）。"""
    out: dict[str, Any] = {}
    if "cash" in wants:
        totals = {acc: dec(section(found, "cash").get("total")) for acc, found in bodies.items()}
        out["cash"] = {"total": sum(totals.values(), ZERO), "by_account": totals}
    if "notes" in wants:
        notes = [section(found, "notes_receivable") for found in bodies.values()]
        out["notes_receivable"] = {
            "total": sum((dec(note.get("total")) for note in notes), ZERO),
            "open_amount": sum((dec(section(note, "open").get("amount")) for note in notes), ZERO),
        }
    if "inventory" in wants:
        inv_amount: Decimal | None = ZERO
        for found in bodies.values():
            inv_amount = madd(inv_amount, mdec(section(found, "inventory"), "amount"))
        inv_qty = sum((dec(section(found, "inventory").get("qty")) for found in bodies.values()), ZERO)
        out["inventory"] = {"amount": inv_amount, "qty": inv_qty}
    return out


def _masked_names(consolidated: dict[str, Any]) -> list[str]:
    names = ["amount"] if consolidated.get("inventory", {}).get("amount", ZERO) is None else []
    purchases = consolidated.get("purchases", {})
    return names + (["external_amount"] if purchases.get("external_amount", ZERO) is None else [])


def _items(found: dict) -> list[dict]:
    return [row for row in found.get("items") or [] if isinstance(row, dict)]


def _production(group: IcGroup, top: int, bodies: dict[str, dict]) -> tuple[dict, list[dict]]:
    merged: dict[str, dict] = {}
    unmapped: list[dict] = []
    total = ZERO
    for acc, found in bodies.items():
        block = section(found, "production")
        total += dec(block.get("total_qty"))
        missing: list[str] = []
        for row in _items(block):
            code = row.get("code")
            inv = group.inventory_of(acc, code) if isinstance(code, str) else None
            if inv is None:
                missing.append(str(code))
                continue
            slot = merged.setdefault(
                inv.id, {"inventory": inv.id, "codes": dict(inv.codes), "qty": ZERO, "by_account": {}}
            )
            slot["qty"] += dec(row.get("qty"))
            slot["by_account"][acc] = {
                "code": code,
                "qty": dec(row.get("qty")),
                "class_code": row.get("class_code"),
                "class_name": row.get("class_name"),
            }
        if missing:
            unmapped.append(unmapped_entry(acc, "inventory", missing))
    items = sorted(merged.values(), key=lambda row: (-row["qty"], row["inventory"]))
    return {"total_qty": total, "items": items[:top]}, unmapped


def _purchases(group: IcGroup, top: int, bodies: dict[str, dict]) -> tuple[dict, list[dict], list[str]]:
    accs = list(bodies)
    amount: Decimal | None = ZERO
    qty = ZERO
    rows: list[dict] = []
    elims: list[dict] = []
    warnings: list[str] = []
    for acc, found in bodies.items():
        block = section(found, "purchases")
        amount = madd(amount, mdec(block, "total_amount"))
        qty += dec(block.get("total_qty"))
        partners = ic_partners(group, acc, accs, group.vendor_code)
        seen: set[str] = set()
        for row in _items(block):
            code = row.get("code")
            if code in partners:
                seen.add(code)
                amount = madd(amount, mdec(row, "amount"), -1)
                qty -= dec(row.get("qty"))
                elims.append(_elim(acc, partners[code], row, block.get("source")))
            else:
                rows.append({"acc": acc, **row})
        if _others_left(section(block, "others")):
            warnings += [
                f"账套 {acc} 的内部供应商 {code}（代表账套 {company}）不在采购排行里，可能并在 others 中，没有去掉"
                for code, company in partners.items()
                if code not in seen
            ]
    rows.sort(key=lambda row: -(mdec(row, "amount") or ZERO))
    return {"external_amount": amount, "external_qty": qty, "items": rows[:top]}, elims, warnings


def _others_left(others: dict) -> bool:
    """采购排行之外还有金额；金额被遮蔽时按笔数、数量判断。"""
    amount = mdec(others, "amount")
    if amount is not None:
        return amount != ZERO
    return dec(others.get("count")) > ZERO or dec(others.get("qty")) != ZERO


def _elim(acc: str, company: str, row: dict, source: object) -> dict[str, Any]:
    return {
        "rule": "ic_purchase",
        "acc": acc,
        "company": company,
        "vendor": row.get("code"),
        "source": source,
        "amount": mdec(row, "amount"),
        "qty": dec(row.get("qty")),
    }
