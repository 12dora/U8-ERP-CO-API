"""经营管理资金与存货 /v1/co/mgmt/cash_stock：各账套调桥 reports/mgmt/cash_stock（按 period_to 一个会计期间）。

合并：货币资金、应收票据、存货金额按账套相加（存货金额含未实现内部利润，不抵销）；产量只按公司间对照的存货 id 相加；
采购去掉供应商是本次所选其他公司（as_vendor）的部分。金额一律用 Decimal 计算，出参才转成数字。
存货、采购金额被字段权限置空（null）的账套，相应合计保持 null，字段名记入 consolidated.masked_fields。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from u8co_api.co_ic_aggregate import plain, unmapped_entry
from u8co_api.co_ic_core import IcGroup
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
    return {
        "fiscal_year": body.fiscal_year,
        "period": body.period_to,
        "top": body.top,
        "purchase_source": body.purchase_source,
        "include_unverified": body.include_unverified,
    }


def fetch_cash(run: MgmtRun, body: MgmtCashStockIn) -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    """各账套的桥结果：（成功的 acc → 响应体，by_account，warnings）。"""
    params = cash_params(body)
    results = run_mgmt(run.request, body, CASH_PATH, lambda _login: dict(params), run.ok_accs)
    warnings = report_failures(results)
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
        merged = consolidate_cash(run.group, body.top, bodies)
        merged["notes"] = list(out.get("notes") or []) + merged["notes"]
        out.update(merged)
        out["warnings"] = warnings + merged["warnings"]
    return out


def section(found: dict, name: str) -> dict:
    value = found.get(name)
    return value if isinstance(value, dict) else {}


def consolidate_cash(group: IcGroup, top: int, bodies: dict[str, dict]) -> dict[str, Any]:
    cash = sum((dec(section(found, "cash").get("total")) for found in bodies.values()), ZERO)
    notes_total = sum((dec(section(found, "notes_receivable").get("total")) for found in bodies.values()), ZERO)
    notes_open = sum(
        (dec(section(section(found, "notes_receivable"), "open").get("amount")) for found in bodies.values()), ZERO
    )
    inv_amount: Decimal | None = ZERO
    for found in bodies.values():
        inv_amount = madd(inv_amount, mdec(section(found, "inventory"), "amount"))
    inv_qty = sum((dec(section(found, "inventory").get("qty")) for found in bodies.values()), ZERO)
    production, unmapped = _production(group, top, bodies)
    purchases, eliminations, warnings = _purchases(group, top, bodies)
    consolidated = {
        "cash": {"total": cash, "by_account": {acc: dec(section(f, "cash").get("total")) for acc, f in bodies.items()}},
        "notes_receivable": {"total": notes_total, "open_amount": notes_open},
        "inventory": {"amount": inv_amount, "qty": inv_qty},
        "production": production,
        "purchases": purchases,
        "unmapped": unmapped,
    }
    masked = masked_union(bodies.values(), *_masked_names(consolidated))
    if masked:
        consolidated[MASKED] = masked
    return {
        "consolidated": plain(consolidated),
        "eliminations": plain(eliminations),
        "notes": [NOTE_UNREALIZED, NOTE_QTY],
        "warnings": warnings,
    }


def _masked_names(consolidated: dict[str, Any]) -> list[str]:
    names = ["amount"] if consolidated["inventory"]["amount"] is None else []
    return names + (["external_amount"] if consolidated["purchases"]["external_amount"] is None else [])


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
            slot["by_account"][acc] = {"code": code, "qty": dec(row.get("qty"))}
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
