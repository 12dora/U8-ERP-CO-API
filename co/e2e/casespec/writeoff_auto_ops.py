"""自动核销用例里的试算一步（步骤 kind 为 "custom"，HXA_OPS 并入 ops_run；执行一步 hxa_exec 在 writeoff_run）。本模块引用 steps，不能被 wiring 链引用。

hxa_dry：限定挑中的收款单行和发票做 dry_run，计划非空；首批是挑中的收款单行，每笔落在挑中的发票上、金额大于 0 且不超过
  分配前余额，各批金额之和等于 total，total 不超过收款单行余额；第一笔是挑中的发票行，金额不超过它的余额（核销前记下的 hxa_i0）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.writeoff_auto import ROUTE, ask_body
from co.e2e.casespec.writeoff_run import HXA_RUN_OPS
from co.e2e.casespec.steps import auth_fields, invoke


def _money(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _plan(env: Env) -> tuple[list[dict], Decimal | None]:
    fields = auth_fields(env)
    fields.update(ask_body(env, True))
    status, payload = invoke(env.client, ROUTE, fields)
    if status != 200:
        raise ValueError(f"{ROUTE} {status} {payload.get('code')} {payload.get('message')}"[:200])
    plan = payload.get("plan")
    if payload.get("dry_run") is not True or not isinstance(plan, list) or not plan:
        raise ValueError(f"试算应返回非空计划：dry_run={payload.get('dry_run')} plan={plan}"[:200])
    if not all(isinstance(batch, dict) for batch in plan):
        raise ValueError("plan 不是对象列表")
    return plan, _money(payload.get("total"))


def _piece_problem(env: Env, piece: object) -> str:
    if not isinstance(piece, dict):
        return "targets 不是对象列表"
    amount, balance = _money(piece.get("amount")), _money(piece.get("balance"))
    if amount is None or balance is None or amount <= 0 or amount > balance:
        return f"金额不在 (0, 余额] 内：{piece}"[:200]
    if piece.get("type") != "sale_invoice" or str(piece.get("id")) != env.bag.get("hxa_inv"):
        return f"不是挑中的发票：{piece}"[:200]
    return ""


def _batch_problem(env: Env, batch: dict) -> tuple[str, Decimal]:
    targets = batch.get("targets")
    if not isinstance(targets, list) or not targets:
        return "批里没有单据行", Decimal(0)
    for piece in targets:
        problem = _piece_problem(env, piece)
        if problem:
            return problem, Decimal(0)
    amount = _money(batch.get("amount"))
    pieces = sum((_money(piece.get("amount")) or Decimal(0) for piece in targets), Decimal(0))
    if amount is None or amount != pieces:
        return f"批金额 {batch.get('amount')} 与各笔之和 {pieces} 不等", Decimal(0)
    return "", amount


def _first_problem(env: Env, first: dict) -> str:
    receipt = first.get("receipt")
    want = (env.bag.get("hxa_rc"), env.bag.get("hxa_rcl"))
    if not isinstance(receipt, dict) or (str(receipt.get("id")), str(receipt.get("line_id"))) != want:
        return f"首批不是挑中的收款单行：{receipt}"[:200]
    piece = first["targets"][0]
    before = _money(env.bag.get("hxa_i0", ""))
    if str(piece.get("line_id")) != env.bag.get("hxa_invl") or before is None or _money(piece.get("amount")) > before:
        return f"第一笔不是挑中的发票行或超过其余额：{piece}"[:200]
    return ""


def dry(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    plan, total = _plan(env)
    summed = Decimal(0)
    for batch in plan:
        problem, amount = _batch_problem(env, batch)
        if problem:
            return "FAIL", problem
        summed += amount
    remain = _money(env.bag.get("hxa_r0", ""))
    if total is None or total != summed or remain is None or total > remain:
        return "FAIL", f"total={total} 各批之和={summed} 收款单行余额={remain}"
    problem = _first_problem(env, plan[0])
    if problem:
        return "FAIL", problem
    return "PASS", f"计划 {len(plan)} 批，合计 {total}"


# 执行一步（hxa_exec，writeoff_run）也经这里接进 ops_run。
HXA_OPS = {"hxa_dry": dry, **HXA_RUN_OPS}
