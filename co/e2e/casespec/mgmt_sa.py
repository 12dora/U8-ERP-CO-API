"""经营管理报表（业务口径）reports/mgmt/sales、mgmt/arap_terms 的只读核对。

report-mgmt-sales：登录年度 1 月到登录月，按客户 × 存货取一次：收入、成本合计与对照 SQL（mgmt_sa_sql.sales_sql，
  发票日期窗口取响应的 date_from–date_to）相等；items 不超过 top、按收入降序，items 加 others 等于 totals；再按期间分组、
  不分组各取一次，合计不变，期间都在请求范围内。没有启用销售管理（sa_enabled=false）时要求 items 为空、合计为 0，记 PASS。
report-mgmt-arap-terms：应收、应付各取一次（as_of 缺省登录日期）：启用状态与账套选项一致；余额、未结票据合计与对照 SQL
  （arap_sql）相等；每个单位 余额 = 各段账龄合计 - 预收付、逾期 = 第 1 段起的合计，信用期分布的单据数之和等于 invoice_count；
  partners 加 others 等于 totals。两边都没有启用记 N/A。

全部只读，不改账套。步骤 kind 为 "custom"，sql_name 是操作名（MGMT_SA_OPS 并入 ops_run）。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_ops import send
from co.e2e.casespec.flip_util import rows
from co.e2e.casespec.mgmt_sa_sql import arap_sql, sales_sql

SALES = "/v1/reports/mgmt/sales"
ARAP = "/v1/reports/mgmt/arap_terms"
_CENT = Decimal("0.01")
_HALF = Decimal("0.005")


def _ok(env: Env, route: str, body: dict) -> dict:
    status, payload = send(env, route, body)
    if status != 200:
        raise ValueError(f"{route} {status} {payload.get('code')} {payload.get('message')}"[:200])
    return payload


def _dec(value: object) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except InvalidOperation as exc:
        raise ValueError(f"金额 {value!r} 不是数") from exc


def _near(got: object, want: object, slack: Decimal = _CENT) -> bool:
    return abs(_dec(got) - _dec(want)) <= slack


def _year_month(env: Env) -> tuple[int, int]:
    return int(str(env.today)[:4]), int(str(env.today)[5:7])


def _rank_problem(payload: dict, key: str, measure: str) -> str:
    items = payload.get(key)
    if not isinstance(items, list):
        return f"响应缺 {key}"
    summed = sum((_dec(i.get(measure)) for i in items), Decimal(0)) + _dec((payload.get("others") or {}).get(measure))
    if not _near(summed, (payload.get("totals") or {}).get(measure)):
        return f"{key} 加 others 的 {measure} 是 {summed}，totals 是 {(payload.get('totals') or {}).get(measure)}"
    return ""


def _sales_order_problem(payload: dict) -> str:
    items = payload.get("items") or []
    revenues = [_dec(i.get("revenue")) for i in items]
    if len(items) > int(payload.get("top") or 0) or revenues != sorted(revenues, reverse=True):
        return "items 超过 top 或没有按收入降序"
    return _rank_problem(payload, "items", "revenue") or _rank_problem(payload, "items", "cogs")


def _sales_sql_problem(env: Env, spec: CaseSpec, payload: dict, month: int) -> str:
    stop = (date.fromisoformat(str(payload.get("date_to"))) + timedelta(days=1)).isoformat()
    window = (str(payload.get("date_from")), stop)
    found = rows(env, spec, sales_sql(window, int(payload.get("fiscal_year")), (1, month)))
    if not found or len(found[0]) != 2:
        return "对照 SQL 没有返回一行两列"
    totals = payload.get("totals") or {}
    slack = _CENT + _HALF * int(totals.get("groups") or 0)
    if not _near(totals.get("revenue"), found[0][0], slack) or not _near(totals.get("cogs"), found[0][1], slack):
        return f"合计收入 / 成本 {totals.get('revenue')} / {totals.get('cogs')}，SQL {found[0][0]} / {found[0][1]}"
    return ""


def _regroup_problem(env: Env, base: dict, first: dict, month: int) -> str:
    want = first.get("totals") or {}
    by_period = _ok(env, SALES, dict(base, group_by=["period"]))
    whole = _ok(env, SALES, dict(base, group_by=[], top=1))
    for label, payload in (("按期间", by_period), ("不分组", whole)):
        got = payload.get("totals") or {}
        if not _near(got.get("revenue"), want.get("revenue")) or not _near(got.get("cogs"), want.get("cogs")):
            return f"{label}的合计与按客户 × 存货不同"
    if any(not 1 <= int(i.get("period") or 0) <= month for i in by_period.get("items") or []):
        return "按期间分组的 period 不在请求范围内"
    return _sales_order_problem(by_period)


def sales_read(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    year, month = _year_month(env)
    base = {"period_from": 1, "period_to": month}
    payload = _ok(env, SALES, dict(base, group_by=["customer", "inventory"]))
    totals = payload.get("totals") or {}
    if payload.get("sa_enabled") is False:
        if payload.get("items") or _dec(totals.get("revenue")) != 0:
            return "FAIL", "没有启用销售管理时 items 应为空、收入为 0"
        return "PASS", f"{year} 年：账套没有启用销售管理，items 为空"
    problem = _sales_order_problem(payload) or _sales_sql_problem(env, spec, payload, month)
    problem = problem or _regroup_problem(env, base, payload, month)
    if problem:
        return "FAIL", f"{year} 年 1–{month} 期：{problem}"[:240]
    return "PASS", (f"{year} 年 1–{month} 期 {totals.get('groups')} 组，收入 {totals.get('revenue')}、成本 {totals.get('cogs')}"
                    f" 与 SQL 一致，按期间、不分组合计不变")


def _pos(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _date_filled(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _days_match_as_of(days: object, oldest: object, as_of: object) -> str:
    raw = str(as_of or "").strip()
    if not raw:
        return ""
    try:
        start = date.fromisoformat(str(oldest).strip()[:10])
    except (TypeError, ValueError):
        return "oldest_overdue_date 不是 yyyy-MM-dd"
    try:
        report = date.fromisoformat(raw[:10])
    except (TypeError, ValueError):
        return ""
    if start + timedelta(days=int(days)) != report:
        return "oldest_overdue_date 加 max_overdue_days 与报表日期不一致"
    return ""


def _overdue_extrema_problem(days: object, oldest: object, as_of: object) -> str:
    if days is None:
        return "" if not _date_filled(oldest) else "oldest_overdue_date 有值时 max_overdue_days 不能空"
    if not _pos(days):
        return "max_overdue_days 不是正整数"
    if not _date_filled(oldest):
        return "有 max_overdue_days 时 oldest_overdue_date 不能空"
    return _days_match_as_of(days, oldest, as_of)


def _overdue_keys_problem(item: dict, as_of: object) -> str:
    if "max_overdue_days" not in item or "oldest_overdue_date" not in item:
        return "缺 max_overdue_days 或 oldest_overdue_date"
    return _overdue_extrema_problem(item.get("max_overdue_days"), item.get("oldest_overdue_date"), as_of)


def _partner_problem(item: dict, as_of: object) -> str:
    aging = [_dec(v) for v in item.get("aging") or []]
    slack = _CENT * (len(aging) + 1)
    if not aging or not _near(sum(aging, Decimal(0)) - _dec(item.get("prepaid")), item.get("balance"), slack):
        return f"单位 {item.get('code')} 的余额不等于账龄各段合计减预收付"
    if not _near(sum(aging[1:], Decimal(0)), item.get("overdue"), slack):
        return f"单位 {item.get('code')} 的逾期不等于第 1 段起的合计"
    if sum(int(t.get("count") or 0) for t in item.get("terms") or []) != int(item.get("invoice_count") or 0):
        return f"单位 {item.get('code')} 的信用期分布单据数与 invoice_count 不同"
    return _overdue_keys_problem(item, as_of)


def _member_rows(partners: list, others: object) -> list:
    members = [row for row in partners if isinstance(row, dict)]
    if isinstance(others, dict):
        members.append(others)
    return members


def _max_days(members: list) -> object:
    days = [row.get("max_overdue_days") for row in members if _pos(row.get("max_overdue_days"))]
    return max(days) if days else None


def _dates_for(members: list, want: object) -> list:
    found = []
    for row in members:
        if row.get("max_overdue_days") != want:
            continue
        oldest = row.get("oldest_overdue_date")
        if _date_filled(oldest):
            found.append(oldest)
    return found


def _extrema_problem(partners: list, others: object, totals: object) -> str:
    if not isinstance(totals, dict):
        return ""
    members = _member_rows(partners, others)
    want = _max_days(members)
    got = totals.get("max_overdue_days")
    if want is None:
        return "" if got is None else "totals 有 max_overdue_days 但成员都没有"
    if got != want:
        return "totals.max_overdue_days 不是成员最大值"
    dates = _dates_for(members, want)
    if not dates:
        return "有 max_overdue_days 时 oldest_overdue_date 不能空"
    if totals.get("oldest_overdue_date") not in dates:
        return "totals.oldest_overdue_date 不是最长逾期那名成员的日期"
    return ""


def _aging_extra_problem(partners: list, others: object, totals: object, as_of: object) -> str:
    if isinstance(others, dict):
        problem = _overdue_keys_problem(others, as_of)
        if problem:
            return "others：" + problem
    if isinstance(totals, dict):
        problem = _overdue_keys_problem(totals, as_of)
        if problem:
            return problem
    return _extrema_problem(partners, others, totals)


def _side_problem(env: Env, spec: CaseSpec, side: str) -> tuple[str, bool]:
    """（问题，是否启用）。"""
    payload = _ok(env, ARAP, {"side": side})
    found = rows(env, spec, arap_sql(side, str(payload.get("as_of"))))
    if not found or len(found[0]) != 3:
        return "对照 SQL 没有返回一行三列", False
    enabled = bool(found[0][0])
    if payload.get("enabled") is not enabled:
        return f"enabled 应为 {enabled}", enabled
    totals = payload.get("totals") or {}
    slack = _CENT + _HALF * int(totals.get("partners") or 0)
    if not _near(totals.get("balance"), found[0][1], slack) or not _near(totals.get("open_notes"), found[0][2], slack):
        return f"余额 / 票据 {totals.get('balance')} / {totals.get('open_notes')}，SQL {found[0][1]} / {found[0][2]}", enabled
    as_of = payload.get("as_of")
    for item in payload.get("partners") or []:
        problem = _partner_problem(item, as_of)
        if problem:
            return problem, enabled
    problem = _aging_extra_problem(payload.get("partners") or [], payload.get("others"), payload.get("totals") or {}, as_of)
    if problem:
        return problem, enabled
    return _rank_problem(payload, "partners", "balance"), enabled


def arap_read(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    seen = []
    for side in ("ar", "ap"):
        problem, enabled = _side_problem(env, spec, side)
        if problem:
            return "FAIL", f"{side}：{problem}"[:240]
        if enabled:
            seen.append(side)
    if not seen:
        return "N/A", "账套没有启用应收、应付款管理"
    return "PASS", f"{'、'.join(seen)} 的余额、票据与 SQL 一致，各单位账龄、逾期、信用期分布自洽"


MGMT_SA_OPS = {"mgmt_sales_read": sales_read, "mgmt_arap_read": arap_read}


def mgmt_sa_cases() -> list[CaseSpec]:
    def case(name: str, title: str, op: str, routes: tuple[str, ...]) -> CaseSpec:
        step = Step("custom", sql_name=op, id_from="none")
        return CaseSpec(name, title, (), (step,), True, optional=True, routes=routes)

    return [
        case("report-mgmt-sales", "经营管理销售分析：收入、成本合计与对照 SQL 一致，排行加 others 等于合计，换分组合计不变",
             "mgmt_sales_read", (SALES,)),
        case("report-mgmt-arap-terms", "经营管理往来账期：余额、票据与对照 SQL 一致，账龄、逾期、信用期分布自洽",
             "mgmt_arap_read", (ARAP,)),
    ]
