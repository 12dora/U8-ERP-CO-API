"""总账记账（gl/vouchers/post，桥 GlPost 调 U8 自己的总账 .NET 组件）。

gl-post：在本年（登录日期的年份）第一个未结账期间新建一张转账凭证（gl_voucher_bodies 的转账分录，日期取登录日，
  登录月份晚于该期间时取该期间的 28 日）→ 审核 → 记账 → 响应 posted、poster 是本操作员 → 回读每行 ibook=1、
  cbook=本操作员姓名 → 本凭证之外全部 GL_accvouch 行（含别的年度期间、类别、凭证号相同的凭证）的记账标志校验和
  与记账前一致（U8 回写记账标志的 UPDATE 不带年度，桥在同一事务里改回）→ 响应 local_txn 为 true（记账事务没有升级
  MSDTC）→ 再记账 409 state_mismatch。
  没有取消记账接口：凭证和科目总账的发生额留给快照还原（co/e2e/README.md「已知的还原差异」）。审核失败、记账调用本身失败
  （非 200）时尽力弃审、作废、删除本用例新建的凭证；记账成功之后的核对失败不清理（已记账，删不掉）。
  第一个未结账期间在登录月份之后、第一个未结账期间是 1 期而上年度总账未结账（桥拒绝）、或总账选项要求主管签字时记 N/A。
gl-post-closed-period：记已结账的期间（第一个未结账期间的前一期）409 state_mismatch，不动数据；本年 1 月未结账时记 N/A。
gl-post-bad-body：vouchers 为空，登录前 400。

步骤 kind 为 "custom"，sql_name 是操作名（GLPOST_OPS 并入 ops_run）。
"""

from __future__ import annotations

import calendar

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.gl_voucher_bodies import GL_BODIES, GL_DATA
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, call
from co.e2e.casespec.gl_post_sql import first_open_sql, master_sign_sql, others_sql, posted_sql, prior_open_sql
from co.e2e.casespec.gl_voucher_sql import GlKey, gl_key
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.testdata import SIGN_TRANSFER, gl_sign

_ROOT = "/v1/gl/vouchers/"
_POST = _ROOT + "post"
_ROUTES = tuple(_ROOT + op for op in ("create", "verify", "post", "unverify", "void", "delete"))


def _send(env: Env, op: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, _ROOT + op, fields)


def _cell(env: Env, spec: CaseSpec, query: str) -> str:
    return first_cell(run_guarded(env, query, spec.name)).strip()


def _want(status: int, payload: dict, expect: int, what: str) -> str:
    if status == expect:
        return ""
    return f"{what} 应 {expect}，实际 {status} {payload.get('code')} {payload.get('message')}"[:240]


def _first_open(env: Env, spec: CaseSpec) -> int:
    text = _cell(env, spec, first_open_sql(env.today[:4]))
    return int(text) if text.isdigit() else 0


def _post_body(key: GlKey) -> dict:
    return {"period": key.period, "vouchers": [{"sign": key.sign, "no": key.no}]}


def _create(env: Env, period: int) -> tuple[GlKey | None, str]:
    body = GL_BODIES["create_transfer"](env, "")
    if int(env.today[5:7]) != period:
        # 期间最后一天：账套开了「制单序时」时新凭证日期不能早于本期同类别前一张凭证（实测 409）。
        last = calendar.monthrange(int(env.today[:4]), period)[1]
        body["head"]["date"] = f"{env.today[:4]}-{period:02d}-{last:02d}"
    status, payload = _send(env, "create", body)
    bad = _want(status, payload, 200, "新建凭证")
    if bad:
        return None, bad
    return gl_key(payload.get("period"), payload.get("sign"), payload.get("no")), ""


def _drop(env: Env, key: GlKey) -> str:
    # 记账之前失败时尽力清理；已审核时先弃审。
    ident = {"period": key.period, "sign": key.sign, "no": key.no}
    notes = [f"{op} {_send(env, op, ident)[0]}" for op in ("unverify", "void", "delete")]
    return "清理：" + "，".join(notes)


def _posted_problem(env: Env, payload: dict, key: GlKey) -> str:
    items = payload.get("posted")
    one = items[0] if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) else {}
    state = one.get("state") if isinstance(one.get("state"), dict) else {}
    if one.get("no") != key.no or state.get("posted") is not True:
        return f"记账响应不对：{payload}"[:240]
    if payload.get("local_txn") is not True:
        return f"记账响应 local_txn 应为 true（没有升级 MSDTC），实际 {payload.get('local_txn')}"
    if str(state.get("poster", "")).strip() != env.operator_name.strip():
        return f"记账人应为本操作员，实际 {state.get('poster')}"
    return ""


