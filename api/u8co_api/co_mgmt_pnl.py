"""经营管理利润表 /v1/co/mgmt/pnl：各账套按期间汇总损益科目（桥 reports/mgmt/pnl），按行定义（co_mgmt_lines）
归到利润表行并算派生行；合并时各行相加，再按公司间对照的 rev_cogs 抵销内部收入和成本，派生行和比率按合并数重算。

金额一律用 Decimal 计算，出参才转成数字。期间 0 表示「没有拆到期间」的抵销额：只计入合计，不出现在 periods 里。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from u8co_api.co_ic_core import FanResult
from u8co_api.co_ic_map import IcElim, IcGroup
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
from u8co_api.co_mgmt_lines import COGS, INCOME, REVENUE, MgmtLines
from u8co_api.co_models_mgmt import MgmtPnlIn

PNL_PATH = MGMT_BRIDGE + "pnl"
SALES_PATH = MGMT_BRIDGE + "sales"
NOTE_UNREALIZED = "未抵销存货中未实现的内部利润"
NOTE_NO_RULE = "公司组没有为所选账套配置收入成本抵销（rev_cogs），合并营业收入、营业成本含内部交易"
_SALES_TOP = 500
# 抵销额没有拆到期间时记在这个期间：只进合计。
UNSPLIT = 0

Amounts = dict[str, dict[int, Decimal]]


def run_pnl(request, caller, action: str, _bridge_path: str, body: MgmtPnlIn) -> dict[str, Any]:
    return serve(request, caller, action, body, lambda run: compute_pnl(run, body))


def pnl_params(body: MgmtPnlIn, lines: MgmtLines, acc: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "fiscal_year": body.fiscal_year,
        "period_from": body.period_from,
        "period_to": body.period_to,
        "include_unposted": body.include_unposted,
        "detail": "leaf" if lines.needs_leaf(acc) else "prefix4",
    }
    if body.dims:
        params["dims"] = list(body.dims)
    return params


def compute_pnl(run: MgmtRun, body: MgmtPnlIn) -> dict[str, Any]:
    lines: MgmtLines = run.settings().mgmt_lines
    results = run_mgmt(run.request, body, PNL_PATH, lambda login: pnl_params(body, lines, login.acc), run.ok_accs)
    warnings = report_failures(results)
    periods = list(range(body.period_from, body.period_to + 1))
    amounts: dict[str, Amounts] = {}
    by_account: dict[str, dict] = {}
    for acc, result in results.items():
        if not result.ok:
            by_account[acc] = account_error(result)
            continue
        rows = _rows(result.body or {})
        found, unmapped = account_amounts(lines, acc, rows)
        amounts[acc] = found
        by_account[acc] = {"ok": True, **render(lines, found, periods), "unmapped": unmapped}
        if body.dims:
            by_account[acc]["by_dim"] = dim_totals(lines, acc, rows, body.dims[0])
        warnings += _account_warnings(run, acc, body, unmapped)
    out: dict[str, Any] = {"by_account": by_account, "warnings": warnings, "complete": len(amounts) == len(results)}
    if run.consolidating and out["complete"] and run.group is not None:
        merged = consolidate_pnl(run, run.group, body, lines, amounts)
        out.update(merged)
        out["warnings"] = warnings + merged["warnings"]
        out["complete"] = merged["consolidated"] is not None
    return out


def _rows(body: dict) -> list[dict]:
    raw = body.get("items")
    if raw is None:
        raw = body.get("rows")
    return [row for row in raw or [] if isinstance(row, dict)]


def _row_amount(row: dict, sign: str) -> Decimal:
    net = dec(row.get("credit")) - dec(row.get("debit"))
    return net if sign == INCOME else -net


def _matches(code: str, prefixes: tuple[str, ...]) -> bool:
    return any(code.startswith(prefix) for prefix in prefixes)


def account_amounts(lines: MgmtLines, acc: str, rows: list[dict]) -> tuple[Amounts, list[dict]]:
    """取数行的金额（行 id → 期间 → 金额）和行定义之外的科目（code、name、net 贷减借，按编码汇总）。"""
    amounts: Amounts = {line.id: {} for line in lines.lines}
    outside: dict[str, dict[str, Any]] = {}
    for row in rows:
        code, period = str(row.get("code") or ""), row.get("period")
        if not isinstance(period, int):
            continue
        hit = False
        for line in lines.lines:
            if _matches(code, line.codes_for(acc)):
                cell = amounts[line.id]
                cell[period] = cell.get(period, ZERO) + _row_amount(row, line.sign)
                hit = True
        if not hit:
            item = outside.setdefault(code, {"code": code, "name": row.get("name"), "net": ZERO})
            item["net"] += _row_amount(row, INCOME)
    unmapped = [dict(item, net=float(item["net"])) for item in outside.values() if item["net"] != ZERO]
    return amounts, sorted(unmapped, key=lambda item: item["code"])


def dim_totals(lines: MgmtLines, acc: str, rows: list[dict], dim: str) -> dict[str, dict[str, float]]:
    """取数行按维度（部门或项目编码）的期间合计：行 id → 维度编码 → 金额；没有维度的记在空串下。"""
    found: dict[str, dict[str, Decimal]] = {}
    for row in rows:
        code = str(row.get("code") or "")
        key = row.get(f"{dim}_code", row.get(dim))
        key = str(key.get("code") if isinstance(key, dict) else (key or ""))
        for line in lines.lines:
            if _matches(code, line.codes_for(acc)):
                cell = found.setdefault(line.id, {})
                cell[key] = cell.get(key, ZERO) + _row_amount(row, line.sign)
    return {line_id: {key: float(value) for key, value in cell.items()} for line_id, cell in found.items()}


def derive(lines: MgmtLines, amounts: Amounts) -> Amounts:
    """取数行加上按顺序算出的派生行（同一期间内 plus 之和减 minus 之和）。"""
    out: Amounts = {line_id: dict(cells) for line_id, cells in amounts.items()}
    for item in lines.derived:
        keys = {period for ref in (*item.plus, *item.minus) for period in out.get(ref, {})}
        cell: dict[int, Decimal] = {}
        for period in keys:
            plus = sum((out.get(ref, {}).get(period, ZERO) for ref in item.plus), ZERO)
            minus = sum((out.get(ref, {}).get(period, ZERO) for ref in item.minus), ZERO)
            cell[period] = plus - minus
        out[item.id] = cell
    return out


def render(lines: MgmtLines, amounts: Amounts, periods: list[int]) -> dict[str, Any]:
    full = derive(lines, amounts)
    rows: list[dict[str, Any]] = []
    for line in lines.lines:
        rows.append({"id": line.id, "name": line.name, "kind": "line", "sign": line.sign, **_cells(full, line.id, periods)})
    for item in lines.derived:
        rows.append({"id": item.id, "name": item.name, "kind": "derived", **_cells(full, item.id, periods)})
    totals = {line_id: sum(cells.values(), ZERO) for line_id, cells in full.items()}
    revenue = totals.get(REVENUE, ZERO)
    ratios = {
        "gross_margin_pct": ratio(totals["gross_profit"], revenue) if "gross_profit" in totals else None,
        "net_margin_pct": ratio(totals["net_profit"], revenue) if "net_profit" in totals else None,
    }
    return {"lines": rows, "ratios": ratios}


def _cells(full: Amounts, line_id: str, periods: list[int]) -> dict[str, Any]:
    cells = full.get(line_id, {})
    return {
        "total": float(sum(cells.values(), ZERO)),
        "periods": {str(period): float(cells.get(period, ZERO)) for period in periods},
    }


def _account_warnings(run: MgmtRun, acc: str, body: MgmtPnlIn, unmapped: list[dict]) -> list[str]:
    found: list[str] = []
    if unmapped:
        codes = "、".join(item["code"] for item in unmapped[:10])
        found.append(f"账套 {acc} 有 {len(unmapped)} 个损益科目不在利润表行定义里（{codes}），没有计入各行")
    if not body.include_unposted:
        count = _unposted(run.meta_body(acc), body.period_from, body.period_to)
        if count:
            found.append(f"账套 {acc} 所选期间有 {count} 张未记账凭证，没有计入（include_unposted=false）")
    return found


def _unposted(meta: dict, low: int, high: int) -> int:
    total = 0
    for item in meta.get("periods") or []:
        if not isinstance(item, dict) or not isinstance(item.get("period"), int):
            continue
        if low <= item["period"] <= high:
            count = item.get("unposted", item.get("unposted_gl"))
            total += count if isinstance(count, int) and not isinstance(count, bool) else 0
    return total


def consolidate_pnl(
    run: MgmtRun, group: IcGroup, body: MgmtPnlIn, lines: MgmtLines, amounts: dict[str, Amounts]
) -> dict[str, Any]:
    """各账套取数行相加，按 rev_cogs 抵销内部收入、成本。抵销取数失败时 consolidated 为 None。"""
    periods = list(range(body.period_from, body.period_to + 1))
    rules = [item for item in group.elim if item.rule == "rev_cogs" and item.seller in amounts and item.buyer in amounts]
    notes = [NOTE_UNREALIZED] + ([] if rules else [NOTE_NO_RULE])
    sales = _seller_sales(run, body, rules)
    merged: Amounts = {line.id: {} for line in lines.lines}
    for found in amounts.values():
        for line_id, cells in found.items():
            target = merged.setdefault(line_id, {})
            for period, value in cells.items():
                target[period] = target.get(period, ZERO) + value
    elims: list[dict[str, Any]] = []
    warnings: list[str] = []
    for rule in rules:
        record, problem = rev_cogs_amounts(group, rule, amounts, sales)
        if record is None:
            warnings.append(problem)
            return {"consolidated": None, "eliminations": elims, "notes": notes, "warnings": warnings}
        if problem:
            warnings.append(problem)
        _subtract(merged, REVENUE, record["_revenue"])
        _subtract(merged, COGS, record["_cogs"])
        elims.append(_public(record))
    return {"consolidated": render(lines, merged, periods), "eliminations": elims, "notes": notes, "warnings": warnings}


def _subtract(merged: Amounts, line_id: str, cells: dict[int, Decimal]) -> None:
    target = merged.setdefault(line_id, {})
    for period, value in cells.items():
        target[period] = target.get(period, ZERO) - value


def _seller_sales(run: MgmtRun, body: MgmtPnlIn, rules: list[IcElim]) -> dict[str, FanResult]:
    """卖方账套按客户的销售统计：逐期间各读一次（每期取 500 组），行上补期间，others 逐期相加；任一期失败即失败。"""
    sellers = {rule.seller for rule in rules if _needs_sales(rule)}
    if not sellers:
        return {}
    rows: dict[str, list[dict]] = {acc: [] for acc in sellers}
    others: dict[str, dict[str, Decimal]] = {acc: {"revenue": ZERO, "cogs": ZERO} for acc in sellers}
    failed: dict[str, FanResult] = {}
    for period in range(body.period_from, body.period_to + 1):
        if not sellers - set(failed):
            break
        params = {
            "fiscal_year": body.fiscal_year,
            "period_from": period,
            "period_to": period,
            "group_by": ["customer"],
            "top": _SALES_TOP,
        }
        found = run_mgmt(run.request, body, SALES_PATH, lambda _login, p=params: dict(p), sellers - set(failed))
        for acc, result in found.items():
            if not result.ok:
                failed[acc] = result
                continue
            rows[acc] += [dict(row, period=period) for row in _rows(result.body or {})]
            extra = (result.body or {}).get("others")
            if isinstance(extra, dict):
                for key in ("revenue", "cogs"):
                    others[acc][key] += dec(extra.get(key))
    merged = {acc: FanResult(True, 200, {"items": rows[acc], "others": others[acc]}) for acc in sellers - set(failed)}
    return {**merged, **failed}


def _needs_sales(rule: IcElim) -> bool:
    return rule.revenue_source == "sales_to_customer" or rule.cost_source == "ia_to_customer"


def _truncated(body: dict) -> bool:
    """桥按收入取前 top 组，其余并成 others；某期 others 里有收入或成本时，内部客户那一期可能落在里面。"""
    others = body.get("others")
    if not isinstance(others, dict):
        return False
    return dec(others.get("revenue")) != ZERO or dec(others.get("cogs")) != ZERO


def _customer(row: dict) -> str | None:
    value = row.get("customer_code", row.get("customer"))
    if isinstance(value, dict):
        value = value.get("code")
    return value if isinstance(value, str) else None


def rev_cogs_amounts(group: IcGroup, rule: IcElim, amounts: dict[str, Amounts], sales: dict) -> tuple[dict | None, str]:
    """一条 rev_cogs 的抵销额（期间 → 金额）。返回（记录, 提醒）；卖方销售统计读取失败时记录为 None。"""
    code = group.customer_code(rule.seller, rule.buyer)
    rows: list[dict] = []
    use_sales = rule.seller in sales and _needs_sales(rule)
    if use_sales:
        result = sales[rule.seller]
        if not result.ok:
            message = (result.error or {}).get("message")
            return None, f"账套 {rule.seller} 的销售统计读取失败（{message}），合并利润表没有出具"
        if _truncated(result.body or {}):
            return None, (
                f"账套 {rule.seller} 的销售统计有期间超过 {_SALES_TOP} 个客户，其余并入「其他」，"
                f"对账套 {rule.buyer} 的内部收入成本无法完整抵销，合并利润表没有出具"
            )
        rows = [row for row in _rows(result.body or {}) if _customer(row) == code]
    problem = ""
    if use_sales and not rows:
        problem = f"账套 {rule.seller} 的销售统计里没有客户 {code}（代表账套 {rule.buyer}），按 0 抵销"
    seller = amounts[rule.seller]
    revenue = dict(seller.get(REVENUE, {})) if rule.revenue_source == "gl_revenue_all" else _by_period(rows, "revenue")
    cogs = dict(seller.get(COGS, {})) if rule.cost_source == "gl_cogs_all" else _by_period(rows, "cogs")
    record = {
        "rule": "rev_cogs",
        "seller": rule.seller,
        "buyer": rule.buyer,
        "customer": code,
        "revenue_source": rule.revenue_source,
        "cost_source": rule.cost_source,
        "_revenue": revenue,
        "_cogs": cogs,
    }
    return record, problem


def _by_period(rows: list[dict], key: str) -> dict[int, Decimal]:
    found: dict[int, Decimal] = {}
    for row in rows:
        period = row.get("period")
        slot = period if isinstance(period, int) and not isinstance(period, bool) else UNSPLIT
        found[slot] = found.get(slot, ZERO) + dec(row.get(key))
    return found


def _public(record: dict) -> dict[str, Any]:
    revenue, cogs = record["_revenue"], record["_cogs"]
    out = {key: value for key, value in record.items() if not key.startswith("_")}
    out["revenue"] = float(sum(revenue.values(), ZERO))
    out["cogs"] = float(sum(cogs.values(), ZERO))
    keys = sorted(set(revenue) | set(cogs))
    out["periods"] = {
        str(period): {"revenue": float(revenue.get(period, ZERO)), "cogs": float(cogs.get(period, ZERO))}
        for period in keys
    }
    return out
