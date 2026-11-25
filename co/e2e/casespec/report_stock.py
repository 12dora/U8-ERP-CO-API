"""库存与销售支持报表只读用例：库存台账、收发存汇总表、货位存量、批次存量、客户信用、价格表。不改库。

步骤 kind 为 "custom"，sql_name 是操作名（STOCK_RPT_OPS 并入 ops_run）。
收发存和台账：用现存量挑一个有数的（仓库、存货），从当年 1 月 1 日查到登录日期——汇总表的期末应等于现存量合计，
期初 + 入 − 出 = 期末；台账的期初应等于汇总表的期初，有下一页时第二页的 carry 应等于第一页最后一行的结存。
批次存量与同一（仓库、存货、批号）的现存量合计比对；客户信用核对 used 等于公式里打开的各项之和。
价格表三种各列一次（账套没有价格数据时记 N/A）。挑不到记 N/A。
"""

from __future__ import annotations

from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.readonly import _ask

LIMIT = 5
_EPS = Decimal("0.000001")
_CENT = Decimal("0.01")
_PARTS = ("order", "dispatch", "invoice", "ar", "expense")
_PRICE_KEYS = ("id", "inv_code", "start_date", "end_date", "tax_included")


def _dec(value: object) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _items(payload: dict) -> list[dict]:
    rows = payload.get("items")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("items 不是对象列表")
    return rows


def _current(env: Env, extra: dict) -> Decimal:
    """现存量合计（按条件过滤，最多 500 行，够一个存货用）。"""
    rows = _items(_ask(env, "/v1/stock/current", dict(extra, limit=500)))
    return sum((_dec(row.get("qty")) for row in rows), Decimal(0))


def _pick(env: Env) -> dict | None:
    rows = _items(_ask(env, "/v1/stock/current", {"nonzero": True, "limit": 1}))
    if not rows:
        return None
    return {"inv": rows[0].get("inv_code"), "wh": rows[0].get("wh_code")}


def _summary(env: Env, pick: dict) -> dict:
    body = {"date_from": env.today[:4] + "-01-01", "inv": pick["inv"], "wh": pick["wh"], "limit": LIMIT}
    return _ask(env, "/v1/reports/stock_summary", body)


def _summary_row(payload: dict) -> dict | None:
    rows = _items(payload)
    return rows[0] if len(rows) == 1 else None


