"""总账取消记账（gl/vouchers/unpost，桥 GlUnpost，第二级写入，只对测试账套开放）。

gl-post-unpost-cycle：先预演一次不带核对字段的取消记账探测（403 test_account_only / feature_disabled 记 N/A，什么都没建）→
  在本年第一个未结账期间新建一张转账凭证（gl_voucher_bodies 的转账分录，同 gl-post）→ 审核 → 读科目总账、辅助总账、
  多辅助总账在本凭证科目上本期到 12 期的校验和（gl_unpost_sql.totals_sql）→ 记账 → 预演取消记账（rollback，
  凭证仍是记账状态）→ 取消记账（period、vouchers 核对为本凭证）200、响应 count=1 → 凭证每行 ibook=0、cbook 为空 →
  三张总账的校验和与记账前一致 → GL_mpostcond 本年度清空 → 再取消记账 409 state_mismatch → 弃审、作废、删除。
  第一个未结账期间不能记账（同 gl-post 的 N/A 条件）时记 N/A。记账之前失败时尽力清理；记账之后、取消记账成功之前
  失败的凭证已记账，删不掉，留给快照还原。

步骤 kind 为 "custom"，sql_name 是操作名（GL_UNPOST_OPS 并入 ops_run）。
"""

from __future__ import annotations

import calendar

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.gl_voucher_bodies import GL_BODIES, GL_DATA
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.gl_post_sql import first_open_sql, master_sign_sql, posted_sql, prior_open_sql
from co.e2e.casespec.gl_unpost_sql import range_sql, totals_sql
from co.e2e.casespec.gl_voucher_sql import GlKey, gl_key
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.casespec.tier2 import tier2_refused

_ROOT = "/v1/gl/vouchers/"
_UNPOST = _ROOT + "unpost"
_ROUTES = tuple(_ROOT + op for op in ("create", "verify", "post", "unpost", "unverify", "void", "delete"))