def _rows_problem(env: Env, spec: CaseSpec, key: GlKey) -> str:
    parts = _cell(env, spec, posted_sql(env.today[:4], key)).split("|")
    name = env.operator_name.strip()
    if len(parts) != 4 or parts[0] in ("", "0") or parts[1] != "1" or parts[2].strip() != name or parts[3].strip() != name:
        return f"回读凭证行不是本操作员记账：{'|'.join(parts)}"
    return ""


def _post_and_check(env: Env, spec: CaseSpec, key: GlKey) -> str:
    before = _cell(env, spec, others_sql(env.today[:4], key))
    status, payload = _send(env, "post", _post_body(key))
    bad = _want(status, payload, 200, "记账")
    if bad:
        # 记账调用失败（桥已回滚，或 504 结果未知）：尽力清理；真记上了的话弃审会被拒，留给快照还原。
        return (bad + "；" + _drop(env, key))[:240]
    bad = _posted_problem(env, payload, key) or _rows_problem(env, spec, key)
    if bad:
        return bad
    after = _cell(env, spec, others_sql(env.today[:4], key))
    if before == "" or after != before:
        return f"其他凭证的记账标志变了：记账前 {before}，记账后 {after}"
    status, payload = _send(env, "post", _post_body(key))
    if status != 409 or payload.get("code") != "state_mismatch":
        return _want(status, payload, 409, "再次记账") or f"再次记账应 state_mismatch，实际 {payload.get('code')}"
    return ""


def gl_post(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    period = _first_open(env, spec)
    if period == 0 or period > int(env.today[5:7]):
        return "N/A", f"本年第一个未结账期间是 {period}，不在登录月份之前或当月"
    if period == 1 and _cell(env, spec, prior_open_sql(env.today[:4])) != "0":
        return "N/A", "本年第一个未结账期间是 1 期，上年度总账未结账，桥不许记账"
    if _cell(env, spec, master_sign_sql()).lower() in ("1", "true"):
        return "N/A", "总账选项要求主管签字，桥不做主管签字"
    key, bad = _create(env, period)
    if key is None:
        return "FAIL", bad
    ident = {"period": key.period, "sign": key.sign, "no": key.no}
    status, payload = _send(env, "verify", ident)
    bad = _want(status, payload, 200, "审核")
    if bad:
        return "FAIL", (bad + "；" + _drop(env, key))[:240]
    problem = _post_and_check(env, spec, key)
    if problem:
        return "FAIL", problem
    return "PASS", f"{key.period}期 {key.sign}-{key.no} 已记账（留给快照还原）"


def gl_post_closed(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    period = _first_open(env, spec)
    if period <= 1:
        return "N/A", "本年没有已结账的期间"
    voucher = {"sign": gl_sign(SIGN_TRANSFER), "no": 1}
    status, payload = _send(env, "post", {"period": period - 1, "vouchers": [voucher]})
    if status != 409 or payload.get("code") != "state_mismatch":
        return "FAIL", _want(status, payload, 409, "记已结账期间") or f"应 state_mismatch，实际 {payload.get('code')}"
    return "PASS", f"{period - 1} 期已结账：409"


GLPOST_OPS = {"gl_post": gl_post, "gl_post_closed": gl_post_closed}


def glpost_cases() -> list[CaseSpec]:
    # 总账写彼此互斥（gl）；gl-post 另新建转账凭证（new:gl:转）。记账改科目总账，rpt:gl 的报表用例因 gl 避开。
    tags = frozenset(("gl",))
    posted = Step("custom", sql_name="gl_post", id_from="none")
    closed = Step("custom", sql_name="gl_post_closed", id_from="none")
    bad = call(_POST, (("period", "9"),), (), 400, "bad_request")
    title = "总账记账：新建、审核、记账，记账人是本操作员，其他凭证的记账标志不变，再记账 409（不清理，快照还原）"
    return [
        CaseSpec("gl-post", title, (), (posted,), True, data=GL_DATA, tags=tags | {"new:gl:转"}, routes=_ROUTES),
        CaseSpec("gl-post-closed-period", "记已结账的期间：409", (), (closed,), True, data=(SIGN_TRANSFER,), tags=tags,
                 routes=(_POST,)),
        CaseSpec("gl-post-bad-body", "记账不带凭证：登录前 400", (), (bad,), tags=frozenset(("probe",)), routes=(_POST,)),
    ]
