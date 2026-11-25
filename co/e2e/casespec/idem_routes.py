"""幂等键用在全部写路由上的用例。每个键都是新的随机键；重放要拿到与第一次完全相同的正文。

idempotency-sale-order：真建一张销售订单（沿用 sale_order 构造器）→
  修改带键 200 → 同键重放同一正文 → 同键换 cMemo 409 idempotency_mismatch；
  审核带键 200 → 同键重放同一正文 → 同键改成弃审 409 idempotency_mismatch → 不带键再审核 409 state_mismatch
  → idempotency/get 按审核路由和键查到 state=ok、status=200、response 与第一次相同 → 不带键真弃审；
  删除带键 200 → 同键重放仍是第一次的 200（不是 404）→ 读取 404。中途失败时按记下的主键尽力弃审、删除。
idempotency-gl-void：真建一张转账凭证 → 作废带键 200 → 同键重放同一正文 → 同键换一个凭证号 409 idempotency_mismatch
  （键已占用，桥不执行）→ 不带键再作废 409 → idempotency/get 按作废路由查到 → 删除、读取 404。失败时尽力作废并删除。
idempotency-wf-submit：产品检验单提交审批带键 200 → 同键重放同一正文 → 不带键再提交 409 already_submitted → 撤销提交
  → 状态 not_submitted。账套没有这类单据或没启用审批流记 N/A；失败时尽力撤销提交。
idempotency-auto-plan：应收自动核销试算（dry_run: true）带幂等键：登录前 400，field=idempotency_key。
"""

from __future__ import annotations

import os
from datetime import datetime

from co.e2e.casespec.assertutil import status_problem
from co.e2e.casespec.gl_voucher_bodies import GL_DATA, slot
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, call, call_from, keep_step
from co.e2e.casespec.dry_judge import canon, first_diff
from co.e2e.casespec.dry_ops import send
from co.e2e.casespec.dry_steps import VERIFY, ref, refuse
from co.e2e.casespec.meta_batch_judge import strip_ok
from co.e2e.casespec.gl_voucher_sql import gl_key, pack_key, parse_key
from co.e2e.testdata import SIGN_TRANSFER, gl_sign
from co.e2e.testdata import value as td

IDEM_GET = "/v1/idempotency/get"
UPDATE, DELETE, LOAD = "/v1/vouchers/update", "/v1/vouchers/delete", "/v1/vouchers/load"
GL = "/v1/gl/vouchers/"
SUBMIT, WITHDRAW = "/v1/workflow/submit", "/v1/workflow/withdraw"
AUTO = "/v1/arap/writeoff/auto"
SO_KEY = "mb_so"
WF_FLAG = "mb_wf_submitted"
_QM = (("type", "qm_product_check"), ("id", "$id"))
_OFF = ("workflow_disabled", "workflow_unknown")


def new_key() -> str:
    return "e2e-idem-" + os.urandom(8).hex()


def _so_id(env: Env) -> int:
    raw = env.bag.get(SO_KEY, "")
    if not raw.isdigit() or raw == "0":
        raise ValueError("没有新建的销售订单")
    return int(raw)


def same_again(env: Env, route: str, body: dict, first: dict) -> str:
    """同一正文（含键）再发一次：200，且与第一次完全相同。"""
    status, again = send(env, route, body)
    bad = status_problem(status, again, 200, "")
    if bad:
        return f"{route} 同键重放 {bad}"[:200]
    diff = "" if canon(first) == canon(again) else first_diff(first, again) or "(整体)"
    return f"{route} 重放与第一次不同：{diff}" if diff else ""


def replay(env: Env, route: str, body: dict) -> tuple[str, dict]:
    """同一正文（含键）连发两次：都 200，第二次与第一次完全相同。返回（问题, 第一次的正文）。"""
    status, first = send(env, route, body)
    bad = status_problem(status, first, 200, "")
    if bad:
        return f"{route} 第一次 {bad}"[:200], first
    return same_again(env, route, body, first), first


