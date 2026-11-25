"""采购期初记账（openings/post，module=pu）。

openings-post-dry：采购管理已启用、启用年度 GL_mend 第 0 期 bflag_PU 为 0（未记账）时，预演记账（dry_run: true）
  → 200 标准预演体（mode rollback、action opening_post），detail.opening 的 posted 为 true，opening_year、start_date
  与 AccInformation 的 dPUStartDate 一致
  → 回读第 0 期仍未记账（预演回滚）。已记账的账套记 N/A，由 openings-post-refuse-posted 覆盖。
openings-post-refuse-posted：已记账时，预演记账和正式记账都 409 state_mismatch「采购期初已记账」，标志不变。
  未记账的账套记 N/A（空账套由造数 pu_opening 步骤记账）。
openings-bad-module：module 不支持（st），登录前 400（字段校验在测试账套名单之前，任何账套都是 400）。
openings-post-test-only：期初记账只对桥 config.json 的 testAccounts 里的账套开放。预演记账，非测试账套应登录前
  403 test_account_only「期初记账只对配置为测试账套的账套开放」（桥总开关未打开时为 403 feature_disabled，同样 PASS）；
  回 200 或 409 state_mismatch 说明账套在名单里，记 N/A。

采购管理未启用时前两条记 N/A；账套不在 testAccounts 里（或总开关未打开）时桥回 403 test_account_only / feature_disabled，前两条也记 N/A
（桥不公开名单，只能按这个错误码判断，由 openings-post-test-only 覆盖）。opening_balance 报表没有 pu 模块，记账状态按 SQL 回读（同桥 ReportsOpening.Posted）。
步骤 kind 为 "custom"，sql_name 是操作名（OPENINGS_OPS 并入 ops_run）。
"""

from __future__ import annotations

import re

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, call
from co.e2e.casespec.openings_pu_sql import pu_opening_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.casespec.tier2 import TIER2_TEXT, refusal_verdict, tier2_refused

ROUTE = "/v1/openings/post"
_POST = {"module": "pu", "action": "post"}
_POSTED_TEXT = "采购期初已记账"
_TEST_ONLY_TEXT = "期初记账只对配置为测试账套的账套开放"
_NOT_TEST_NA = f"桥没开第二级写入或账套不在 testAccounts 里（{TIER2_TEXT}，openings-post-test-only 覆盖）"
_YMD = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")


def pu_state(env: Env, spec: CaseSpec) -> tuple[str, str]:
    """(启用日期, 第 0 期记账标志 "0" / "1")；没启用时启用日期为空串。"""
    start, _sep, flag = first_cell(run_guarded(env, pu_opening_sql(), spec.name)).partition("|")
    start = start.strip()
    return (start if _YMD.match(start) else ""), flag.strip()


def _send(env: Env, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, ROUTE, fields)


def _not_test(status: int, payload: dict) -> bool:
    return tier2_refused(status, payload)


def _reply_problem(payload: dict, start: str) -> str:
    # 桥的标准预演体：mode rollback、action opening_post，操作后会是的状态在 detail.opening。
    detail = payload.get("detail") if isinstance(payload.get("detail"), dict) else {}
    opening = detail.get("opening") if isinstance(detail.get("opening"), dict) else {}
    if payload.get("dry_run") is not True or payload.get("mode") != "rollback":
        return f"预演响应应 dry_run 为 true、mode 为 rollback：{payload}"[:240]
    if payload.get("action") != "opening_post" or opening.get("module") != "pu" or opening.get("posted") is not True:
        return f"预演响应的 action 或 detail.opening 不对：{payload}"[:240]
    if opening.get("opening_year") != int(start[:4]) or opening.get("start_date") != start:
        return f"期初年度或启用日期不对：{opening.get('opening_year')} {opening.get('start_date')}，库里 {start}"
    return ""


def _refused(status: int, payload: dict, what: str) -> str:
    if status == 409 and payload.get("code") == "state_mismatch" and _POSTED_TEXT in str(payload.get("message")):
        return ""
    got = f"{status} {payload.get('code')} {payload.get('message')}"
    return f"{what}应 409 state_mismatch「{_POSTED_TEXT}」，实际 {got}"[:240]


