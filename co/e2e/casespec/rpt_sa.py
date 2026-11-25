"""销售类报表只读用例：在手订单（open_orders）、销售价格历史（sales_price_history）、客户变动（customer_churn）。不改库。

步骤 kind 为 "custom"，sql_name 是操作名（RPT_SA_OPS 并入 ops_run）。
在手订单：按客户、存货各汇总一次，合计与分组方式无关且等于各行之和（没被 limit 截断时）；逐订单行 limit 5，每行一行、
预发货日期从早到晚。价格历史：用在手订单里的客户查全部历史，日期从新到旧，单价 = 金额 ÷ 数量，再按第一行的存货收窄一次。
客户变动：窗口与请求一致（基线止于当前窗口起日的前一天），各名单个数不超过 counts 和 limit，三张名单的排序和判定字段自洽。
账套里没有对应数据记 N/A。
"""

from __future__ import annotations

from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.readonly import _ask

LIMIT = 5
_CENT = Decimal("0.01")
_UNIT = Decimal("0.000001")
_ORDERS = "/v1/reports/open_orders"
_PRICES = "/v1/reports/sales_price_history"
_CHURN = "/v1/reports/customer_churn"


def _dec(value: object) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _rows(payload: dict, key: str = "items") -> list[dict]:
    rows = payload.get(key)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"{key} 不是对象列表")
    return rows


def _totals_problem(payload: dict, rows: list[dict]) -> str:
    totals = payload.get("totals")
    if not isinstance(totals, dict) or not {"lines", "open_amount", "customers"} <= set(
        totals
    ):
        return "缺 totals 或其字段"
    if payload.get("truncated") is False:
        if sum(int(row["lines"]) for row in rows) != totals["lines"]:
            return "各行 lines 之和不等于 totals.lines"
        if (
            abs(
                sum((_dec(row["open_amount"]) for row in rows), Decimal(0))
                - _dec(totals["open_amount"])
            )
            > _CENT
        ):
            return "各行 open_amount 之和不等于 totals.open_amount"
    return ""


def _shape_problem(payload: dict, rows: list[dict], limit: int) -> str:
    if (
        payload.get("count") != len(rows)
        or len(rows) > limit
        or not isinstance(payload.get("truncated"), bool)
    ):
        return "count、truncated 或行数超过 limit"
    for row in rows:
        if not {
            "lines",
            "open_qty",
            "open_amount",
            "overdue_lines",
            "earliest_due",
        } <= set(row):
            return "行缺字段"
        if row["overdue_lines"] > row["lines"]:
            return "overdue_lines 超过 lines"
    return ""


def _order_rows_problem(rows: list[dict]) -> str:
    dues = [str(row.get("due_date") or "9999-99-99") for row in rows]
    if any(
        row.get("lines") != 1 or not row.get("so_code") for row in rows
    ) or dues != sorted(dues):
        return "逐单行不是一行一单行，或预发货日期不是从早到晚"
    return ""


