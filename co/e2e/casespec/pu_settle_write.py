"""`purchase-settle-cycle`：采购结算单写入用例，参照采购发票自动结算、再结算 409、删除。

挑最新一张可结算的发票（pu_settle_write_sql.settle_pick_write_sql：未结算、已采购复核、未应付审核、1 到 5 行、
每行参照已审核的采购入库行），结算日期取 GL_mend 里第一个采购未结账期间的最后一天（之前各月都已结账，U8 才肯结算），然后：
  预演生单 200（dry_run），回读仍没有结算行；
  生单（head.settle_date）200：响应 id、code、lines、settle_date 对得上；回读正好一张结算单，每行与发票行
    （RdsId、ID、iPBVQuantity）配对一致，发票表头、表体都写了结算日期，应付审核人仍为空；
  再生单 409（发票已结算）；
  删除（date 同结算日期）200：回读没有结算单，发票表头、表体的结算日期都已清空。
清理在生单之前登记：结束时这张发票还有结算单就删掉。挑不到数据记 N/A（空账套没有未结算的发票）。
步骤 kind 为 "custom"，sql_name 是操作名（PU_SETTLE_WRITE_OPS 并入 ops_run）。
"""

from __future__ import annotations

import time

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _Fail, _guard, _Run
from co.e2e.casespec.pu_settle_write_sql import STATE_COLS, settle_pick_write_sql, settle_state_sql

KIND = "purchase_settle"
SOURCE = "purchase_invoice"
_GEN = "/v1/vouchers/generate"
_DELETE = "/v1/vouchers/delete"
# 结果未知（0 / 504）时清理前多等一会，让可能还在跑的结算落库后再查。
_UNKNOWN = (0, 504)
_UNKNOWN_WAIT_S = 5.0
_CLEAN_ROUNDS = 5


class _Pick:
    def __init__(self, parts: list[str]) -> None:
        self.bill, self.code, self.lines, self.day = parts
        self.doc = int(self.bill)


class _State:
    def __init__(self, parts: list[str]) -> None:
        self.settles, self.psvid, self.rows, self.paired, self.bill_lines = (int(p or 0) for p in parts[:5])
        self.head_day, stamped, self.ap = parts[5], parts[6], parts[7]
        self.stamped = int(stamped or 0)


def _state(run: _Run, pick: _Pick) -> _State:
    parts = run.pick(settle_state_sql(pick.doc), STATE_COLS)
    if parts is None:
        raise _Fail(f"回读不到发票 {pick.doc} 的结算状态")
    return _State(parts)


def _gen_body(pick: _Pick, dry: bool = False) -> dict:
    body = {"type": KIND, "source_type": SOURCE, "id": pick.doc, "head": {"settle_date": pick.day}}
    if dry:
        body["dry_run"] = True
    return body


def _dry_problem(run: _Run, pick: _Pick) -> str:
    status, payload = run.send(_GEN, _gen_body(pick, True))
    if status != 200:
        return f"预演结算应 200，实际 {status} {payload.get('code')} {payload.get('message')}"[:240]
    state = _state(run, pick)
    return f"预演之后发票 {pick.doc} 有了 {state.settles} 张结算单" if state.settles else ""


def _made_problem(payload: dict, pick: _Pick) -> str:
    if str(payload.get("code") or "").strip() == "" or payload.get("lines") != int(pick.lines):
        return f"结算响应的 code / lines 不符（应 {pick.lines} 行）：{payload.get('code')} / {payload.get('lines')}"
    if payload.get("settle_date") not in (None, pick.day):
        return f"结算响应的 settle_date 应为 {pick.day}，实际 {payload.get('settle_date')}"
    return ""


def _settled_problem(state: _State, pick: _Pick, psvid: int) -> str:
    want = int(pick.lines)
    if state.settles != 1 or state.psvid != psvid:
        return f"发票 {pick.doc} 应正好有结算单 {psvid}，实际 {state.settles} 张（最大 {state.psvid}）"
    if state.rows != want or state.paired != want or state.bill_lines != want:
        return f"结算行 {state.rows}、配对一致 {state.paired}、发票行 {state.bill_lines}，都应为 {want}"
    if state.head_day != pick.day or state.stamped != want:
        return f"发票结算日期应为 {pick.day}（{want} 行），实际表头 {state.head_day or '空'}、{state.stamped} 行"
    return f"结算后发票有了应付审核人 {state.ap}" if state.ap else ""