def openings_post_dry(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    start, flag = pu_state(env, spec)
    if not start:
        return "N/A", "采购管理未启用"
    if flag == "1":
        return "N/A", f"{start[:4]} 年采购期初已记账（openings-post-refuse-posted 覆盖）"
    status, payload = _send(env, dict(_POST, dry_run=True))
    if _not_test(status, payload):
        return "N/A", _NOT_TEST_NA
    if status != 200:
        return "FAIL", f"预演记账应 200，实际 {status} {payload.get('code')} {payload.get('message')}"[:240]
    problem = _reply_problem(payload, start)
    if problem:
        return "FAIL", problem
    after = pu_state(env, spec)[1]
    if after != "0":
        return "FAIL", f"预演之后第 0 期记账标志应仍为 0，实际 {after or '空'}"
    return "PASS", f"{start[:4]} 年采购期初预演记账 200，标志仍未记账"


def openings_refuse_posted(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    start, flag = pu_state(env, spec)
    if not start:
        return "N/A", "采购管理未启用"
    if flag != "1":
        return "N/A", f"{start[:4]} 年采购期初未记账（openings-post-dry 覆盖）"
    status, payload = _send(env, dict(_POST, dry_run=True))
    if _not_test(status, payload):
        return "N/A", _NOT_TEST_NA
    problem = _refused(status, payload, "预演记账")
    problem = problem or _refused(*_send(env, dict(_POST)), "再次记账")
    if problem:
        return "FAIL", problem
    after = pu_state(env, spec)[1]
    if after != "1":
        return "FAIL", f"被拒之后第 0 期记账标志应仍为 1，实际 {after or '空'}"
    return "PASS", f"{start[:4]} 年采购期初已记账：预演和正式记账都 409"


def openings_post_test_only(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del spec, step
    status, payload = _send(env, dict(_POST, dry_run=True))
    if _not_test(status, payload):
        return refusal_verdict(payload, _TEST_ONLY_TEXT, "预演期初记账")
    if status == 200 or (status == 409 and payload.get("code") == "state_mismatch"):
        return "N/A", f"账套在桥的 testAccounts 里（预演回 {status}）"
    return "FAIL", f"预演期初记账应 {TIER2_TEXT}（测试账套 200 或 409），实际 {status} {payload.get('code')}"[:240]


OPENINGS_OPS = {
    "openings_post_dry": openings_post_dry,
    "openings_refuse_posted": openings_refuse_posted,
    "openings_post_test_only": openings_post_test_only,
}


def openings_cases() -> list[CaseSpec]:
    # 期初记账持全局写闸门、改 GL_mend 的采购标志；采购发票保存要读这个标志，所以与采购链（chain:pu）互斥，
    # 两条期初用例之间也互斥（opening:pu）。
    tags = frozenset(("opening:pu", "chain:pu"))
    dry = Step("custom", sql_name="openings_post_dry", id_from="none")
    refuse = Step("custom", sql_name="openings_refuse_posted", id_from="none")
    test_only = Step("custom", sql_name="openings_post_test_only", id_from="none")
    bad = call(ROUTE, (("module", "st"), ("action", "post")), (), 400, "bad_request")
    return [
        CaseSpec(
            "openings-post-dry",
            "采购期初预演记账：200 posted、dry_run，回读仍未记账（已记账的账套、非测试账套 N/A）",
            (),
            (dry,),
            True,
            tags=tags,
            routes=(ROUTE,),
        ),
        CaseSpec(
            "openings-post-refuse-posted",
            "采购期初已记账时预演和正式记账都 409（未记账的账套、非测试账套 N/A）",
            (),
            (refuse,),
            True,
            tags=tags,
            routes=(ROUTE,),
        ),
        CaseSpec(
            "openings-post-test-only",
            "非测试账套预演期初记账：登录前 403 test_account_only / feature_disabled（测试账套 N/A）",
            (),
            (test_only,),
            True,
            tags=tags,
            routes=(ROUTE,),
        ),
        CaseSpec(
            "openings-bad-module",
            "期初记账 module 不支持：登录前 400",
            (),
            (bad,),
            tags=frozenset(("probe",)),
            routes=(ROUTE,),
        ),
    ]