class _Stop(Exception):
    """用例在某一步结束：status 是 N/A 或 FAIL。"""

    def __init__(self, status: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail[:240]


def _send(env: Env, op: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, _ROOT + op, fields)


def _cell(env: Env, spec: CaseSpec, query: str) -> str:
    return first_cell(run_guarded(env, query, spec.name)).strip()


def _want(status: int, payload: dict, expect: int, what: str) -> str:
    if status == expect:
        return ""
    return f"{what} 应 {expect}，实际 {status} {payload.get('code')} {payload.get('message')}"


def _ident(key: GlKey) -> dict:
    return {"period": key.period, "sign": key.sign, "no": key.no}


def _check(key: GlKey) -> dict:
    return {"period": key.period, "vouchers": [{"sign": key.sign, "no": key.no}]}


def _drop(env: Env, key: GlKey) -> str:
    notes = [f"{op} {_send(env, op, _ident(key))[0]}" for op in ("unverify", "void", "delete")]
    return "清理：" + "，".join(notes)


def _period(env: Env, spec: CaseSpec) -> int:
    text = _cell(env, spec, first_open_sql(env.today[:4]))
    period = int(text) if text.isdigit() else 0
    if period == 0 or period > int(env.today[5:7]):
        raise _Stop("N/A", f"本年第一个未结账期间是 {period}，不在登录月份之前或当月")
    if period == 1 and _cell(env, spec, prior_open_sql(env.today[:4])) != "0":
        raise _Stop("N/A", "本年第一个未结账期间是 1 期，上年度总账未结账，桥不许记账")
    if _cell(env, spec, master_sign_sql()).lower() in ("1", "true"):
        raise _Stop("N/A", "总账选项要求主管签字，桥不做主管签字")
    return period


def _probe(env: Env) -> None:
    # 预演不带核对字段：测试账套上回 200（预演最近一次记账）或 409（没有可恢复的记账），都不写入。
    status, payload = _send(env, "unpost", {"dry_run": True})
    if tier2_refused(status, payload):
        raise _Stop("N/A", "桥没开第二级写入或账套不在 testAccounts 里（403 feature_disabled / test_account_only）")
    if status not in (200, 409):
        raise _Stop("FAIL", "预演取消记账应 200 或 409，" + _want(status, payload, 200, "实际"))


def _create(env: Env, period: int) -> GlKey:
    body = GL_BODIES["create_transfer"](env, "")
    if int(env.today[5:7]) != period:
        last = calendar.monthrange(int(env.today[:4]), period)[1]
        body["head"]["date"] = f"{env.today[:4]}-{period:02d}-{last:02d}"
    status, payload = _send(env, "create", body)
    if status != 200:
        raise _Stop("FAIL", _want(status, payload, 200, "新建凭证"))
    return gl_key(payload.get("period"), payload.get("sign"), payload.get("no"))


def _book(env: Env, spec: CaseSpec, key: GlKey) -> str:
    # 行数|最小 ibook|最小 cbook|最大 cbook，只返回后三段。
    parts = _cell(env, spec, posted_sql(env.today[:4], key)).split("|")
    return "|".join(parts[1:]) if len(parts) == 4 and parts[0] not in ("", "0") else "无凭证行"


def _post(env: Env, spec: CaseSpec, key: GlKey) -> str:
    status, payload = _send(env, "verify", _ident(key))
    if status != 200:
        raise _Stop("FAIL", _want(status, payload, 200, "审核") + "；" + _drop(env, key))
    before = _cell(env, spec, totals_sql(env.today[:4], key))
    status, payload = _send(env, "post", _check(key))
    if status != 200:
        # 记账失败（已回滚，或 504 结果未知）：尽力清理；真记上了的话弃审会被拒，留给快照还原。
        raise _Stop("FAIL", _want(status, payload, 200, "记账") + "；" + _drop(env, key))
    return before


def _dry(env: Env, spec: CaseSpec, key: GlKey) -> None:
    status, payload = _send(env, "unpost", dict(_check(key), dry_run=True))
    if status != 200 or payload.get("dry_run") is not True or payload.get("mode") != "rollback":
        shape = f"dry_run={payload.get('dry_run')} mode={payload.get('mode')}"
        raise _Stop("FAIL", (_want(status, payload, 200, "预演取消记账") or "预演取消记账应为 rollback，") + shape)
    name = env.operator_name.strip()
    book = _book(env, spec, key)
    if book != f"1|{name}|{name}":
        raise _Stop("FAIL", f"预演取消记账之后凭证应仍由本操作员记账，实际 {book}（已记账，留给快照还原）")


def _unpost(env: Env, spec: CaseSpec, key: GlKey) -> None:
    status, payload = _send(env, "unpost", _check(key))
    if status != 200:
        raise _Stop("FAIL", _want(status, payload, 200, "取消记账") + "（已记账，留给快照还原）")
    items = payload.get("vouchers")
    one = items[0] if isinstance(items, list) and len(items) == 1 and isinstance(items[0], dict) else {}
    if payload.get("count") != 1 or one.get("no") != key.no or payload.get("period") != key.period:
        raise _Stop("FAIL", f"取消记账响应不对：{payload}；" + _drop(env, key))


def _verify_after(env: Env, spec: CaseSpec, key: GlKey, before: str) -> str:
    book = _book(env, spec, key)
    if book != "0||":
        return f"取消记账后凭证行应 ibook=0、cbook 为空，实际 {book}"
    after = _cell(env, spec, totals_sql(env.today[:4], key))
    if before == "" or after != before:
        return f"三张总账的校验和与记账前不同：记账前 {before}，取消记账后 {after}"
    left = _cell(env, spec, range_sql(env.today[:4]))
    if left != "0":
        return f"GL_mpostcond 本年度应清空，实际 {left} 行"
    status, payload = _send(env, "unpost", _check(key))
    if status != 409 or payload.get("code") != "state_mismatch":
        return _want(status, payload, 409, "再次取消记账") or f"再次取消记账应 state_mismatch，实际 {payload.get('code')}"
    return ""


def _cleanup(env: Env, key: GlKey) -> str:
    bad = []
    for op in ("unverify", "void", "delete"):
        status = _send(env, op, _ident(key))[0]
        if status != 200:
            bad.append(f"{op} {status}")
    return "清理失败：" + "，".join(bad) if bad else ""


def gl_post_unpost_cycle(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    try:
        period = _period(env, spec)
        _probe(env)
        key = _create(env, period)
        before = _post(env, spec, key)
        _dry(env, spec, key)
        _unpost(env, spec, key)
    except _Stop as stop:
        return stop.status, stop.detail
    problem = _verify_after(env, spec, key, before)
    if problem:
        return "FAIL", (problem + "；" + _drop(env, key))[:240]
    problem = _cleanup(env, key)
    if problem:
        return "FAIL", problem
    return "PASS", f"{key.period}期 {key.sign}-{key.no} 记账、取消记账后三张总账与记账前一致，已删除"


GL_UNPOST_OPS = {"gl_post_unpost_cycle": gl_post_unpost_cycle}


def gl_unpost_cases() -> list[CaseSpec]:
    # 总账写彼此互斥（gl），另新建转账凭证（new:gl:转）；改科目总账，rpt:gl 的报表用例因 gl 避开。
    tags = frozenset(("gl", "new:gl:转"))
    step = Step("custom", sql_name="gl_post_unpost_cycle", id_from="none")
    title = "总账取消记账：新建、审核、记账、预演取消、取消记账（测试账套），三张总账与记账前一致，再取消 409，删除"
    return [CaseSpec("gl-post-unpost-cycle", title, (), (step,), True, data=GL_DATA, tags=tags, routes=_ROUTES)]
