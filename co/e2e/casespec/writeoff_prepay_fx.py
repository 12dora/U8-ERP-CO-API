"""预收 / 预付行的核销、外币核销（arap/writeoff 及其取消）。挑单和回读 SQL 在 writeoff_prepay_fx_sql。

ar-writeoff-prepay：挑一行已审核、有余额（≥ 2）的预收行（Ap_CloseBills.bPrePay=1，造数 ar_prepay）和同一客户下已应收审核、
  该行余额 ≥ 2 的本币销售发票行（造数 arwo_invoice），核销 1.00 → 预收行原币余额少 1、发票行余额少 1、没有汇兑损益 9M 行，
  该核销号的 9P 行里收款单自身的冲减行带 bPrePay=1（同 U8 的「预收冲应收」）；再按核销号取消 → 余额、表头 bPrePay、9M 行数
  都回到核销前，该核销号的 9P 行删光。
ap-writeoff-prepay：付款单预付行（造数 ap_prepay）对已应付审核的采购发票行（造数 apwo_invoice），同上。
ar-writeoff-fx：挑一行已审核的外币收款单行（造数 fx_ar_receipt）和同一客户、同一币种、同一汇率的已审核应收单
  （造数 fx_ar_bill，整单核销），核销原币 1.00 → 两边原币余额各少 1、没有 9M 行，9P 行全是外币行、对方单据行原币 1、
  本币 = round(汇率, 2)（≠ 原币）；再取消 → 复原。桥还回 400「外币核销暂不支持」时记 N/A（带原文）。
ap-writeoff-fx：外币付款单（fx_ap_payment）对外币应付单（fx_ap_bill），同上。
挑不到单据记 N/A。核销成功之后任何一步失败，都尽力按核销号取消（writeoff.cancel_one，404 视为已取消），
补偿也失败时留给快照还原。步骤 kind 为 "custom"，sql_name 是操作名（ARAP_MORE_OPS 并入 ops_run）。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.writeoff import cancel_one
from co.e2e.casespec.writeoff_run import digits_of
from co.e2e.casespec.writeoff_prepay_fx_sql import balance_sql, batch_sql, fx_pick_sql, prepay_pick_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

ROUTE = "/v1/arap/writeoff"
CANCEL = "/v1/arap/writeoff/cancel"
FX_REFUSED = "外币核销暂不支持"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_INT_MAX = 2147483647
_ONE = Decimal("1")
_CENT = Decimal("0.01")
# 侧 → (收付款单类型, 发票类型, 应收应付单类型, 收付款单名, 往来单位名, 预收付名)。
SIDE_TEXT = {
    "AR": ("ar_receipt", "sale_invoice", "ar_bill", "收款单", "客户", "预收"),
    "AP": ("ap_payment", "purchase_invoice", "ap_bill", "付款单", "供应商", "预付"),
}


@dataclass(frozen=True)
class Plan:
    side: str
    body: dict
    # 余额回读的对象：("invoice", 发票行) 或 ("bill", 应收应付单主键)。
    target: tuple[str, int]
    line: int
    # 核销之后对本核销号 9P 行的核对（batch_sql 的五项），返回问题或空串。
    check: Callable[[list[Decimal]], str]


# ---- 小工具 ----


def _send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, route, fields)


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _cells(env: Env, spec: CaseSpec, query: str, width: int) -> list[str] | None:
    cell = first_cell(run_guarded(env, query, spec.name))
    parts = [part.strip() for part in cell.split("|")] if cell else []
    return parts if len(parts) == width and all(parts) else None


def _ids(parts: list[str]) -> list[int]:
    if not all(_ID.fullmatch(part) and int(part) <= _INT_MAX for part in parts):
        raise ValueError(f"挑单返回的主键不对：{parts}")
    return [int(part) for part in parts]


def _nums(parts: list[str] | None, what: str) -> list[Decimal]:
    if parts is None:
        raise ValueError(what + "回读不到")
    try:
        return [Decimal(part) for part in parts]
    except InvalidOperation as exc:
        raise ValueError(f"{what}回读不是数：{parts}") from exc


def _balance(env: Env, spec: CaseSpec, plan: Plan) -> list[Decimal]:
    # 收付款单行原币余额、本币余额、表头 bPrePay、被核销单据原币余额、9M 行数。
    return _nums(_cells(env, spec, balance_sql(plan.side, plan.line, plan.target), 5), "余额")


def _batch(env: Env, spec: CaseSpec, side: str, digits: str) -> list[Decimal]:
    return _nums(_cells(env, spec, batch_sql(side, digits), 5), "核销行")


# ---- 核对 ----


def _moved(before: list[Decimal], after: list[Decimal]) -> str:
    want = [before[0] - _ONE, before[3] - _ONE, before[4]]
    got = [after[0], after[3], after[4]]
    if got != want:
        return f"核销后（行原币余额、单据原币余额、9M 行数）应为 {want}，实际 {got}"
    return ""


def _restored(before: list[Decimal], after: list[Decimal]) -> str:
    if after != before:
        return f"取消后（行原币余额、本币余额、表头 bPrePay、单据原币余额、9M 行数）应回到 {before}，实际 {after}"
    return ""


def prepay_check(batch: list[Decimal]) -> str:
    rows, prepay, fx, orig = batch[0], batch[1], batch[2], batch[3]
    if rows < 2 or prepay < 1 or fx != 0 or orig != _ONE:
        return f"9P 行（行数、自身 bPrePay=1 行数、外币行数、对方原币合计）应为 ≥2、≥1、0、1，实际 {batch[:4]}"
    return ""


def fx_checker(rate: Decimal) -> Callable[[list[Decimal]], str]:
    local = rate.quantize(_CENT, rounding=ROUND_HALF_UP)

    def check(batch: list[Decimal]) -> str:
        rows, fx, orig, got = batch[0], batch[2], batch[3], batch[4]
        if rows < 2 or fx != rows or orig != _ONE or got != local or got == orig:
            return f"9P 行（行数、外币行数、对方原币、对方本币）应为 ≥2、全部、1、{local}，实际 {[rows, fx, orig, got]}"
        return ""

    return check


# ---- 一整轮 ----


def _after_write(env: Env, spec: CaseSpec, plan: Plan, before: list[Decimal], digits: str) -> str:
    problem = _moved(before, _balance(env, spec, plan)) or plan.check(_batch(env, spec, plan.side, digits))
    if problem:
        return problem
    status, payload = _send(env, CANCEL, {"flag": plan.side, "cancel_no": "HX" + plan.side + digits})
    if status != 200:
        return "取消核销应 200，实际 " + _brief(status, payload)
    if _batch(env, spec, plan.side, digits)[0] != 0:
        return "取消核销后该核销号仍有 9P 行"
    return _restored(before, _balance(env, spec, plan)) or ""


def run_plan(env: Env, spec: CaseSpec, plan: Plan, refused: Callable[[int, dict], str] | None = None) -> tuple[str, str]:
    before = _balance(env, spec, plan)
    status, payload = _send(env, ROUTE, plan.body)
    if status != 200:
        na = refused(status, payload) if refused is not None else ""
        return ("N/A", na) if na else ("FAIL", "核销应 200，实际 " + _brief(status, payload))
    digits = digits_of(payload.get("cancel_no"), plan.side)
    if not digits:
        return "FAIL", f"核销响应的 cancel_no 不是 HX{plan.side} 开头：{payload.get('cancel_no')!r}，留给快照还原"
    try:
        problem = _after_write(env, spec, plan, before, digits)
    except (RuntimeError, ValueError) as exc:
        problem = str(exc)[:160]
    if problem:
        _gone, note = cancel_one(env, plan.side, digits)
        return "FAIL", f"HX{plan.side}{digits}：{problem}；{note}"[:300]
    return "PASS", f"HX{plan.side}{digits} 核销 1.00 后余额、核销行都对，取消后复原"


def _ask(side: str, ids: list[int], target_kind: str) -> dict:
    receipt, doc = SIDE_TEXT[side][0], SIDE_TEXT[side][1 if target_kind == "invoice" else 2]
    item: dict = {"type": doc, "id": ids[2], "amount": 1}
    if target_kind == "invoice":
        item["line_id"] = ids[3]
    return {"receipt": {"type": receipt, "id": ids[0], "line_id": ids[1]}, "items": [item]}


def prepay_run(env: Env, spec: CaseSpec, side: str) -> tuple[str, str]:
    _r, _i, _b, name, party, pre = SIDE_TEXT[side]
    parts = _cells(env, spec, prepay_pick_sql(side), 4)
    if parts is None:
        return "N/A", f"没有已审核、余额 ≥ 2 的{pre}行和同一{party}已审核、余额 ≥ 2 的本币发票行（造数 {side.lower()}_prepay）"
    ids = _ids(parts)
    plan = Plan(side, _ask(side, ids, "invoice"), ("invoice", ids[3]), ids[1], prepay_check)
    status, detail = run_plan(env, spec, plan)
    return status, (f"{pre}{name}行 {ids[1]}：" + detail)[:300]


def _fx_refused(status: int, payload: dict) -> str:
    if status == 400 and FX_REFUSED in str(payload.get("message")):
        return f"桥还不支持外币核销：400 {payload.get('message')}"[:200]
    return ""


def fx_run(env: Env, spec: CaseSpec, side: str) -> tuple[str, str]:
    _r, _i, _b, name, party, _pre = SIDE_TEXT[side]
    parts = _cells(env, spec, fx_pick_sql(side), 4)
    if parts is None:
        return "N/A", f"没有已审核的外币{name}行和同一{party}、同币种同汇率、已审核的外币单据（造数 fx_{side.lower()}_*）"
    ids = _ids(parts[:3])
    rate = _nums(parts[3:], "汇率")[0]
    plan = Plan(side, _ask(side, ids, "bill"), ("bill", ids[2]), ids[1], fx_checker(rate))
    status, detail = run_plan(env, spec, plan, _fx_refused)
    return status, (f"外币{name}行 {ids[1]}（汇率 {rate}）：" + detail)[:300]


def _op(run: Callable[[Env, CaseSpec, str], tuple[str, str]], side: str):
    def op(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
        del step
        return run(env, spec, side)

    return op


ARAP_MORE_OPS = {
    "hxm_prepay_ar": _op(prepay_run, "AR"),
    "hxm_prepay_ap": _op(prepay_run, "AP"),
    "hxm_fx_ar": _op(fx_run, "AR"),
    "hxm_fx_ap": _op(fx_run, "AP"),
}

_CASES = (
    ("ar-writeoff-prepay", "hxm_prepay_ar", "AR", ("w:ar_receipt", "w:sale_invoice"),
     "应收核销预收行：预收行对销售发票行核销 1.00，余额各少 1、收款单自身冲减行 bPrePay=1；取消后复原"),
    ("ap-writeoff-prepay", "hxm_prepay_ap", "AP", ("w:ap_payment", "w:purchase_invoice"),
     "应付核销预付行：预付行对采购发票行核销 1.00，余额各少 1、付款单自身冲减行 bPrePay=1；取消后复原"),
    ("ar-writeoff-fx", "hxm_fx_ar", "AR", ("w:ar_receipt", "w:ar_bill", "chain:sale"),
     "外币应收核销：外币收款单行对同币种同汇率的应收单核销原币 1.00，本币按汇率、没有汇兑损益；取消后复原（桥未支持时 N/A）"),
    ("ap-writeoff-fx", "hxm_fx_ap", "AP", ("w:ap_payment", "w:ap_bill", "chain:pu"),
     "外币应付核销：外币付款单行对同币种同汇率的应付单核销原币 1.00，本币按汇率、没有汇兑损益；取消后复原（桥未支持时 N/A）"),
)


def arap_more_cases() -> list[CaseSpec]:
    # 同一侧的核销、取消核销在桥里串行（arap:writeoff:<侧>）；标签与 ar-writeoff / ap-writeoff 相交，调度上也不同时跑。
    return [
        CaseSpec(
            name,
            title,
            (),
            (Step("custom", sql_name=op, id_from="none"),),
            True,
            optional=True,
            tags=frozenset(tags),
            routes=(ROUTE, CANCEL),
        )
        for name, op, _side, tags, title in _CASES
    ]