def stock_summary(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    pick = _pick(env)
    if pick is None:
        return "N/A", "本账套没有现存量"
    row = _summary_row(_summary(env, pick))
    if row is None:
        return "FAIL", "汇总表没有且只有一行"
    closing = _dec(row.get("closing"))
    if abs(_dec(row.get("opening")) + _dec(row.get("in_qty")) - _dec(row.get("out_qty")) - closing) > _EPS:
        return "FAIL", "期初 + 入 − 出 不等于期末"
    stock = _current(env, {"inv": pick["inv"], "wh": pick["wh"]})
    if abs(closing - stock) > _EPS:
        return "FAIL", f"期末 {closing} 与现存量 {stock} 不一致"
    return "PASS", f"{pick['wh']}/{pick['inv']} 期末 {closing}"


def _ledger_rows_problem(payload: dict, rows: list[dict]) -> str:
    balance = _dec(payload.get("carry"))
    for row in rows:
        balance += _dec(row.get("in_qty")) - _dec(row.get("out_qty"))
        if abs(balance - _dec(row.get("balance"))) > _EPS:
            return "逐行结存不连续"
    if payload.get("next") is None and abs(balance - _dec(payload.get("closing"))) > _EPS:
        return "最后一页的 closing 不等于最后一行的结存"
    return ""


def _ledger_problem(env: Env, body: dict, payload: dict, opening: object) -> str:
    rows = _items(payload)
    if len(rows) > LIMIT or payload.get("inv") != body["inv"]:
        return "inv 回显或行数不对"
    if abs(_dec(payload.get("opening")) - _dec(opening)) > _EPS or payload.get("carry") != payload.get("opening"):
        return "期初与收发存汇总表不一致，或第一页 carry 不等于 opening"
    problem = _ledger_rows_problem(payload, rows)
    if problem or not payload.get("next"):
        return problem
    more = _ask(env, "/v1/reports/stock_ledger", dict(body, after=payload["next"]))
    if rows and abs(_dec(more.get("carry")) - _dec(rows[-1].get("balance"))) > _EPS:
        return "第二页的 carry 不等于第一页最后一行的结存"
    return _ledger_rows_problem(more, _items(more))


def stock_ledger(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    pick = _pick(env)
    if pick is None:
        return "N/A", "本账套没有现存量"
    row = _summary_row(_summary(env, pick))
    if row is None:
        return "FAIL", "汇总表没有且只有一行"
    body = {"inv": pick["inv"], "wh": pick["wh"], "date_from": env.today[:4] + "-01-01", "limit": LIMIT}
    payload = _ask(env, "/v1/reports/stock_ledger", body)
    problem = _ledger_problem(env, body, payload, row.get("opening"))
    if problem:
        return "FAIL", problem
    return "PASS", f"{pick['inv']} {len(_items(payload))} 行"


def position_stock(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    rows = _items(_ask(env, "/v1/reports/position_stock", {"limit": LIMIT}))
    if not rows:
        return "N/A", "本账套没有货位存量"
    if len(rows) > LIMIT or any(not row.get("position") or _dec(row.get("qty")) == 0 for row in rows):
        return "FAIL", "行数超过 limit，或有行缺货位、数量为 0"
    return "PASS", f"{len(rows)} 行"


def batch_stock(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    rows = _items(_ask(env, "/v1/reports/batch_stock", {"limit": LIMIT}))
    if not rows:
        return "N/A", "本账套没有批次存量"
    first = rows[0]
    stock = _current(env, {"wh": first.get("wh_code"), "inv": first.get("inv_code"), "batch": first.get("batch")})
    if abs(stock - _dec(first.get("qty"))) > _EPS:
        return "FAIL", f"批次数量 {first.get('qty')} 与现存量 {stock} 不一致"
    return "PASS", f"{len(rows)} 行"


def _credit_problem(payload: dict, rows: list[dict]) -> str:
    formula = payload.get("formula")
    if not isinstance(formula, dict) or payload.get("check_point") not in ("save", "verify"):
        return "缺 formula 或 check_point"
    for row in rows:
        on = [part for part in _PARTS if formula.get(part) and (part != "ar" or payload.get("ar_enabled"))]
        if abs(sum((_dec(row.get(part)) for part in on), Decimal(0)) - _dec(row.get("used"))) > _CENT:
            return "used 不等于公式里打开的各项之和"
        if row.get("controlled") is False and row.get("available") is not None:
            return "不受控客户的 available 不是 null"
    return ""


def customer_credit(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    payload = _ask(env, "/v1/reports/customer_credit", {"limit": LIMIT})
    rows = _items(payload)
    if not rows:
        return "N/A", "本账套没有客户"
    problem = "行数超过 limit" if len(rows) > LIMIT else _credit_problem(payload, rows)
    if problem:
        return "FAIL", problem
    return "PASS", f"{len(rows)} 行，检查点 {payload.get('check_point')}"


def price_list(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    counts = []
    for kind in ("customer", "inventory", "vendor"):
        payload = _ask(env, "/v1/reports/price_list", {"kind": kind, "limit": LIMIT})
        rows = _items(payload)
        if payload.get("kind") != kind or payload.get("as_of") != env.today or len(rows) > LIMIT:
            return "FAIL", f"{kind} 回显、as_of 或行数不对"
        if any(key not in row for row in rows for key in _PRICE_KEYS):
            return "FAIL", f"{kind} 行缺字段"
        counts.append(len(rows))
    if not any(counts):
        return "N/A", "本账套没有价格表数据"
    return "PASS", "客户、存货、供应商 " + "/".join(str(n) for n in counts) + " 行"


STOCK_RPT_OPS = {
    "report_stock_summary": stock_summary,
    "report_stock_ledger": stock_ledger,
    "report_position_stock": position_stock,
    "report_batch_stock": batch_stock,
    "report_customer_credit": customer_credit,
    "report_price_list": price_list,
}

_TITLES = (
    ("report_stock_summary", "报表：收发存汇总表（期末等于现存量，期初 + 入 − 出 = 期末）"),
    ("report_stock_ledger", "报表：库存台账（期初同汇总表，逐行结存与翻页连续）"),
    ("report_position_stock", "报表：货位存量"),
    ("report_batch_stock", "报表：批次存量（与现存量一致）"),
    ("report_customer_credit", "报表：客户信用（used 按额度检查公式合计）"),
    ("report_price_list", "报表：价格表（客户、存货、供应商）"),
)


# 同时核对现存量的三张报表另走 /v1/stock/current（路由供按改动选用例）。
_WITH_CURRENT = ("report_stock_summary", "report_stock_ledger", "report_batch_stock")


def _routes(op: str) -> tuple[str, ...]:
    route = "/v1/reports/" + op[len("report_") :]
    return (route, "/v1/stock/current") if op in _WITH_CURRENT else (route,)


def stock_rpt_cases() -> list[CaseSpec]:
    rows = []
    for op, title in _TITLES:
        name = op.replace("_", "-")
        rows.append(CaseSpec(name, title, (), (Step("custom", sql_name=op, id_from="none"),), True, routes=_routes(op)))
    return rows
