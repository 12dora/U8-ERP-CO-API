"""核销记录查询（reports/arap_writeoffs）在核销用例里的两步（步骤 kind 为 "custom"，HX_LIST_OPS 并入 ops_run；
步骤由 writeoff.list_steps 生成。本模块引用 steps，不能被 writeoff 引用（steps → wiring → writeoff）。

hx_listed：核销之后按新核销号查，恰好列出一批，cancellable 为 true，合计 1.00，收付款单是挑中的那一行，
  被核销单据恰好一行、是挑中的发票行、金额 1.00。
hx_unlisted：取消核销之后按同一核销号查，一批也不列。
fields 的 side 是 AR 或 AP；核销号数字取自 bag（hx_cno / hx_pno，由核销一步 hx_exec 从响应记下）。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.writeoff_run import HX_RUN_OPS
from co.e2e.casespec.steps import auth_fields, invoke

REPORT = "/v1/reports/arap_writeoffs"
_ONE = Decimal("1")
# side → 核销号数字键、收付款单键、收付款单行键、发票键、发票行键、发票类型、收付款单类型。
_KEYS = {
    "AR": ("hx_cno", "hx_rc", "hx_rcl", "hx_inv", "hx_invl", "sale_invoice", "ar_receipt"),
    "AP": ("hx_pno", "hx_pc", "hx_pcl", "hx_pinv", "hx_pinvl", "purchase_invoice", "ap_payment"),
}


def _side(step: Step) -> str:
    side = dict(step.fields).get("side", "AR")
    if side not in _KEYS:
        raise ValueError("side 只能是 AR 或 AP")
    return side


def _query(env: Env, side: str) -> tuple[str, list[dict]]:
    digits = env.bag.get(_KEYS[side][0], "")
    if not digits.isdigit():
        raise ValueError("缺核销号")
    cancel_no = "HX" + side + digits
    fields = auth_fields(env)
    fields.update({"flag": side, "cancel_no": cancel_no})
    status, payload = invoke(env.client, REPORT, fields)
    if status != 200:
        raise ValueError(f"{REPORT} {status} {payload.get('code')} {payload.get('message')}"[:200])
    items = payload.get("items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("items 不是对象列表")
    return cancel_no, items


def _money(value: object) -> Decimal | None:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _same(row: dict, want: dict[str, object]) -> bool:
    return all(str(row.get(key)) == str(value) for key, value in want.items())


def _receipt_problem(env: Env, side: str, item: dict) -> str:
    keys = _KEYS[side]
    receipt = item.get("receipt")
    want = {"type": keys[6], "id": env.bag.get(keys[1], ""), "line_id": env.bag.get(keys[2], "")}
    if not isinstance(receipt, dict) or not _same(receipt, want):
        return f"receipt 不是挑中的收付款单行：{receipt}"[:240]
    return ""


def _target_problem(env: Env, side: str, item: dict) -> str:
    keys = _KEYS[side]
    targets = item.get("targets")
    if not isinstance(targets, list) or len(targets) != 1 or not isinstance(targets[0], dict):
        return f"targets 应恰好一行：{targets}"[:240]
    want = {"type": keys[5], "id": env.bag.get(keys[3], ""), "line_id": env.bag.get(keys[4], "")}
    if not _same(targets[0], want) or _money(targets[0].get("amount")) != _ONE:
        return f"targets 不是挑中的发票行或金额不是 1：{targets[0]}"[:240]
    return ""


def _batch_problem(env: Env, side: str, cancel_no: str, item: dict) -> str:
    if item.get("cancel_no") != cancel_no:
        return f"cancel_no 回显 {item.get('cancel_no')}"
    if item.get("cancellable") is not True:
        return f"刚做的核销应可取消：cancellable={item.get('cancellable')} reason={item.get('reason')}"[:240]
    if _money(item.get("amount")) != _ONE or item.get("gl_voucher") is not False:
        return f"amount={item.get('amount')} gl_voucher={item.get('gl_voucher')}"
    return _receipt_problem(env, side, item) or _target_problem(env, side, item)


def listed(env: Env, _spec: CaseSpec, step: Step) -> tuple[str, str]:
    side = _side(step)
    cancel_no, items = _query(env, side)
    if len(items) != 1:
        return "FAIL", f"核销号 {cancel_no} 列出 {len(items)} 批，应为 1"
    problem = _batch_problem(env, side, cancel_no, items[0])
    if problem:
        return "FAIL", problem
    return "PASS", cancel_no + " 可取消"


def unlisted(env: Env, _spec: CaseSpec, step: Step) -> tuple[str, str]:
    cancel_no, items = _query(env, _side(step))
    if items:
        return "FAIL", f"取消后核销号 {cancel_no} 仍列出 {len(items)} 批"
    return "PASS", cancel_no + " 已不在"


# 核销一步（hx_exec，writeoff_run）也经这里接进 ops_run。
HX_LIST_OPS = {"hx_listed": listed, "hx_unlisted": unlisted, **HX_RUN_OPS}
