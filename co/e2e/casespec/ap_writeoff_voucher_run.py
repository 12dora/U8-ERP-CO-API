"""写预演自定义步骤（Step.kind 为 "custom"）：真核销 / 真自动核销、真制单，先把响应里的号记进本用例自己的 bag 键再核对响应。
DRY_AP_OPS 并入 ops_run。

dry_wo_exec：fields 的 builder 给正文、side 是 AR / AP、key 是记核销号数字的 bag 键。响应的 cancel_no（手工核销）或
  batches[].cancel_no（自动核销）去掉 HXAR / HXAP 前缀记进 <key>，多出的批记进 <key>_more（逗号分隔）。
dry_pz_make：fields 的 builder 给制单正文、side、key：响应的 pz_id 去掉 AR / AP 前缀记进 <key>。
之后任何一步失败，dry_cleanup 按记下的号取消核销、取消制单。本模块引用 steps，不能被 wiring 链引用。
"""

from __future__ import annotations

import re

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.writeoff_run import digits_of
from co.e2e.casespec.steps import _make, auth_fields, finish_call, invoke

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,20}\Z")
_DIGITS = re.compile(r"^[0-9]{1,20}\Z")


def _args(step: Step) -> tuple[str, str, str]:
    args = dict(step.fields)
    side, key = args.get("side", ""), args.get("key", "")
    if side not in ("AR", "AP") or _KEY.fullmatch(key) is None:
        raise ValueError("side / key 不合法")
    return args.get("builder", ""), side, key


def _call(env: Env, step: Step, builder: str) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(_make(builder)(env))
    return invoke(env.client, step.route, fields)


def cancel_digits(payload: dict, side: str) -> list[str]:
    """响应里本方的核销号数字：手工核销的 cancel_no，或自动核销各批的 cancel_no。"""
    found = [digits_of(payload.get("cancel_no"), side)]
    batches = payload.get("batches")
    if isinstance(batches, list):
        found.extend(digits_of(batch.get("cancel_no"), side) for batch in batches if isinstance(batch, dict))
    return [digits for digits in found if digits]


def wo_exec(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    builder, side, key = _args(step)
    status, payload = _call(env, step, builder)
    found = cancel_digits(payload, side) if status == 200 and isinstance(payload, dict) else []
    if found:
        env.bag[key] = found[0]
    if len(found) > 1:
        env.bag[key + "_more"] = ",".join(found[1:])
    return finish_call(env, spec, step, status, payload)


def pz_digits(value: object, side: str) -> str:
    if not isinstance(value, str) or not value.startswith(side):
        return ""
    rest = value[len(side) :]
    return rest if _DIGITS.fullmatch(rest) else ""


def pz_make(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    builder, side, key = _args(step)
    status, payload = _call(env, step, builder)
    digits = pz_digits(payload.get("pz_id"), side) if status == 200 and isinstance(payload, dict) else ""
    if digits:
        env.bag[key] = digits
    return finish_call(env, spec, step, status, payload)


DRY_AP_OPS = {"dry_wo_exec": wo_exec, "dry_pz_make": pz_make}