def expect(env: Env, route: str, body: dict, status: int, code: str) -> str:
    got, payload = send(env, route, body)
    bad = status_problem(got, payload, status, code)
    return f"{route} 期望 {status} {code}：{bad}"[:200] if bad else ""


def lookup(env: Env, route: str, key: str, first: dict) -> str:
    """idempotency/get：found、state ok、status 200、response 与第一次的正文相同（不计 ok）。"""
    status, payload = send(env, IDEM_GET, {"route": "/u8co" + route, "idempotency_key": key})
    if status != 200 or payload.get("found") is not True:
        return f"idempotency/get {status} found={payload.get('found')} {payload.get('code')}"
    if payload.get("state") != "ok" or payload.get("status") != 200:
        return f"idempotency/get state={payload.get('state')} status={payload.get('status')}"
    diff = first_diff(strip_ok(first), strip_ok(payload.get("response")))
    return f"idempotency/get 的 response 与第一次不同：{diff}" if diff else ""


def so_update(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    key = new_key()
    body = {"type": "sale_order", "id": _so_id(env), "head": {"cMemo": "CO幂等改"}, "idempotency_key": key}
    problem, _first = replay(env, UPDATE, body)
    other = dict(body, head={"cMemo": "CO幂等改二"})
    problem = problem or expect(env, UPDATE, other, 409, "idempotency_mismatch")
    return ("FAIL", problem) if problem else ("PASS", "update 重放、换内容 409")


def so_verify(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    key = new_key()
    body = {"type": "sale_order", "id": _so_id(env), "action": "verify", "idempotency_key": key}
    problem, first = replay(env, VERIFY, body)
    problem = problem or expect(env, VERIFY, dict(body, action="unverify"), 409, "idempotency_mismatch")
    bare = {name: value for name, value in body.items() if name != "idempotency_key"}
    problem = problem or expect(env, VERIFY, bare, 409, "state_mismatch")
    problem = problem or lookup(env, VERIFY, key, first)
    return ("FAIL", problem) if problem else ("PASS", "verify 重放、弃审 409 mismatch、裸审核 409、查到")


def so_delete(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    doc_id = _so_id(env)
    body = {"type": "sale_order", "id": doc_id, "idempotency_key": new_key()}
    problem, first = replay(env, DELETE, body)
    if not problem and first.get("deleted") is not True:
        problem = f"delete 的 deleted={first.get('deleted')}"
    problem = problem or expect(env, LOAD, {"type": "sale_order", "id": doc_id}, 404, "not_found")
    return ("FAIL", problem) if problem else ("PASS", "delete 重放仍 200，读取 404")


def gl_make(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    digest = "CO幂等作废" + datetime.now().strftime("%Y%m%d%H%M%S")
    lines = [
        {"account": td("gl.expense_account"), "digest": digest, "debit": 1.0, "dept": td("gl.expense_dept")},
        {"account": td("gl.payable_account"), "digest": digest, "credit": 1.0},
    ]
    status, payload = send(env, GL + "create", {"head": {"sign": gl_sign(SIGN_TRANSFER), "date": env.today}, "lines": lines})
    bad = status_problem(status, payload, 200, "")
    if bad:
        return "FAIL", bad[:240]
    env.bag[slot(spec)] = pack_key(gl_key(payload.get("period"), payload.get("sign"), payload.get("no")))
    return "PASS", env.bag[slot(spec)]


def gl_void(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    found = parse_key(env.bag.get(slot(spec), ""))
    ident = {"period": found.period, "sign": found.sign, "no": found.no}
    key = new_key()
    problem, first = replay(env, GL + "void", dict(ident, idempotency_key=key))
    # 键已占用（重放成功），换凭证号时桥在执行前就 409，不会动那张凭证。
    other = dict(ident, no=found.no + 1 if found.no < 32767 else found.no - 1, idempotency_key=key)
    problem = problem or expect(env, GL + "void", other, 409, "idempotency_mismatch")
    problem = problem or expect(env, GL + "void", ident, 409, "")
    problem = problem or lookup(env, GL + "void", key, first)
    return ("FAIL", problem) if problem else ("PASS", "void 重放、换凭证号 409 mismatch、裸作废 409、查到")


def wf_submit(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    body = {"type": "qm_product_check", "id": int(env.bag[spec.needs[0]]), "idempotency_key": new_key()}
    status, first = send(env, SUBMIT, body)
    if status != 200 and first.get("code") in _OFF:
        return "N/A", f"产品检验单没有启用审批流（{first.get('code')}）"
    bad = status_problem(status, first, 200, "")
    if bad:
        return "FAIL", f"submit 第一次 {bad}"[:240]
    env.bag[WF_FLAG] = str(body["id"])  # 失败时补偿撤销提交
    problem = same_again(env, SUBMIT, body, first)
    bare = {name: value for name, value in body.items() if name != "idempotency_key"}
    problem = problem or expect(env, SUBMIT, bare, 409, "already_submitted")
    return ("FAIL", problem[:240]) if problem else ("PASS", "submit 重放同一正文、裸提交 409")


MB_IDEM_OPS = {
    "mb_id_so_update": so_update,
    "mb_id_so_verify": so_verify,
    "mb_id_so_delete": so_delete,
    "mb_id_gl_make": gl_make,
    "mb_id_gl_void": gl_void,
    "mb_id_wf_submit": wf_submit,
}


def _custom(route: str, op: str) -> Step:
    return Step("custom", route, sql_name=op, id_from="none")


def _sale_order() -> CaseSpec:
    steps = (
        Step("create", sql_name="sale_order", checks=(("type", "sale_order"), ("code", "$nonempty"))),
        keep_step(SO_KEY),
        _custom(UPDATE, "mb_id_so_update"),
        _custom(VERIFY, "mb_id_so_verify"),
        call_from(VERIFY, ref("sale_order", ("action", "unverify")), "bag:" + SO_KEY),
        _custom(DELETE, "mb_id_so_delete"),
    )
    title = "幂等键：销售订单修改、审核、删除带键重放拿到第一次的结果，换内容 409，裸重复审核 409，按键查到审核结果"
    return CaseSpec("idempotency-sale-order", title, ("sale_master",), steps, True, tags=frozenset(("w:sale_order",)),
                    routes=(UPDATE, VERIFY, DELETE, IDEM_GET))


def _gl() -> CaseSpec:
    steps = (
        _custom(GL + "create", "mb_id_gl_make"),
        _custom(GL + "void", "mb_id_gl_void"),
        Step("core_gl", GL + "delete", (), 200, "", (("deleted", "true"),), "key"),
        Step("core_gl", GL + "load", (), 404, "not_found", (), "key"),
    )
    title = "幂等键：总账凭证作废带键重放拿到第一次的结果，换凭证号 409，裸重复作废 409，按键查到，再删除"
    tags = frozenset(("gl", "new:gl:转"))
    return CaseSpec("idempotency-gl-void", title, (), steps, True, data=GL_DATA, tags=tags,
                    routes=(GL + "create", GL + "void", IDEM_GET))


def _wf() -> CaseSpec:
    steps = (
        _custom(SUBMIT, "mb_id_wf_submit"),
        call(WITHDRAW, _QM),
        call("/v1/workflow/state", _QM, (("wf.status", "not_submitted"),)),
    )
    title = "幂等键：产品检验单提交审批带键重放拿到第一次的结果，裸重复提交 409，再撤销提交"
    return CaseSpec("idempotency-wf-submit", title, ("qm04_b",), steps, True, optional=True, tags=frozenset(("wf",)),
                    routes=(SUBMIT, WITHDRAW))


def _auto_plan() -> CaseSpec:
    step = refuse(AUTO, (("flag", "AR"),), dry="1", key="1", text="预演不能带", field="idempotency_key")
    title = "幂等键：应收自动核销试算（dry_run）不能带幂等键，登录前 400 field=idempotency_key"
    return CaseSpec("idempotency-auto-plan", title, (), (step,), False, tags=frozenset(("probe",)), routes=(AUTO,))


def idem_cases() -> list[CaseSpec]:
    return [_sale_order(), _gl(), _wf(), _auto_plan()]
