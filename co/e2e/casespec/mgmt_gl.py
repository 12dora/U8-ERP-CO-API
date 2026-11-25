"""经营管理报表（总账口径）reports/mgmt/pnl、mgmt/meta、mgmt/cash_stock 的只读核对。

report-mgmt-pnl：登录年度 1 月到登录月，prefix4 含未记账、缺省只计已记账各取一次，每期每个一级损益科目的贷减借与对照 SQL
  （mgmt_gl_sql.pnl_sql，同一口径的另一种写法）逐项相等；leaf + dims [dept, item] 按编码前 4 位合计后与 prefix4 相等；
  periods 的 gl_closed 与 close_status 的总账结账标志一致。整年没有损益发生额记 N/A。
report-mgmt-meta：连查两次，modules、periods、watermarks 完全相同（不含易变字段，API 拿它做缓存键）；本年度凭证行数、最大
  制单日期与 SQL 一致；总账已启用；periods 的结账标志与 close_status 一致。
report-mgmt-cash-stock：取登录年度最后一个总账已结账期间（没有就取登录月），货币资金合计、存货结存金额、已审核产成品入库
  数量、采购金额（按响应的 source 取发票或入库）与 SQL 一致；排行 items 加 others 等于合计。

全部只读，不改账套。步骤 kind 为 "custom"，sql_name 是操作名（MGMT_GL_OPS 并入 ops_run）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_ops import send
from co.e2e.casespec.flip_util import rows
from co.e2e.casespec.mgmt_gl_sql import cash_sql, meta_sql, pnl_sql

PNL = "/v1/reports/mgmt/pnl"
META = "/v1/reports/mgmt/meta"
CASH = "/v1/reports/mgmt/cash_stock"
_CLOSE = "/v1/reports/close_status"
_CENT = Decimal("0.01")
_STABLE = ("fiscal_year", "modules", "periods", "watermarks")


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


def _year_month(env: Env) -> tuple[int, int]:
    return int(str(env.today)[:4]), int(str(env.today)[5:7])


def _gl_closed(env: Env) -> dict[int, bool]:
    periods = _ok(env, _CLOSE, {}).get("periods") or []
    return {p.get("period"): bool((p.get("closed") or {}).get("GL")) for p in periods if isinstance(p, dict)}


def _net_by_prefix(items: list) -> dict[tuple[int, str], Decimal]:
    found: dict[tuple[int, str], Decimal] = {}
    for item in items:
        key = (int(item.get("period")), str(item.get("code"))[:4])
        found[key] = found.get(key, Decimal(0)) + _dec(item.get("credit")) - _dec(item.get("debit"))
    return found


def _diff(got: dict, want: dict, what: str) -> str:
    for key in sorted(set(got) | set(want)):
        if abs(got.get(key, Decimal(0)) - want.get(key, Decimal(0))) > _CENT:
            return f"{what} 第 {key[0]} 期 {key[1]}：报表 {got.get(key)}，SQL {want.get(key)}"
    return ""


def _sql_net(env: Env, spec: CaseSpec, year: int, month: int, unposted: bool) -> dict[tuple[int, str], Decimal]:
    return {(int(r[0]), r[1]): _dec(r[2]) for r in rows(env, spec, pnl_sql(year, month, unposted))}


def _pnl_items(env: Env, body: dict) -> list:
    items = _ok(env, PNL, body).get("items")
    if not isinstance(items, list):
        raise ValueError("mgmt/pnl 响应缺 items")
    return items


def _periods_problem(env: Env, payload: dict) -> str:
    closed = _gl_closed(env)
    for p in payload.get("periods") or []:
        if not isinstance(p.get("unposted"), int) or bool(p.get("gl_closed")) != closed.get(p.get("period"), False):
            return f"mgmt/pnl 第 {p.get('period')} 期的 gl_closed / unposted 与 close_status 不一致"
    return ""


def pnl_read(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    year, month = _year_month(env)
    base = {"period_from": 1, "period_to": month}
    wide = _net_by_prefix(_pnl_items(env, dict(base, include_unposted=True)))
    want = _sql_net(env, spec, year, month, True)
    if not want and not wide:
        return "N/A", f"{year} 年 1–{month} 期没有损益发生额"
    problem = _diff(wide, want, "含未记账")
    if not problem:
        posted = _net_by_prefix(_pnl_items(env, dict(base, include_unposted=False)))
        problem = _diff(posted, _sql_net(env, spec, year, month, False), "只计已记账")
    if not problem:
        body = dict(base, include_unposted=True, detail="leaf", dims=["dept", "item"])
        problem = _diff(_net_by_prefix(_pnl_items(env, body)), wide, "leaf + dept + item 合计")
    if not problem:
        problem = _periods_problem(env, _ok(env, PNL, base))
    if problem:
        return "FAIL", problem
    return "PASS", f"{year} 年 1–{month} 期 {len(want)} 组（期间 × 一级科目）与 SQL 一致，明细合计、结账状态一致"


def meta_read(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    year, _month = _year_month(env)
    first, second = _ok(env, META, {}), _ok(env, META, {})
    if any(first.get(key) != second.get(key) for key in _STABLE):
        return "FAIL", "两次 mgmt/meta 的 modules / periods / watermarks 不同"
    marks = first.get("watermarks") or {}
    got = [str(marks.get("gl_lines")), str(marks.get("gl_max_date") or "")]
    want = (rows(env, spec, meta_sql(year)) or [["", ""]])[0]
    if got != want:
        return "FAIL", f"凭证水位 {got} 与 SQL {want} 不同"
    if not ((first.get("modules") or {}).get("GL") or {}).get("enabled"):
        return "FAIL", "modules.GL.enabled 应为 true"
    closed = _gl_closed(env)
    for p in first.get("periods") or []:
        if bool((p.get("closed") or {}).get("GL")) != closed.get(p.get("period"), False) or not isinstance(p.get("unposted"), int):
            return "FAIL", f"第 {p.get('period')} 期的结账标志与 close_status 不一致"
    return "PASS", f"两次一致；凭证 {got[0]} 行、最大日期 {got[1] or '无'} 与 SQL 一致"


def _pick_period(env: Env, month: int) -> int:
    closed = [p for p, flag in _gl_closed(env).items() if flag and isinstance(p, int)]
    return max(closed) if closed else month


def _rank_problem(part: dict, what: str, total_key: str, item_key: str) -> str:
    items = part.get("items") or []
    summed = sum((_dec(i.get(item_key)) for i in items), Decimal(0)) + _dec((part.get("others") or {}).get(item_key))
    if abs(summed - _dec(part.get(total_key))) > _CENT:
        return f"{what} 排行加 others 是 {summed}，合计 {part.get(total_key)}"
    return ""


def _cash_problem(payload: dict, sql_row: list[str]) -> str:
    purchases = payload.get("purchases") or {}
    want_purchase = sql_row[3] if purchases.get("source") == "invoice" else sql_row[4]
    pairs = (
        ("货币资金", (payload.get("cash") or {}).get("total"), sql_row[0]),
        ("存货结存金额", (payload.get("inventory") or {}).get("amount"), sql_row[1]),
        ("产成品入库数量", (payload.get("production") or {}).get("total_qty"), sql_row[2]),
        ("采购金额", purchases.get("total_amount"), want_purchase),
    )
    for what, got, want in pairs:
        if abs(_dec(got) - _dec(want)) > _CENT:
            return f"{what}：报表 {got}，SQL {want}"
    return _rank_problem(payload.get("production") or {}, "产量", "total_qty", "qty") or _rank_problem(
        purchases, "采购", "total_amount", "amount")


def cash_read(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    year, month = _year_month(env)
    period = _pick_period(env, month)
    payload = _ok(env, CASH, {"period": period})
    start, end = str(payload.get("period_start")), str(payload.get("period_end"))
    found = rows(env, spec, cash_sql(year, period, start, end))
    if not found or len(found[0]) != 5:
        return "FAIL", "对照 SQL 没有返回一行五列"
    problem = _cash_problem(payload, found[0])
    if problem:
        return "FAIL", f"{year} 年第 {period} 期：{problem}"
    source = (payload.get("purchases") or {}).get("source")
    return "PASS", f"{year} 年第 {period} 期（{start}–{end}）资金、存货、产量、采购（{source}）与 SQL 一致"


MGMT_GL_OPS = {"mgmt_pnl_read": pnl_read, "mgmt_meta_read": meta_read, "mgmt_cash_read": cash_read}


def mgmt_gl_cases() -> list[CaseSpec]:
    def case(name: str, title: str, op: str, routes: tuple[str, ...]) -> CaseSpec:
        step = Step("custom", sql_name=op, id_from="none")
        return CaseSpec(name, title, (), (step,), True, optional=True, routes=routes)

    return [
        case("report-mgmt-pnl", "经营管理损益：去掉期间损益结转后与对照 SQL 一致，明细合计、结账状态一致", "mgmt_pnl_read",
             (PNL, _CLOSE)),
        case("report-mgmt-meta", "经营管理数据水位：两次一致、凭证水位与 SQL 一致", "mgmt_meta_read", (META, _CLOSE)),
        case("report-mgmt-cash-stock", "经营管理资金存货：资金、存货、产量、采购合计与对照 SQL 一致", "mgmt_cash_read",
             (CASH, _CLOSE)),
    ]