def open_orders(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    by_customer = _ask(env, _ORDERS, {"limit": LIMIT})
    rows = _rows(by_customer)
    problem = _shape_problem(by_customer, rows, LIMIT) or _totals_problem(
        by_customer, rows
    )
    if problem:
        return "FAIL", problem
    if by_customer["totals"]["lines"] == 0:
        return "N/A", "本账套没有在手订单"
    by_inv = _ask(env, _ORDERS, {"group_by": "inventory", "limit": LIMIT})
    problem = _shape_problem(by_inv, _rows(by_inv), LIMIT) or _totals_problem(
        by_inv, _rows(by_inv)
    )
    if problem or by_inv["totals"] != by_customer["totals"]:
        return "FAIL", problem or "按存货汇总的合计与按客户汇总的不一致"
    orders = _ask(
        env, _ORDERS, {"group_by": "order", "basis": "invoice", "limit": LIMIT}
    )
    problem = _order_rows_problem(_rows(orders))
    if problem:
        return "FAIL", problem
    totals = by_customer["totals"]
    return (
        "PASS",
        f"{totals['lines']} 行、{totals['customers']} 个客户，当前页 {len(rows)} 行",
    )


def _price_problem(payload: dict, rows: list[dict], limit: int) -> str:
    if (
        payload.get("count") != len(rows)
        or len(rows) > limit
        or not isinstance(payload.get("truncated"), bool)
    ):
        return "count、truncated 或行数超过 limit"
    dates = [row["date"] for row in rows]
    if dates != sorted(dates, reverse=True):
        return "日期不是从新到旧"
    for row in rows:
        qty, amount = _dec(row["qty"]), _dec(row["amount"])
        want = None if qty == 0 else (amount / qty).quantize(_UNIT)
        got = row["unit_price"]
        if (want is None) != (got is None) or (
            want is not None and abs(_dec(got) - want) > _UNIT
        ):
            return "unit_price 不等于 amount ÷ qty"
    return ""


def sales_price_history(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    customers = _rows(_ask(env, _ORDERS, {"limit": 1}))
    if not customers:
        return "N/A", "本账套没有在手订单，挑不到客户"
    body = {
        "customer": [customers[0]["customer_code"]],
        "date_from": "2000-01-01",
        "limit": LIMIT,
    }
    payload = _ask(env, _PRICES, body)
    rows = _rows(payload)
    problem = _price_problem(payload, rows, LIMIT)
    if problem:
        return "FAIL", problem
    if not rows:
        return "N/A", "该客户没有销售发票"
    narrowed = _ask(env, _PRICES, dict(body, inv=[rows[0]["inv_code"]]))
    if any(row["inv_code"] != rows[0]["inv_code"] for row in _rows(narrowed)):
        return "FAIL", "按存货收窄后仍有其他存货"
    return "PASS", f"{len(rows)} 行，最新 {rows[0]['date']}"


def _window_problem(payload: dict, env: Env, body: dict) -> str:
    win = payload.get("windows")
    if (
        not isinstance(win, dict)
        or win.get("current_from") != body["date_from"]
        or win.get("current_to") != env.today
    ):
        return "windows 与请求不符"
    base_to, cur_from = win.get("baseline_to"), win.get("current_from")
    if (
        not isinstance(base_to, str)
        or not base_to < cur_from
        or payload.get("lookback_months") != body["lookback_months"]
    ):
        return "基线窗口没有止于当前窗口之前"
    return ""


def _lists_problem(payload: dict, limit: int) -> str:
    counts = payload.get("counts")
    if not isinstance(counts, dict) or not {
        "new",
        "lost",
        "declining",
        "active",
    } <= set(counts):
        return "缺 counts"
    for key, sort_key in (("new", "revenue_current"), ("lost", "revenue_baseline")):
        rows = _rows(payload, key)
        values = [_dec(row[sort_key]) for row in rows]
        if len(rows) != min(limit, counts[key]) or values != sorted(
            values, reverse=True
        ):
            return f"{key} 个数或排序不对"
    if len(_rows(payload, "declining")) != min(limit, counts["declining"]):
        return "declining 个数不对"
    return ""


def _members_problem(payload: dict, date_from: str) -> str:
    if any(row["first_date"] < date_from for row in _rows(payload, "new")):
        return "新客户的第一张发票早于 date_from"
    if any(
        _dec(row["revenue_current"]) != 0 or _dec(row["revenue_baseline"]) <= 0
        for row in _rows(payload, "lost")
    ):
        return "流失客户当前窗口有收入，或基线窗口收入不为正"
    if any(
        _dec(row["revenue_baseline"]) <= 0 or row["months_current"] < 1
        for row in _rows(payload, "declining")
    ):
        return "下降客户基线收入不为正，或当前窗口没有发票月"
    return ""


def customer_churn(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    body = {
        "date_from": env.today[:4] + "-01-01",
        "lookback_months": 6,
        "decline_pct": 30,
        "limit": LIMIT,
    }
    payload = _ask(env, _CHURN, dict(body, date_to=env.today))
    problem = _window_problem(payload, env, body) or _lists_problem(payload, LIMIT)
    problem = problem or _members_problem(payload, body["date_from"])
    if problem:
        return "FAIL", problem
    counts = payload["counts"]
    if counts["active"] == 0 and counts["lost"] == 0:
        return "N/A", "本账套在这两个窗口里没有销售发票"
    return (
        "PASS",
        f"新 {counts['new']}、流失 {counts['lost']}、下降 {counts['declining']}、活跃 {counts['active']}",
    )


RPT_SA_OPS = {
    "report_open_orders": open_orders,
    "report_sales_price_history": sales_price_history,
    "report_customer_churn": customer_churn,
}

_TITLES = (
    (
        "report_open_orders",
        "报表：在手订单（合计与分组无关、等于各行之和，逐单按预发货日期排序）",
    ),
    (
        "report_sales_price_history",
        "报表：销售价格历史（日期从新到旧，单价 = 金额 ÷ 数量）",
    ),
    ("report_customer_churn", "报表：客户变动（窗口、名单个数与排序、判定字段自洽）"),
)


def rpt_sa_cases() -> list[CaseSpec]:
    rows = []
    for op, title in _TITLES:
        name = op.replace("_", "-")
        step = Step("custom", sql_name=op, id_from="none")
        rows.append(
            CaseSpec(
                name,
                title,
                (),
                (step,),
                True,
                tags=frozenset(("wave:2", "rpt:arap", "rpt:trace")),
                routes=("/v1/reports/" + op[len("report_") :],),
            )
        )
    return rows
