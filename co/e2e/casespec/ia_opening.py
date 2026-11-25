"""存货核算期初记账（openings/post module=ia）在测试账套上的一整轮。

openings-ia-post-live：
  1. 读账套状态（存货核算、库存启用日期，第 0 期标志，启用年度已结账月数、日常数据、期初行、期初记账痕迹，取数口径的
     已审核期初结存单行数）。存货核算或库存未启用、两者启用日期不同、存货核算已期初记账时记 N/A（不动账套）。
  2. 以存货核算启用日期登录，预演取消期初记账：非测试账套 403 test_account_only / feature_disabled 记 N/A；测试账套应 409「存货核算期初未记账」。
  3. 库存已月结时临时清 bflag_ST（复用 openings_live 的 open_st_flags / restore_st_flags），新增并审核一张期初结存单
     （1 行，数量 4、单位成本 3）。
  4. 预演期初记账（rollback）：detail.opening 的 counts 核对取数行数 st34_verified、期初明细 subsidiary_m0_34 等于取数口径的
     行数，summary_m0 ≥ 1、gl_mend_p0 = 1；预演之后第 0 期标志和期初行数不变（确实回滚了）。
  5. 只有「取消一定会成功」的账套（启用年度没有结账月份、没有日常数据、没有期初行和期初记账痕迹）才真做：记账 → SQL 核对
     该存货的期初汇总（数量、金额等于取数口径）和第 0 期标志 → 取消 → 期初汇总清空、标志回 0。其余账套（如启用年度
     已结账的账套）不真记账，只核对未记账时真取消应 409「存货核算期初未记账」。

恢复设计：取消期初记账在已结账的账套上一律 409（U8 也不让取消），真记账就无法用接口复原，所以这类账套只预演；真做只在
取消必然成功的账套上，并且在清理里兜底：记账成功而取消没做完时清理先再取消一次，再弃审、删除本用例的期初结存单
（存货核算已期初记账时 U8 不让弃审），最后改回库存月结标志。还原不了的写明原因记 FAIL，快照还原兜底。

步骤 kind 为 "custom"，sql_name 是操作名（IA_OPENING_OPS 并入 ops_run）。独占（exclusive），带 ia:post、period:close。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.ia_opening_sql import ia_state_sql, summary_inv_sql, verified_inv_sql
from co.e2e.casespec.openings_live import (
    _NOT_TEST_NA,
    _brief,
    _created_ids,
    _Fail,
    _not_test,
    _Stock,
    _with_flags,
    open_st_flags,
    restore_st_flags,
    st_closed_periods,
)
from co.e2e.casespec.openings_live_sql import MAX_PERIODS
from co.e2e.casespec.stock_opening import CREATE, DELETE, VERIFY
from co.e2e.casespec.stock_opening_sql import stock_pick_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

ROUTE = "/v1/openings/post"
NOT_POSTED = "存货核算期初未记账"
_QTY = 4
_COST = 3
_MEMO = "回归存货核算期初记账"
_FIFO = "先进先出"


class _Skip(Exception):
    """账套形态不适合（桥按接口暂不支持拒绝）：记 N/A，照常清理。"""


@dataclass(frozen=True)
class IaState:
    start: str
    st_start: str
    posted: str
    closed: int
    daily: int
    opening_rows: int
    traces: str
    verified: int


def _int(text: str) -> int:
    return int(text) if text.isdigit() else -1


def ia_state(env: Env, spec: CaseSpec) -> IaState:
    parts = [part.strip() for part in first_cell(run_guarded(env, ia_state_sql(), spec.name)).split("|")]
    parts += [""] * (8 - len(parts))
    nums = [_int(part) for part in parts[3:6]]
    return IaState(parts[0], parts[1], parts[2], nums[0], nums[1], nums[2], parts[6], _int(parts[7]))


def na_reason(state: IaState) -> str:
    """不能跑的原因；空串表示可以跑。纯函数。"""
    if len(state.start) != 10:
        return "存货核算未启用"
    if len(state.st_start) != 10:
        return "库存管理未启用"
    if state.start != state.st_start:
        return f"库存（{state.st_start}）与存货核算（{state.start}）启用日期不同，桥 409 不能期初取数"
    if state.posted != "0":
        return "存货核算已期初记账（期初结存单不能增删）"
    if min(state.closed, state.daily, state.opening_rows, state.verified) < 0:
        return "读不到存货核算的期初状态"
    return ""


def can_unpost(state: IaState) -> bool:
    """取消期初记账一定成功、且取消后账套回到原样：没有结账月份、日常数据、期初行和期初记账痕迹。纯函数。"""
    return state.closed == 0 and state.daily == 0 and state.opening_rows == 0 and state.traces == "0"


class _Run:
    """一轮存货核算期初记账：期初结存单交给 _Stock，记下是否真记了账，清理时先取消记账再删单据。"""

    def __init__(self, env: Env, spec: CaseSpec, state: IaState) -> None:
        self.env = env
        self.spec = spec
        self.state = state
        self.year = int(state.start[:4])
        day = (date.fromisoformat(state.st_start) - timedelta(days=1)).isoformat()
        self.stock = _Stock(env, spec, day)
        self.posted = False

    def send(self, action: str, dry: bool = False) -> tuple[int, dict]:
        # 登录日期用存货核算启用日期（桥要求登录年度是启用年度）。
        fields = auth_fields(self.env)
        fields.update(module="ia", action=action, date=self.state.start)
        if dry:
            fields["dry_run"] = True
        return invoke(self.env.client, ROUTE, fields)

    def cells(self, query: str) -> list[str]:
        return [part.strip() for part in first_cell(run_guarded(self.env, query, self.spec.name)).split("|")]

    def cleanup(self) -> str:
        notes = []
        if self.posted:
            status, payload = self.send("unpost")
            if status == 200:
                self.posted = False
            else:
                notes.append("取消存货核算期初记账失败（留给快照还原）：" + _brief(status, payload))
        left = self.stock.cleanup()
        if left:
            notes.append(left)
        return "；".join(notes)


def _probe(run: _Run) -> tuple[str, str] | None:
    status, payload = run.send("unpost", dry=True)
    if _not_test(status, payload):
        return "N/A", _NOT_TEST_NA
    if status == 409 and payload.get("code") == "state_mismatch" and payload.get("message") == NOT_POSTED:
        return None
    return "FAIL", f"未记账时预演取消应 409「{NOT_POSTED}」，实际 " + _brief(status, payload)


def _pick(env: Env, spec: CaseSpec) -> tuple[str, str] | None:
    wh, _sep, inv = first_cell(run_guarded(env, stock_pick_sql(), spec.name)).partition("|")
    wh, inv = wh.strip(), inv.strip()
    return (wh, inv) if wh and inv else None


def _make_doc(run: _Run, picked: tuple[str, str]) -> None:
    line = {"cinvcode": picked[1], "iquantity": _QTY, "iunitcost": _COST}
    payload = run.stock.ok(CREATE, {"head": {"cwhcode": picked[0], "cmemo": _MEMO}, "lines": [line]}, "新增期初结存单")
    ids = _created_ids(payload)
    run.stock.live.extend(ids)
    if len(ids) != 1:
        raise _Fail(f"新增 1 行期初结存单应得 1 张单据，实际 ids={ids}")
    run.stock.ok(VERIFY, {"id": ids[0], "action": "verify"}, "审核期初结存单")


def _counts(payload: dict) -> dict:
    counts = payload.get("counts")
    return counts if isinstance(counts, dict) else {}


def check_dry(payload: dict, verified: int) -> str:
    """预演记账响应的问题说明；空串表示正常。纯函数。"""
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    opening = detail.get("opening") if isinstance(detail.get("opening"), dict) else {}
    counts = _counts(opening)
    if payload.get("dry_run") is not True or opening.get("module") != "ia" or opening.get("posted") is not True:
        return f"预演记账的响应不对：dry_run={payload.get('dry_run')} opening={str(opening)[:120]}"
    want = {"st34_verified": verified, "subsidiary_m0_34": verified, "gl_mend_p0": 1}
    wrong = {key: counts.get(key) for key, value in want.items() if counts.get(key) != value}
    if wrong or not isinstance(counts.get("summary_m0"), int) or counts["summary_m0"] < 1:
        return f"预演记账的计数不对：应 {want} 且 summary_m0 ≥ 1，实际 {counts}"[:240]
    return ""


def _dry_post(run: _Run, verified: int) -> dict:
    status, payload = run.send("post", dry=True)
    if status == 409 and _FIFO in str(payload.get("message")):
        raise _Skip("账套有先进先出 / 后进先出计价的期初行，桥按接口暂不支持拒绝（409）")
    if status != 200:
        raise _Fail("预演存货核算期初记账应 200，实际 " + _brief(status, payload))
    problem = check_dry(payload, verified)
    if problem:
        raise _Fail(problem)
    after = ia_state(run.env, run.spec)
    if after.posted != "0" or after.opening_rows != run.state.opening_rows:
        raise _Fail(f"预演之后账套变了：第 0 期标志 {after.posted}，期初行 {run.state.opening_rows} → {after.opening_rows}")
    return _counts(payload["detail"]["opening"])


def _real_cycle(run: _Run, inv: str, want: list[str]) -> str:
    status, payload = run.send("post")
    if status != 200 or payload.get("posted") is not True:
        raise _Fail("存货核算期初记账应 200，实际 " + _brief(status, payload))
    run.posted = True
    rows = _counts(payload).get("summary_m0")
    got = run.cells(summary_inv_sql(run.year, inv))
    expect = [want[1], want[2], "1", str(rows)]
    if len(got) != 5 or _int(got[0]) < 1 or got[1:] != expect:
        raise _Fail(f"记账后存货 {inv} 的期初汇总应为（数量|金额|第 0 期标志|期初汇总总行数）{expect}，实际 {got}")
    status, payload = run.send("unpost")
    if status != 200 or payload.get("posted") is not False or _counts(payload).get("summary_m0_left") != 0:
        raise _Fail("取消存货核算期初记账应 200 且 summary_m0_left=0，实际 " + _brief(status, payload))
    run.posted = False
    got = run.cells(summary_inv_sql(run.year, inv))
    if got != ["0", "", "", "0", "0"]:
        raise _Fail(f"取消后期初汇总应清空、第 0 期标志回 0，实际 {got}")
    return f"真记账：{inv} 期初汇总数量 {want[1]}、金额 {want[2]}，第 0 期 bflag_IA=1；取消后清空"


def _closed_unpost(run: _Run) -> str:
    status, payload = run.send("unpost")
    if status != 409 or payload.get("code") != "state_mismatch" or payload.get("message") != NOT_POSTED:
        raise _Fail(f"未记账时取消应 409「{NOT_POSTED}」，实际 " + _brief(status, payload))
    return f"启用年度已结账 {run.state.closed} 个月或已有数据：只预演，不真记账；真取消 409「{NOT_POSTED}」"


def _steps(run: _Run, picked: tuple[str, str]) -> tuple[str, str]:
    _make_doc(run, picked)
    inv = picked[1]
    want = run.cells(verified_inv_sql(inv))
    verified = ia_state(run.env, run.spec).verified
    if len(want) != 3 or verified < 1:
        raise _Fail(f"审核后读不到取数口径的期初结存单：{want}，总行数 {verified}")
    counts = _dry_post(run, verified)
    head = f"预演记账：取数 {verified} 行、期初汇总 {counts.get('summary_m0')} 行、第 0 期标志 1，回滚后不变"
    tail = _real_cycle(run, inv, want) if can_unpost(run.state) else _closed_unpost(run)
    return "PASS", f"{head}；{tail}"


def openings_ia_post_live(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    state = ia_state(env, spec)
    reason = na_reason(state)
    if reason:
        return "N/A", reason
    run = _Run(env, spec, state)
    probe = _probe(run)
    if probe is not None:
        return probe
    picked = _pick(env, spec)
    if picked is None:
        return "N/A", "挑不到不管货位的仓库和不管批次、保质期的单计量存货"
    periods = st_closed_periods(env, spec)
    if len(periods) > MAX_PERIODS:
        return "N/A", f"库存已月结的期间有 {len(periods)} 个，超过 {MAX_PERIODS}，不动标志"

    def body() -> tuple[str, str]:
        open_st_flags(env, spec, periods)
        try:
            status, detail = _steps(run, picked)
        except _Skip as exc:
            return "N/A", str(exc)
        return status, f"{detail}（临时清 {len(periods)} 个库存月结标志）"

    return _with_flags(body, run.cleanup, lambda: restore_st_flags(env, spec, periods))


IA_OPENING_OPS = {"openings_ia_post_live": openings_ia_post_live}


def ia_opening_cases() -> list[CaseSpec]:
    # 新增、审核期初结存单，记账写 IA_* 表、改 GL_mend 第 0 期，临时清库存月结标志：独占，并带 ia:post、period:close、opening:pu。
    tags = frozenset(("new:stock_opening", "w:stock_opening", "stock", "ia:post", "period:close", "opening:pu"))
    step = Step("custom", sql_name="openings_ia_post_live", id_from="none")
    title = ("存货核算期初记账：期初结存单取数、预演记账计数与回滚；取消必成功的账套真记账、核对期初汇总后取消，"
             "已结账账套只预演（未记账取消 409）；非测试账套 N/A")
    return [CaseSpec("openings-ia-post-live", title, (), (step,), True, tags=tags,
                     routes=(ROUTE, CREATE, VERIFY, DELETE), exclusive=True)]
