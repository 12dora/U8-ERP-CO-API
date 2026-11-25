"""SQL 回读和登录、审计这几步。"""

from __future__ import annotations

import time

from co.client.u8co_client import U8CoError
from co.e2e.calllog_io import pull_audit
from co.e2e.cases import blocked_without_com, rows_since
from co.e2e.casespec.assertutil import first_cell, judge_cell, judge_verifier
from co.e2e.casespec.env import Env
from co.e2e.casespec.login import check_bad_signature, check_foreign_account, try_login
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.perm_cache import remember
from co.e2e.casespec.wiring import has_edit, edit_sql
from co.e2e.casespec.preds import judge_pred, store_cell
from co.e2e.casespec.arap_gen_readback_sql import has_core, core_sql
from co.e2e.casespec.returns_misc_readback_sql import has_custom, custom_sql
from co.e2e.casespec.flow_read_sql import flow_sql
from co.e2e.casespec.sql_shape import code_sql, named_sql, read_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import focus_id


def _flow_cell(env: Env, spec: CaseSpec, step: Step) -> str:
    doc = focus_id(env, spec, step)
    reader = core_sql if has_core(step.sql_name) else flow_sql
    # 退货等回读（returns_misc_readback_sql）。
    if has_custom(step.sql_name):
        reader = custom_sql
    # 各模块回读（wiring 汇总）。
    if has_edit(step.sql_name):
        reader = edit_sql
    return first_cell(run_guarded(env, reader(step.sql_name, doc, env.bag), spec.name))


def step_stash(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        cell = _flow_cell(env, spec, step)
        store_cell(env.bag, step.code, cell)
    except (RuntimeError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    return "PASS", cell


def step_pred(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        cell = _flow_cell(env, spec, step)
    except (RuntimeError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    return judge_pred(cell, step.code, env.bag, env.operator_name)


def step_sql(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        doc = focus_id(env, spec, step)
        sql = named_sql(step.sql_name, doc, env.operator.code)
        cell = first_cell(run_guarded(env, sql, spec.name))
    except (RuntimeError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    return judge_cell(cell, step.code)


def step_read(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        doc = focus_id(env, spec, step)
        cell = first_cell(run_guarded(env, read_sql(step.sql_name, doc), spec.name))
    except (RuntimeError, ValueError, KeyError) as exc:
        return "FAIL", str(exc)[:200]
    return judge_verifier(cell, step.code, env.operator_name)


def step_readcode(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        doc = focus_id(env, spec, step)
        cell = first_cell(run_guarded(env, code_sql(step.sql_name, doc), spec.name))
    except (RuntimeError, ValueError, KeyError) as exc:
        return "FAIL", str(exc)[:200]
    if cell and cell == env.last_code:
        return "PASS", cell
    return "FAIL", "回读单号不符"


# 桥刚启动时 COM 签名自检在后台跑（signatures 为 pending），最多等这么多秒。
SIG_WAIT_SECONDS = 90
_SIG_POLL = 3


def _health_sigs(env: Env) -> tuple[dict, str]:
    """健康检查响应和 signatures 摘要；pending 时轮询到出结果或超时。"""
    deadline = time.monotonic() + SIG_WAIT_SECONDS
    while True:
        payload = env.client.health()
        sigs = str(payload.get("signatures") or "缺")
        if payload.get("ok") is not True or sigs != "pending" or time.monotonic() >= deadline:
            return payload, sigs
        time.sleep(_SIG_POLL)


def step_health(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    try:
        payload, sigs = _health_sigs(env)
    except U8CoError as exc:
        return "FAIL", f"{exc.status} {exc.code} {exc.message}"[:240]
    if payload.get("ok") is not True:
        return "FAIL", "健康检查未返回 ok"
    remember(env, payload)
    # COM 签名自检必须全部匹配（ok）；mismatch:<n> 的细节见 meta/fields 的 features.signatures。
    if sigs != "ok":
        return "FAIL", f"ok，但 COM 签名自检 signatures={sigs}，应为 ok"
    return "PASS", "ok，signatures ok"


def step_login(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    year, payload, error = try_login(env.client, env.operator, env.today)
    env.year = year
    env.ctx.year_used = year
    if payload is None:
        env.operator_name = ""
        env.ctx.operator_name = ""
        return "FAIL", error[:240]
    env.operator_name = str(payload.get("operator_name") or "")
    env.ctx.operator_name = env.operator_name
    env.bag["login"] = "1"
    return "PASS", f"年度 {year} 姓名 {env.operator_name}"


def step_audit(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    rows = rows_since(pull_audit(env.ctx, env.today.replace("-", "")), env.audit_since)
    ok, detail = blocked_without_com(rows)
    if ok:
        return "PASS", detail
    return "FAIL", detail


def step_badsig(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return check_bad_signature(env.today)


def step_acc_foreign(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return check_foreign_account(env.client, env.today)
