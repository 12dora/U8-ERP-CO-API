"""采购期初取消记账（openings/post，module=pu，action=unpost）。

桥照 U8 依次查四条拒绝条件：期初未记账、启用月份起已有月份结账、存货核算已期初记账、账套里已有任何采购发票（不分年度）。
SQL 先读出这四项（openings_pu_unpost_sql），预先算出桥应回的那一条原文。

openings-post-pu-unpost-refused：已记账、但四条里至少一条成立时，预演取消和正式取消都应 409 state_mismatch，原文是
  算出来的那一条（同时核对桥的检查顺序）；之后第 0 期仍是已记账、启用月份之前各期的标志不变。万一真的取消了，
  恢复步骤经 API 重新记账。前提都满足（能取消）的账套记 N/A，由下一条覆盖。
openings-post-pu-unpost：前提都满足时走一整轮：预演取消（200、detail.opening.posted 为 false、标志不变）→ 正式取消
  （第 0 期和启用月份之前各期标志清成 0）→ 重新记账（标志回到原样，启用月份之前已置的期间数同取消前）。
  恢复：标志不是已记账时经 API 再记一次账并回读。已有采购发票、已有月份结账、存货核算已期初记账时
  U8 不许取消；造出这个前提要删真实单据或改存货核算，不做，记 N/A 并写明原因。

采购管理未启用、期初未记账、账套不在桥的 testAccounts 里（403 test_account_only / feature_disabled）都记 N/A。
步骤 kind 为 "custom"，sql_name 是操作名（PU_UNPOST_OPS 经 flip 并入 ops_run）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.flip_util import Fail, brief, not_test, ok, row, send, with_restore
from co.e2e.casespec.openings_pu_unpost_sql import pu_unpost_state_sql

ROUTE = "/v1/openings/post"
_POST = {"module": "pu", "action": "post"}
_UNPOST = {"module": "pu", "action": "unpost"}
_CONFLICT = (409, "state_mismatch")
_YMD = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_NOT_TEST_NA = "桥没开第二级写入或账套不在 testAccounts 里（403 feature_disabled / test_account_only，openings-post-test-only 覆盖）"
# 桥 OpeningPost 的拒绝原文，按检查顺序。
NOT_POSTED = "采购期初未记账"
CLOSED = "采购已有月份结账，不能取消期初记账"
IA_POSTED = "存货核算已期初记账，不能取消采购期初记账"
HAS_INVOICE = "已有采购发票，不能取消期初记账"


@dataclass(frozen=True)
class PuState:
    start: str
    zero: str
    closed: int
    ia: str
    invoices: int
    before: int


def _int(text: str) -> int:
    return int(text) if text.isdigit() else -1


def parse_state(parts: list[str]) -> PuState:
    if len(parts) != 6:
        raise ValueError(f"采购期初状态读不全：{parts}")
    start = parts[0] if _YMD.fullmatch(parts[0]) else ""
    return PuState(start, parts[1], _int(parts[2]), parts[3], _int(parts[4]), _int(parts[5]))


def pu_state(env: Env, spec: CaseSpec) -> PuState:
    return parse_state(row(env, spec, pu_unpost_state_sql()))


def expected_refusal(state: PuState) -> str:
    """桥对取消记账应回的拒绝原文；能取消时为空串。读不到的计数按「有」算（宁可期望拒绝）。"""
    if state.zero != "1":
        return NOT_POSTED
    if state.closed != 0:
        return CLOSED
    if state.ia == "1":
        return IA_POSTED
    if state.invoices != 0:
        return HAS_INVOICE
    return ""


def _na(env: Env, spec: CaseSpec) -> tuple[str, PuState]:
    state = pu_state(env, spec)
    if not state.start:
        return "采购管理未启用", state
    if state.zero != "1":
        return f"{state.start[:4]} 年采购期初未记账（openings-post-dry 覆盖）", state
    return "", state


def _opening(payload: dict) -> dict:
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    found = detail.get("opening") if isinstance(detail.get("opening"), dict) else {}
    return found


def _repost(env: Env, spec: CaseSpec, before: PuState) -> str:
    """恢复：第 0 期不是已记账时经 API 记账，再回读；返回问题说明，空串表示正常。"""
    now = pu_state(env, spec)
    if now.zero == "1" and now.before == before.before:
        return ""
    if now.zero != "1":
        status, payload = send(env, ROUTE, dict(_POST))
        if status != 200:
            return "重新记账失败（留给快照还原）：" + brief(status, payload)
        now = pu_state(env, spec)
    if now.zero != "1" or now.before != before.before:
        return f"恢复后第 0 期标志 {now.zero}、启用月份之前已置期间数 {now.before}（原 {before.before}）"
    return ""


def _check_refused(status: int, payload: dict, want: str, what: str) -> None:
    if (status, payload.get("code")) != _CONFLICT or want not in str(payload.get("message") or ""):
        raise Fail(f"{what}应 409 state_mismatch「{want}」，实际 " + brief(status, payload))


def pu_unpost_refused(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    reason, state = _na(env, spec)
    if reason:
        return "N/A", reason
    want = expected_refusal(state)
    if not want:
        return "N/A", "取消记账的前提都满足（openings-post-pu-unpost 覆盖）"
    status, payload = send(env, ROUTE, dict(_UNPOST, dry_run=True))
    if not_test(status, payload):
        return "N/A", _NOT_TEST_NA

    def body() -> tuple[str, str]:
        _check_refused(status, payload, want, "预演取消记账")
        _check_refused(*send(env, ROUTE, dict(_UNPOST)), want, "正式取消记账")
        after = pu_state(env, spec)
        if after.zero != "1" or after.before != state.before:
            raise Fail(f"被拒之后第 0 期标志应仍为 1、启用月份之前已置期间数应为 {state.before}，实际 {after.zero}、{after.before}")
        return "PASS", f"{state.start[:4]} 年采购期初：预演和正式取消记账都 409「{want}」，标志不变"

    return with_restore(body, (("恢复采购期初记账", lambda: _repost(env, spec, state)),))


def _cycle(env: Env, spec: CaseSpec, state: PuState) -> tuple[str, str]:
    year = int(state.start[:4])
    payload = ok(env, ROUTE, dict(_UNPOST), "取消记账")
    if payload.get("posted") is not False or payload.get("module") != "pu" or payload.get("opening_year") != year:
        raise Fail(f"取消记账响应应 posted=false、module=pu、opening_year={year}：{payload}"[:240])
    mid = pu_state(env, spec)
    if mid.zero != "0" or mid.before != 0:
        raise Fail(f"取消后第 0 期和启用月份之前各期应都清成 0，实际第 0 期 {mid.zero}、仍置 {mid.before} 个期间")
    payload = ok(env, ROUTE, dict(_POST), "重新记账")
    if payload.get("posted") is not True:
        raise Fail(f"重新记账响应应 posted=true：{payload}"[:240])
    after = pu_state(env, spec)
    if after.zero != "1" or after.before != state.before:
        raise Fail(f"重新记账后第 0 期应为 1、启用月份之前已置期间数应为 {state.before}，实际 {after.zero}、{after.before}")
    return "PASS", f"{state.start[:4]} 年采购期初：预演取消、取消（{state.before} 个期间清 0）、重新记账，标志回到原样"


def _blocked_text(state: PuState, want: str) -> str:
    facts = f"采购发票 {state.invoices} 张、启用月份起已结账 {state.closed} 个期间、存货核算期初记账标志 {state.ia}"
    return f"U8 不许取消（{want}；{facts}），造这个前提要删真实单据，不做（openings-post-pu-unpost-refused 覆盖拒绝）"


def pu_unpost_cycle(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    reason, state = _na(env, spec)
    if reason:
        return "N/A", reason
    want = expected_refusal(state)
    if want:
        return "N/A", _blocked_text(state, want)[:240]
    status, payload = send(env, ROUTE, dict(_UNPOST, dry_run=True))
    if not_test(status, payload):
        return "N/A", _NOT_TEST_NA
    if status != 200 or payload.get("dry_run") is not True or _opening(payload).get("posted") is not False:
        return "FAIL", "预演取消记账应 200、dry_run、detail.opening.posted=false，实际 " + brief(status, payload)
    if pu_state(env, spec).zero != "1":
        return "FAIL", ("预演取消之后第 0 期标志变了；" + (_repost(env, spec, state) or "已重新记账"))[:240]
    return with_restore(lambda: _cycle(env, spec, state), (("恢复采购期初记账", lambda: _repost(env, spec, state)),))


PU_UNPOST_OPS = {
    "pu_unpost_refused": pu_unpost_refused,
    "pu_unpost_cycle": pu_unpost_cycle,
}


def pu_unpost_cases() -> list[CaseSpec]:
    # 同 openings_pu：改 GL_mend 的 bflag_PU，与采购链、其余期初记账用例互斥。
    tags = frozenset(("opening:pu", "chain:pu"))
    return [
        CaseSpec(
            "openings-post-pu-unpost-refused",
            "采购期初取消记账被拒：预演和正式取消都 409，原文按桥的检查顺序（已结账、存货已期初记账、已有发票），标志不变"
            "（能取消、未记账、非测试账套 N/A）",
            (),
            (Step("custom", sql_name="pu_unpost_refused", id_from="none"),),
            True,
            tags=tags,
            routes=(ROUTE,),
        ),
        CaseSpec(
            "openings-post-pu-unpost",
            "采购期初取消记账一整轮：预演取消、取消、重新记账，标志回原样（已有采购发票等 U8 不许取消时 N/A）",
            (),
            (Step("custom", sql_name="pu_unpost_cycle", id_from="none"),),
            True,
            tags=tags,
            routes=(ROUTE,),
        ),
    ]