def _again_problem(run: _Run, pick: _Pick) -> str:
    status, payload = run.send(_GEN, _gen_body(pick))
    if status == 409:
        return ""
    return f"再结算应 409，实际 {status} {payload.get('code')} {payload.get('message')}"[:240]


def _gone_problem(state: _State, pick: _Pick) -> str:
    if state.settles or state.rows:
        return f"删除后发票 {pick.doc} 仍有 {state.settles} 张结算单"
    if state.head_day or state.stamped:
        return f"删除后发票结算日期没有清空：表头 {state.head_day or '空'}、{state.stamped} 行"
    return ""


def _cycle(run: _Run, holder: dict) -> tuple[str, str]:
    parts = run.pick(settle_pick_write_sql(), 4)
    if parts is None:
        return "N/A", "本账套没有可结算的采购发票（或 GL_mend 没有采购未结账的期间）"
    pick = _Pick(parts)
    holder["pick"] = pick  # 生单之前登记清理
    problem = _dry_problem(run, pick)
    if problem:
        raise _Fail(problem)
    status, made = run.send(_GEN, _gen_body(pick))
    if status != 200:
        holder["unknown"] = status in _UNKNOWN
        raise _Fail(f"采购结算：{status} {made.get('code')} {made.get('message')}"[:240])
    psvid = made.get("id") if type(made.get("id")) is int else 0
    problem = _made_problem(made, pick) or _settled_problem(_state(run, pick), pick, psvid) or _again_problem(run, pick)
    if problem:
        raise _Fail(problem)
    run.ok(_DELETE, {"type": KIND, "id": psvid, "date": pick.day}, "删除采购结算单")
    problem = _gone_problem(_state(run, pick), pick)
    if problem:
        raise _Fail(problem)
    return "PASS", f"发票 {pick.doc}（{pick.code}，{pick.lines} 行）结算日期 {pick.day}：结算单 {psvid} 配对一致，再结算 409，删除后还原"


def _cleanup(env: Env, spec: CaseSpec, holder: dict) -> str:
    """删到这张发票没有结算单为止（每轮删最大的一张，最多 _CLEAN_ROUNDS 轮）。"""
    pick = holder.get("pick")
    if pick is None:
        return ""
    if holder.get("unknown"):
        time.sleep(_UNKNOWN_WAIT_S)
    run = _Run(env, spec)
    try:
        for _ in range(_CLEAN_ROUNDS):
            state = _state(run, pick)
            if state.settles == 0:
                return ""
            status, payload = run.send(_DELETE, {"type": KIND, "id": state.psvid, "date": pick.day})
            if status != 200:
                return f"删除结算单 {state.psvid}：{status} {payload.get('message')}"[:240]
    except Exception as exc:  # 清理不能因任何异常被跳过或中断整轮回归
        return f"发票 {pick.doc}：{exc}"[:240]
    return f"发票 {pick.doc} 删了 {_CLEAN_ROUNDS} 轮仍有结算单"


def pu_settle_cycle(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    holder: dict = {}
    result = ("FAIL", "用例中途异常")
    try:
        result = _guard(env, spec, lambda run: _cycle(run, holder))
    finally:
        left = _cleanup(env, spec, holder)
    if not left:
        return result
    head = result[1] + "；" if result[0] == "FAIL" else ""
    return "FAIL", (head + "清理失败：" + left)[:240]


PU_SETTLE_WRITE_OPS = {"pu_settle_cycle": pu_settle_cycle}


def pu_settle_write_cases() -> list[CaseSpec]:
    # 生单与桥 DocLocks 的 new:purchase_settle 同名；w:purchase_settle 让 purchase-settle-read 的列表断言避开；
    # 结算改发票（w:purchase_invoice，带出 chain:pu）；结算日期取自 GL_mend 的采购结账标志，与改它的用例互斥（opening:pu）。
    tags = frozenset(("new:" + KIND, "w:" + KIND, "w:" + SOURCE, "opening:pu"))
    step = Step("custom", sql_name="pu_settle_cycle", id_from="none")
    title = "采购结算：参照采购发票预演、自动结算（配对、结算日期）、再结算 409、删除后发票还原"
    return [CaseSpec("purchase-settle-cycle", title, (), (step,), True, optional=True, tags=tags,
                     routes=(_GEN, _DELETE))]
