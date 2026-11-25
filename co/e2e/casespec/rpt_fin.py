"""收付款与现金流报表只读用例：收付款汇总 receipts_payments、到期票据 notes_due、现金流量统计 cash_flow。不改库。

步骤 kind 为 "custom"，sql_name 是操作名（RPT_FIN_OPS 并入 ops_run 的 _OPS）。
每张报表各查一次，核对响应的必有字段和内部口径（net = 收付 − 退款、状态与到期天数、分期之和等于合计）；
账套本期没有数据时记 N/A，不算失败。
"""

from __future__ import annotations

from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.readonly import _ask

LIMIT = 5
_CENT = Decimal("0.01")
_TOTALS = ("docs", "amount", "refund_amount", "net_amount")
_NOTE_KEYS = (
    "code",
    "note_type",
    "partner_code",
    "partner_name",
    "sign_date",
    "expire_date",
    "amount",
    "remainder",
    "status",
    "days_to_due",
)
_NOTE_STATUS = {"open", "settled", "discounted", "endorsed", "returned"}


def _dec(value: object) -> Decimal:
    return Decimal(str(value if value is not None else 0))


def _items(payload: dict) -> list[dict]:
    rows = payload.get("items")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("items 不是对象列表")
    return rows


def _page_problem(payload: dict, rows: list[dict]) -> str:
    if payload.get("ok") is not True:
        return "ok 不是 true"
    if payload.get("count") != len(rows) or not isinstance(
        payload.get("truncated"), bool
    ):
        return "count 或 truncated 不对"
    if len(rows) > LIMIT:
        return "行数超过 limit"
    return ""


def _rec_problem(payload: dict, rows: list[dict]) -> str:
    totals = payload.get("totals")
    if not isinstance(totals, dict) or any(key not in totals for key in _TOTALS):
        return "totals 缺字段"
    for row in (*rows, totals):
        if (
            abs(
                _dec(row.get("amount"))
                - _dec(row.get("refund_amount"))
                - _dec(row.get("net_amount"))
            )
            > _CENT
        ):
            return "net_amount 不等于 amount − refund_amount"
    if payload.get("group_by") != ["month"] or any("month" not in row for row in rows):
        return "按月分组的回显或键不对"
    if not payload.get("truncated") and sum(_dec(row["docs"]) for row in rows) != _dec(
        totals["docs"]
    ):
        return "各行单据数之和不等于合计"
    return ""


def receipts_payments(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    body = {
        "side": "ar",
        "date_from": env.today[:4] + "-01-01",
        "group_by": ["month"],
        "limit": LIMIT,
    }
    payload = _ask(env, "/v1/reports/receipts_payments", body)
    rows = _items(payload)
    problem = _page_problem(payload, rows) or _rec_problem(payload, rows)
    if problem:
        return "FAIL", problem
    if not _dec(payload["totals"]["docs"]):
        return "N/A", "本账套本年没有已审核的收款单"
    return "PASS", f"{len(rows)} 行，单据 {payload['totals']['docs']} 张"


def _note_problem(env: Env, payload: dict, rows: list[dict]) -> str:
    totals = payload.get("totals")
    if not isinstance(totals, dict) or any(
        key not in totals for key in ("count", "amount", "remainder")
    ):
        return "totals 缺字段"
    if (
        payload.get("as_of") != env.today
        or payload.get("expire_from") != env.today
        or payload.get("open_only") is not True
    ):
        return "as_of、expire_from 或 open_only 回显不对"
    if int(totals["count"]) < len(rows):
        return "totals.count 小于行数"
    for row in rows:
        if any(key not in row for key in _NOTE_KEYS):
            return "行缺字段"
        if (
            row["status"] != "open"
            or _dec(row["remainder"]) == 0
            or int(row["days_to_due"]) < 0
        ):
            return "open_only 下出现非未结票据或已过期票据"
    return ""


def notes_due(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    payload = _ask(env, "/v1/reports/notes_due", {"side": "ar", "limit": LIMIT})
    rows = _items(payload)
    problem = _page_problem(payload, rows) or _note_problem(env, payload, rows)
    if problem:
        return "FAIL", problem
    if any(row["status"] not in _NOTE_STATUS for row in rows):
        return "FAIL", "status 不在取值范围内"
    if not rows:
        return "N/A", "本账套未来 30 天没有到期的未结应收票据"
    return "PASS", f"{len(rows)} 行，合计 {payload['totals']['count']} 张"


def _flow_problem(payload: dict, rows: list[dict]) -> str:
    unassigned = payload.get("unassigned")
    if payload.get("ok") is not True or not isinstance(unassigned, dict):
        return "ok 或 unassigned 不对"
    if any(key not in unassigned for key in ("vouchers", "amount")):
        return "unassigned 缺字段"
    start, end = int(payload["period_from"]), int(payload["period_to"])
    for row in rows:
        periods = row.get("periods")
        if not isinstance(periods, dict) or set(periods) != {
            str(n) for n in range(start, end + 1)
        }:
            return "periods 的期间不对"
        if row.get("direction") not in ("in", "out", None):
            return "direction 取值不对"
        if (
            abs(
                sum((_dec(v) for v in periods.values()), Decimal(0))
                - _dec(row.get("amount"))
            )
            > _CENT
        ):
            return "各期之和不等于 amount"
    return ""


def cash_flow(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    payload = _ask(env, "/v1/reports/cash_flow", {})
    rows = _items(payload)
    problem = _flow_problem(payload, rows)
    if problem:
        return "FAIL", problem
    if not rows:
        return "N/A", "本账套本年没有指定现金流量项目的凭证"
    return (
        "PASS",
        f"{len(rows)} 个项目，期间 {payload['period_from']}–{payload['period_to']}",
    )


RPT_FIN_OPS = {
    "report_receipts_payments": receipts_payments,
    "report_notes_due": notes_due,
    "report_cash_flow": cash_flow,
}

_TITLES = (
    (
        "report_receipts_payments",
        "报表：收付款汇总（按月，net = 收款 − 退款，行数之和等于合计）",
    ),
    ("report_notes_due", "报表：到期票据（未结、未来 30 天，状态与到期天数）"),
    ("report_cash_flow", "报表：现金流量统计（分期之和等于合计，含未指定项目的凭证）"),
)


def rpt_fin_cases() -> list[CaseSpec]:
    rows = []
    for op, title in _TITLES:
        name = op.replace("_", "-")
        route = "/v1/reports/" + op[len("report_") :]
        rows.append(
            CaseSpec(
                name,
                title,
                (),
                (Step("custom", sql_name=op, id_from="none"),),
                True,
                tags=frozenset(("wave:2", "rpt:arap", "rpt:gl")),
                routes=(route,),
            )
        )
    return rows
