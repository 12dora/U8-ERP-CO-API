"""自定义步骤（Step.kind 为 "custom"）：总账凭证和档案的预演、自动核销的试算。DRY_FIN_OPS 并入 ops_run。

dry_gl_dry：fields 的 op（void / verify / delete …）对本用例新建的凭证（bag 的 core:<用例名>）预演：rollback、detail 带 gl，
  预演前后 gl/vouchers/load 完全相同。
dry_gl_new：总账凭证新建预演（validate、warnings 带 validate_only、detail 带 voucher），摘要每次不同，之后按摘要数凭证行为 0。
dry_arc_dry：fields 的 archive 按档案用例的正文新建预演（validate、detail 带 archive）；编码先由 core_arcpick 挑空闲的。
dry_plan：arap/writeoff/auto 的 dry_run 试算：200、dry_run、mode=plan、plan 非空。fields 的 builder 给正文（自带 dry_run）。
dry_hx_unlisted：上一步核销预演的 detail 里的核销号，在核销记录查询（step.route）里一批也查不到。
本模块引用 steps，不能被 wiring 链引用。
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.gl_voucher_bodies import GL_BODIES, arc_body, slot
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_judge import Want, cancel_nos, first_diff, preview_problem
from co.e2e.casespec.dry_ops import LAST_DETAIL, send
from co.e2e.casespec.dry_sql import digest_count_sql
from co.e2e.casespec.dry_steps import add_item, hx_key
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import _make
from co.e2e.testdata import SIGN_TRANSFER, gl_sign
from co.e2e.testdata import value as td

_GL = "/v1/gl/vouchers/"
_GL_OPS = ("void", "unvoid", "verify", "unverify", "delete")


def _judge(status: int, payload: dict, want: Want) -> str:
    if status != 200:
        return f"预演 {status} {payload.get('code')} {payload.get('message')}"
    return preview_problem(payload, want)


def gl_dry(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    op = dict(step.fields).get("op", "")
    if op not in _GL_OPS:
        return "FAIL", "op 不合法"
    key = GL_BODIES["key"](env, env.bag.get(slot(spec), ""))
    was, before = send(env, _GL + "load", key)
    if was != 200:
        return "FAIL", f"预演前读取凭证得 {was} {before.get('code')}，无法比较"
    status, payload = send(env, _GL + op, dict(key, dry_run=True))
    problem = _judge(status, payload, Want("rollback", "any", "", 0, False, "gl"))
    if problem:
        return "FAIL", problem[:240]
    now, after = send(env, _GL + "load", key)
    diff = f"读取状态 {was} → {now}" if now != was else first_diff(before, after)
    if diff:
        return "FAIL", f"凭证预演 {op} 前后不同：{diff}"[:240]
    return "PASS", "rollback " + op


def _digest() -> str:
    return "CO预演" + datetime.now().strftime("%Y%m%d%H%M%S") + os.urandom(2).hex()


def gl_new(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    digest = _digest()
    lines = [
        {"account": td("gl.expense_account"), "digest": digest, "debit": 1.0, "dept": td("gl.expense_dept")},
        {"account": td("gl.payable_account"), "digest": digest, "credit": 1.0},
    ]
    body = {"head": {"sign": gl_sign(SIGN_TRANSFER), "date": env.today}, "lines": lines, "dry_run": True}
    status, payload = send(env, _GL + "create", body)
    problem = _judge(status, payload, Want("validate", "any", "", 0, True, "voucher"))
    if problem:
        return "FAIL", problem[:240]
    # 会计年度取登录日期的年份。
    cell = first_cell(run_guarded(env, digest_count_sql(env.today[:4], digest), spec.name)).strip()
    if cell != "0":
        return "FAIL", f"预演之后按摘要找到 {cell or '空'} 行凭证，期望 0"
    return "PASS", "validate，没有凭证"


def arc_dry(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    archive = dict(step.fields).get("archive", "")
    inner = Step("core_arc", "/v1/archives/create", (("archive", archive),), sql_name="create")
    body = arc_body(env, spec, inner)
    body["dry_run"] = True
    status, payload = send(env, "/v1/archives/create", body)
    problem = _judge(status, payload, Want("validate", "any", "", 0, True, "archive"))
    if problem:
        return "FAIL", problem[:240]
    return "PASS", "validate " + archive


def plan_dry(env: Env, _spec: CaseSpec, step: Step) -> tuple[str, str]:
    body = _make(dict(step.fields).get("builder", ""))(env)
    if body.get("dry_run") is not True:
        return "FAIL", "试算正文没有 dry_run"
    status, payload = send(env, step.route, body)
    if status != 200:
        return "FAIL", f"试算 {status} {payload.get('code')} {payload.get('message')}"[:240]
    plan = payload.get("plan")
    if payload.get("dry_run") is not True or payload.get("mode") != "plan":
        return "FAIL", f"dry_run={payload.get('dry_run')} mode={payload.get('mode')}，期望 true / plan"
    if not isinstance(plan, list) or not plan:
        return "FAIL", "计划为空"
    return "PASS", f"plan {len(plan)} 批"


def unlisted(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    """核销预演之后：预演 detail 给出的核销号在核销记录查询（reports/arap_writeoffs）里查不到。detail 没给核销号就不查。"""
    side = dict(step.fields).get("side", "AR")
    detail = json.loads(env.bag.get(LAST_DETAIL) or "{}")
    nos = sorted({no for no in cancel_nos(detail) if no.startswith("HX" + side)})
    if not nos:
        return "PASS", "预演 detail 没有核销号，未查核销记录"
    for cancel_no in nos:
        # 先记下：万一预演真核销了，失败时 dry_cleanup 按它取消。
        add_item(env.bag, hx_key(spec.name), cancel_no)
    for cancel_no in nos:
        status, payload = send(env, step.route, {"flag": side, "cancel_no": cancel_no})
        items = payload.get("items") if status == 200 else None
        if not isinstance(items, list):
            return "FAIL", f"核销记录查询 {status} {payload.get('code')}"
        if items:
            return "FAIL", f"预演之后核销记录里查到 {cancel_no}"
    return "PASS", "核销记录里没有 " + ",".join(nos)


DRY_FIN_OPS = {"dry_gl_dry": gl_dry, "dry_gl_new": gl_new, "dry_arc_dry": arc_dry, "dry_plan": plan_dry}
DRY_FIN_OPS["dry_hx_unlisted"] = unlisted
