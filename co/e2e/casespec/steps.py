"""执行用例的一步：桥调用、改派或回读。"""

from __future__ import annotations

import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.assertutil import long_opinion, status_problem
from co.e2e.casespec.bodies import BUILDERS
from co.e2e.casespec.edit_close_gen_bodies import FLOW_BUILDERS
from co.e2e.casespec.arap_gen_bodies import CORE_BUILDERS
from co.e2e.casespec.returns_misc_bodies import CUSTOM_BUILDERS
from co.e2e.casespec.checks import View, match_checks
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.wiring import EDIT_BUILDERS
from co.e2e.settings import conf

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,20}\Z")


def _raw_id(env: Env, spec: CaseSpec, step: Step) -> str:
    if step.id_from == "made":
        return env.made.get(spec.name, "")
    if step.id_from.startswith("bag:"):
        return env.bag.get(step.id_from[4:], "")
    if spec.needs:
        return env.bag.get(spec.needs[0], "")
    return ""


def _make(name: str):
    if name in BUILDERS:
        return BUILDERS[name]
    if name in FLOW_BUILDERS:
        return FLOW_BUILDERS[name]
    if name in CORE_BUILDERS:
        return CORE_BUILDERS[name]
    if name in CUSTOM_BUILDERS:
        return CUSTOM_BUILDERS[name]
    if name in EDIT_BUILDERS:
        return EDIT_BUILDERS[name]
    raise KeyError(name)


def pin_focus(env: Env, spec: CaseSpec, step: Step) -> None:
    raw = _raw_id(env, spec, step)
    if _ID.fullmatch(raw or ""):
        env.focus = raw


def focus_id(env: Env, spec: CaseSpec, step: Step) -> str:
    raw = _raw_id(env, spec, step)
    if _ID.fullmatch(raw or "") is None:
        raise ValueError("单据 id 不合法")
    env.focus = raw
    return raw


def resolve_token(env: Env, spec: CaseSpec, step: Step, value: str):
    if value == "$id":
        return int(focus_id(env, spec, step))
    if value == "$long":
        return long_opinion()
    if value == "$today":
        return env.today
    if value.isdigit() and value != "0":
        return int(value)
    return value


def auth_fields(env: Env) -> dict:
    return {
        "acc": env.operator.acc,
        "year": env.year or env.operator.year,
        "operator": env.operator.code,
        "password": env.operator.password,
        "date": env.today,
    }


def call_fields(env: Env, spec: CaseSpec, step: Step) -> dict:
    fields = auth_fields(env)
    for key, value in step.fields:
        fields[key] = resolve_token(env, spec, step, value)
    return fields


def invoke(client, route: str, fields: dict) -> tuple[int, dict]:
    """成功回 (200, 响应)；失败回 (状态, {ok, code, message})，桥给了结构化 detail（4xx）时另带 detail。"""
    try:
        return 200, client.call(route, fields)
    except U8CoError as exc:
        payload: dict = {"ok": False, "code": exc.code, "message": exc.message}
        if exc.detail:
            payload["detail"] = exc.detail
        return exc.status, payload


def _remember(env: Env, spec: CaseSpec, payload: dict) -> None:
    raw = payload.get("id")
    if type(raw) is int and raw > 0:
        env.made[spec.name] = str(raw)
        return
    if type(raw) is str and raw.isdigit() and raw != "0":
        env.made[spec.name] = raw


def finish_call(env: Env, spec: CaseSpec, step: Step, status: int, payload: dict) -> tuple[str, str]:
    bad = status_problem(status, payload, step.status, step.code)
    if bad:
        return "FAIL", bad[:240]
    if status != 200:
        return "PASS", step.code
    _remember(env, spec, payload)
    code = payload.get("code")
    if isinstance(code, str) and code:
        env.last_code = code
    view = View(env.focus, env.bag.get("vn_prefix", ""), env.today, env.operator_name)
    return match_checks(payload, step.checks, view)


def step_call(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        pin_focus(env, spec, step)
        fields = call_fields(env, spec, step)
    except ValueError as exc:
        return "FAIL", str(exc)
    status, payload = invoke(env.client, step.route, fields)
    return finish_call(env, spec, step, status, payload)


def step_create(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        extra = _make(step.sql_name)(env)
    except (KeyError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    fields = auth_fields(env)
    fields.update(extra)
    status, payload = invoke(env.client, "/v1/vouchers/create", fields)
    return finish_call(env, spec, step, status, payload)


def step_apply(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    try:
        if step.id_from != "none":
            focus_id(env, spec, step)
        extra = _make(step.sql_name)(env)
    except (KeyError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    fields = auth_fields(env)
    fields.update(extra)
    status, payload = invoke(env.client, step.route, fields)
    return finish_call(env, spec, step, status, payload)


def step_keep(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    raw = _raw_id(env, spec, step)
    if _ID.fullmatch(raw or "") is None or _KEY.fullmatch(step.code) is None:
        return "FAIL", "单据 id 不合法"
    env.bag[step.code] = raw
    env.focus = raw
    return "PASS", raw


def step_call_foreign(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    cfg = conf()
    fields = {"acc": cfg.foreign_probe, "year": cfg.year, "operator": "op001", "password": "invalid", "date": env.today}
    for key, value in step.fields:
        fields[key] = int(value) if value.isdigit() else value
    status, payload = invoke(env.client, step.route, fields)
    return finish_call(env, spec, step, status, payload)
