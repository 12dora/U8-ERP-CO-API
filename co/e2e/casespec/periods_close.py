"""月末结账（periods/close）。只对桥 config.json 的 testAccounts 里的账套开放，其余账套登录前 403 test_account_only。

periods-close-dry：本年本月之前还有总账未结账的期间时，预演逐月结账到上月（through、dry_run: true）
  → 200 标准预演体（mode rollback、action period_close），detail.periods 是会结账的期间，最后一个总账期间是本月之前
  最晚的未结账期间（一般就是上月）
  → 回读 GL_mend 第 1 到 12 期的全部结账标志与预演前相同（预演回滚）。
  本年本月之前都已结账、或本月是 1 月时记 N/A；账套数据不满足结账条件（409 state_mismatch，例如库存月份有单据、
  有未记账的凭证）时也记 N/A，原文写进说明（拒绝的路径由 periods-close-refuse-order 覆盖）。
periods-close-refuse-order：总账结本年第一个未结账期间的下一期，预演和正式结账都 409 state_mismatch「上一期间还没结账」，
  标志不变。本年总账没有未结账期间或第一个未结账期间是 12 期时记 N/A。
periods-bad-module：module 不支持，登录前 400（字段校验在测试账套名单之前，任何账套都是 400）。

前两条的账套不在 testAccounts 里时桥回 403 test_account_only（桥总开关未打开时 feature_disabled），记 N/A（桥不公开名单，只能按这个错误码判断）。
步骤 kind 为 "custom"，sql_name 是操作名（PERIODS_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.assertutil import sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, call
from co.e2e.casespec.periods_close_sql import period_flags_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.casespec.tier2 import tier2_refused

ROUTE = "/v1/periods/close"
_ORDER_TEXT = "上一期间还没结账"
_GL = 8  # 一行里总账标志的下标：年度、期间、采购、销售、库存、存货、应收、应付、总账
_MODULES = ("pu", "sa", "st", "ia", "ar", "ap", "gl")


def read_flags(env: Env, spec: CaseSpec) -> list[tuple[int, ...]]:
    """GL_mend 第 1 到 12 期，一期一个元组（年度, 期间, 七个标志）；格式不对的行跳过。"""
    rows = []
    for cell in sql_cells(run_guarded(env, period_flags_sql(), spec.name)):
        parts = cell.split("|")
        if len(parts) == 9 and all(part.strip().isdigit() for part in parts):
            rows.append(tuple(int(part) for part in parts))
    return rows


def gl_open(rows: list[tuple[int, ...]], year: int) -> list[int]:
    """该年度总账未结账的期间，升序。"""
    return sorted(row[1] for row in rows if row[0] == year and row[_GL] == 0)


def _send(env: Env, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, ROUTE, fields)


def _today(env: Env) -> tuple[int, int]:
    text = str(env.today or "")
    return int(text[:4]), int(text[5:7])


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _not_test(status: int, payload: dict) -> bool:
    return tier2_refused(status, payload)


def _dry_problem(payload: dict, year: int, last: int) -> str:
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    periods = detail.get("periods") if isinstance(detail.get("periods"), list) else []
    shape = (payload.get("dry_run"), payload.get("mode"), payload.get("action"))
    if shape != (True, "rollback", "period_close"):
        return f"预演响应应 dry_run 为 true、mode 为 rollback、action 为 period_close：{payload}"[:240]
    items = [item for item in periods if isinstance(item, dict)]
    if not items or len(items) != len(periods) or any(item.get("module") not in _MODULES for item in items):
        return f"detail.periods 应是非空的 {{module, fiscal_year, period}} 列表：{periods}"[:240]
    gl = [(item.get("fiscal_year"), item.get("period")) for item in items if item.get("module") == "gl"]
    if not gl or gl[-1] != (year, last):
        return f"detail.periods 的最后一个总账期间应是 {year} 年 {last} 期，实际 {gl[-1] if gl else '没有'}"
    return ""


def periods_close_dry(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    year, month = _today(env)
    if month == 1:
        return "N/A", "本月是 1 月，本年没有本月之前的期间"
    before = read_flags(env, spec)
    earlier = [period for period in gl_open(before, year) if period < month]
    if not earlier:
        return "N/A", f"{year} 年本月之前的期间总账都已结账"
    body = {"action": "close", "through": True, "fiscal_year": year, "period": month - 1, "dry_run": True}
    status, payload = _send(env, body)
    if _not_test(status, payload):
        return "N/A", "桥没开第二级写入或账套不在 testAccounts 里（403 feature_disabled / test_account_only）"
    if status == 409 and payload.get("code") == "state_mismatch":
        return "N/A", ("账套数据不满足结账条件：" + str(payload.get("message")))[:240]
    if status != 200:
        return "FAIL", "预演逐月结账应 200，实际 " + _brief(status, payload)
    # 最后一个总账步骤是本月之前最晚的未结账期间（一般就是上月）。
    problem = _dry_problem(payload, year, earlier[-1])
    if problem:
        return "FAIL", problem
    if read_flags(env, spec) != before:
        return "FAIL", "预演之后 GL_mend 的结账标志变了（应回滚）"
    count = len(payload["detail"]["periods"])
    return "PASS", f"预演逐月结账到 {year} 年 {month - 1} 期：200，{count} 个期间，标志不变"


def _refused(status: int, payload: dict, what: str) -> str:
    if status == 409 and payload.get("code") == "state_mismatch" and _ORDER_TEXT in str(payload.get("message")):
        return ""
    return f"{what}应 409 state_mismatch「{_ORDER_TEXT}」，实际 " + _brief(status, payload)


def periods_close_refuse_order(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    year = _today(env)[0]
    before = read_flags(env, spec)
    open_periods = gl_open(before, year)
    if not open_periods or open_periods[0] >= 12:
        return "N/A", f"{year} 年总账没有未结账期间，或第一个未结账期间是 12 期"
    later = open_periods[0] + 1
    body = {"module": "gl", "action": "close", "fiscal_year": year, "period": later}
    status, payload = _send(env, dict(body, dry_run=True))
    if _not_test(status, payload):
        return "N/A", "桥没开第二级写入或账套不在 testAccounts 里（403 feature_disabled / test_account_only）"
    problem = _refused(status, payload, "预演结账")
    problem = problem or _refused(*_send(env, body), "正式结账")
    if problem:
        return "FAIL", problem
    if read_flags(env, spec) != before:
        return "FAIL", "被拒之后 GL_mend 的结账标志变了"
    return "PASS", f"{year} 年第一个未结账期间是 {open_periods[0]} 期，结 {later} 期：预演和正式结账都 409"


PERIODS_OPS = {"periods_close_dry": periods_close_dry, "periods_close_refuse_order": periods_close_refuse_order}


def periods_cases() -> list[CaseSpec]:
    # 月末结账持全局写闸门，事务里 UPDLOCK 读、改 GL_mend 的结账标志；两条之间互斥（period:close）。
    # 期初记账用例、账套体检也读写 GL_mend（opening:pu），一并错开。
    tags = frozenset(("period:close", "opening:pu"))
    dry = Step("custom", sql_name="periods_close_dry", id_from="none")
    refuse = Step("custom", sql_name="periods_close_refuse_order", id_from="none")
    fields = (("module", "xx"), ("fiscal_year", "2026"), ("period", "1"), ("action", "close"))
    bad = call(ROUTE, fields, (), 400, "bad_request")
    return [
        CaseSpec(
            "periods-close-dry",
            "预演逐月结账到上月：200 dry_run，GL_mend 标志不变（没有未结账期间、非测试账套 N/A）",
            (),
            (dry,),
            True,
            tags=tags,
            routes=(ROUTE,),
        ),
        CaseSpec(
            "periods-close-refuse-order",
            "总账结第一个未结账期间的下一期：预演和正式结账都 409「上一期间还没结账」（非测试账套 N/A）",
            (),
            (refuse,),
            True,
            tags=tags,
            routes=(ROUTE,),
        ),
        CaseSpec(
            "periods-bad-module",
            "月末结账 module 不支持：登录前 400",
            (),
            (bad,),
            tags=frozenset(("probe",)),
            routes=(ROUTE,),
        ),
    ]
