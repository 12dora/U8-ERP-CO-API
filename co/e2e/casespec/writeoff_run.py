"""核销（arap/writeoff）和自动核销（arap/writeoff/auto）执行的一步（步骤 kind 为 "custom"）。

先把响应里的核销号记进 bag，再核对响应：核销成功而响应核对失败（例如自动核销多出一批）时，补偿也能按记下的号取消；
核销号只取本次响应，不再按单据行上「最新的核销行」去猜（那可能是别人刚做的一批）。
hx_exec：fields 的 side 是 AR 或 AP，正文同 hx_ar_one / hx_ap_one，核销号数字记进 hx_cno / hx_pno。
hxa_exec：正文同 hxa_run，首批的核销号数字记进 hxa_cno，其余各批记进 hxa_more（逗号分隔）。
HX_RUN_OPS 并入 writeoff_list.HX_LIST_OPS，HXA_RUN_OPS 并入 writeoff_auto_ops.HXA_OPS（都经 ops_run 接线）。
本模块引用 steps，不能被 writeoff / writeoff_auto 引用（steps → wiring → writeoff）。
"""

from __future__ import annotations

import re

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.writeoff import MARKS, ROUTE, ap_one_body, ar_one_body
from co.e2e.casespec.writeoff_auto import ROUTE as AUTO_ROUTE
from co.e2e.casespec.writeoff_auto import run_body
from co.e2e.casespec.steps import auth_fields, finish_call, invoke

_DIGITS = re.compile(r"^[0-9]{1,20}\Z")
_BODIES = {"AR": ar_one_body, "AP": ap_one_body}


def digits_of(value: object, side: str) -> str:
    """核销号 HXAR… / HXAP… 去掉前缀后的数字；不是本方的核销号返回空串。"""
    prefix = "HX" + side
    if not isinstance(value, str) or not value.startswith(prefix):
        return ""
    rest = value[len(prefix) :]
    return rest if _DIGITS.fullmatch(rest) else ""


def _call(env: Env, spec: CaseSpec, step: Step, how: tuple) -> tuple[str, str]:
    # how：（路由、正文函数、记核销号的函数）。
    route, body, record = how
    try:
        extra = body(env)
    except ValueError as exc:
        return "FAIL", str(exc)[:200]
    fields = auth_fields(env)
    fields.update(extra)
    status, payload = invoke(env.client, route, fields)
    if status == 200 and isinstance(payload, dict):
        record(env, payload)
    return finish_call(env, spec, step, status, payload)


def hx_run(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    side = dict(step.fields).get("side", "")
    if side not in _BODIES:
        return "FAIL", "side 只能是 AR 或 AP"

    def record(bag_env: Env, payload: dict) -> None:
        digits = digits_of(payload.get("cancel_no"), side)
        if digits:
            bag_env.bag[MARKS[side][0]] = digits

    return _call(env, spec, step, (ROUTE, _BODIES[side], record))


def _record_batches(env: Env, payload: dict) -> None:
    batches = payload.get("batches")
    if not isinstance(batches, list):
        return
    found = [digits_of(batch.get("cancel_no"), "AR") for batch in batches if isinstance(batch, dict)]
    found = [digits for digits in found if digits]
    if found:
        env.bag["hxa_cno"] = found[0]
    if len(found) > 1:
        env.bag["hxa_more"] = ",".join(found[1:])


def hxa_run(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    return _call(env, spec, step, (AUTO_ROUTE, run_body, _record_batches))


HX_RUN_OPS = {"hx_exec": hx_run}
HXA_RUN_OPS = {"hxa_exec": hxa_run}
