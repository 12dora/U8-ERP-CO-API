"""经营管理关键指标 /v1/co/mgmt/overview：一次请求里依次算利润表（co_mgmt_pnl）、资金与存货（co_mgmt_cash，
取 period_to）、应收应付（co_mgmt_arap，两边），再从各自的按账套结果和合并结果里摘出关键指标。
逾期应收、逾期应付取未核销预收付按先进先出冲抵后的净额（overdue_net）。

某部分的金额被字段权限置空时，取自它的指标为 null，指标名记入该账套（或合并）的 masked_fields。

整份结果按 overview 自己的缓存键缓存（内层不单独缓存）。某一部分全部账套失败时只记 warnings，三部分都失败才 503。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from u8co_api.co_mgmt_arap import compute_arap
from u8co_api.co_mgmt_cash import compute_cash_stock, section
from u8co_api.co_mgmt_core import MASKED, MgmtRun, serve
from u8co_api.co_mgmt_lines import COGS, REVENUE
from u8co_api.co_mgmt_pnl import compute_pnl
from u8co_api.co_models_mgmt import MgmtPnlIn
from u8co_api.co_models_mgmt_reports import MgmtArapIn, MgmtCashStockIn, MgmtOverviewIn
from u8co_api.errors import ApiError

PART_NAME = {"pnl": "利润表", "cash_stock": "资金与存货", "arap": "应收应付"}
KPI_KEYS = (
    "revenue",
    "cogs",
    "gross_profit",
    "gross_margin_pct",
    "net_profit",
    "net_margin_pct",
    "cash",
    "inventory_value",
    "notes_receivable",
    "ar_balance",
    "ar_overdue",
    "ap_balance",
    "ap_overdue",
    "dso_days",
    "dpo_days",
)


def run_overview(request, caller, action: str, _bridge_path: str, body: MgmtOverviewIn) -> dict[str, Any]:
    return serve(request, caller, action, body, lambda run: compute_overview(run, body))


def part_bodies(body: MgmtOverviewIn) -> dict[str, Any]:
    common = {"logins": list(body.logins), "fiscal_year": body.fiscal_year, "consolidate": body.consolidate}
    return {
        "pnl": MgmtPnlIn(**common, period_from=body.period_from, period_to=body.period_to),
        "cash_stock": MgmtCashStockIn(**common, period_from=body.period_to, period_to=body.period_to),
        "arap": MgmtArapIn(
            **common, period_from=body.period_from, period_to=body.period_to, side="both", as_of=body.as_of
        ),
    }


def compute_overview(run: MgmtRun, body: MgmtOverviewIn) -> dict[str, Any]:
    bodies = part_bodies(body)
    computes: dict[str, Callable[[], dict]] = {
        "pnl": lambda: compute_pnl(run, bodies["pnl"]),
        "cash_stock": lambda: compute_cash_stock(run, bodies["cash_stock"]),
        "arap": lambda: compute_arap(run, bodies["arap"]),
    }
    parts: dict[str, dict] = {}
    warnings: list[str] = []
    failed: list[ApiError] = []
    for name, compute in computes.items():
        try:
            parts[name] = compute()
        except ApiError as exc:
            failed.append(exc)
            warnings.append(f"{PART_NAME[name]}全部账套读取失败（{exc.code}）：{exc.message}")
            continue
        warnings += [f"{PART_NAME[name]}：{line}" for line in parts[name].get("warnings") or []]
    if not parts:
        raise failed[0]
    by_account = {acc: _account_kpis(acc, parts) for acc in run.accs if acc in run.ok_accs}
    complete = len(parts) == len(computes) and all(part.get("complete") is True for part in parts.values())
    out: dict[str, Any] = {
        "by_account": by_account,
        "warnings": warnings,
        "complete": complete,
        "eliminations": [
            dict(item, report=name) for name, part in parts.items() for item in part.get("eliminations") or []
        ],
        "notes": list(dict.fromkeys(note for part in parts.values() for note in part.get("notes") or [])),
    }
    if run.consolidating and complete:
        out["consolidated"] = _consolidated_kpis(parts)
    return out


def _ok(part: dict | None, acc: str) -> dict | None:
    found = ((part or {}).get("by_account") or {}).get(acc)
    return found if isinstance(found, dict) and found.get("ok") is True else None


def _line_total(view: dict, line_id: str) -> Any:
    for line in view.get("lines") or []:
        if isinstance(line, dict) and line.get("id") == line_id:
            return line.get("total")
    return None


def pnl_kpis(view: dict | None) -> dict[str, Any]:
    if view is None:
        return {}
    ratios = view.get("ratios") if isinstance(view.get("ratios"), dict) else {}
    return {
        "revenue": _line_total(view, REVENUE),
        "cogs": _line_total(view, COGS),
        "gross_profit": _line_total(view, "gross_profit"),
        "gross_margin_pct": ratios.get("gross_margin_pct"),
        "net_profit": _line_total(view, "net_profit"),
        "net_margin_pct": ratios.get("net_margin_pct"),
    }


def cash_kpis(view: dict | None) -> dict[str, Any]:
    if view is None:
        return {}
    inventory = section(view, "inventory")
    notes = section(view, "notes_receivable")
    return {
        "cash": section(view, "cash").get("total"),
        "inventory_value": inventory.get("amount"),
        "notes_receivable": notes.get("total"),
    }


def arap_kpis(view: dict | None, consolidated: bool = False) -> dict[str, Any]:
    if view is None:
        return {}
    ar, ap = section(view, "ar"), section(view, "ap")
    ar_totals = section(ar, "totals")
    ap_totals = section(ap, "totals")
    days_ar = ar_totals.get("dso_days") if consolidated else ar.get("dso_days")
    days_ap = ap_totals.get("dpo_days") if consolidated else ap.get("dpo_days")
    return {
        "ar_balance": ar_totals.get("balance"),
        "ar_overdue": ar_totals.get("overdue_net"),
        "ap_balance": ap_totals.get("balance"),
        "ap_overdue": ap_totals.get("overdue_net"),
        "dso_days": days_ar,
        "dpo_days": days_ap,
    }


_KPI_OF = {"pnl": pnl_kpis, "cash_stock": cash_kpis, "arap": arap_kpis}


def _shape(found: dict[str, Any]) -> dict[str, Any]:
    return {key: found.get(key) for key in KPI_KEYS}


def _masked(views: dict[str, dict | None], kpis: dict[str, Any]) -> list[str]:
    """带 masked_fields 的部分里取值为 null 的指标名。"""
    names: list[str] = []
    for name, view in views.items():
        if isinstance(view, dict) and view.get(MASKED):
            names += [key for key in _KPI_OF[name](view) if kpis.get(key) is None]
    return names


def _account_kpis(acc: str, parts: dict[str, dict]) -> dict[str, Any]:
    views = {name: _ok(parts.get(name), acc) for name in PART_NAME}
    kpis = {**pnl_kpis(views["pnl"]), **cash_kpis(views["cash_stock"]), **arap_kpis(views["arap"])}
    missing = [PART_NAME[name] for name, view in views.items() if view is None]
    out: dict[str, Any] = {"ok": True, "kpis": _shape(kpis)}
    masked = _masked(views, kpis)
    if masked:
        out[MASKED] = masked
    if missing:
        out["missing"] = missing
    return out


def _consolidated_kpis(parts: dict[str, dict]) -> dict[str, Any] | None:
    found = {name: part.get("consolidated") for name, part in parts.items()}
    if any(not isinstance(value, dict) for value in found.values()):
        return None
    kpis = {
        **pnl_kpis(found["pnl"]),
        **cash_kpis(found["cash_stock"]),
        **arap_kpis(found["arap"], consolidated=True),
    }
    out: dict[str, Any] = {"kpis": _shape(kpis)}
    masked = _masked(found, kpis)
    if masked:
        out[MASKED] = masked
    return out
