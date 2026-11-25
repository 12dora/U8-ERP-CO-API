"""应收自动核销（arap/writeoff/auto）。

ar-writeoff-auto：挑一个客户、它的一行有余额的本币收款单和一张本币销售发票（行号最小的有余额行），限定这张收款单的这一行和
  这张发票：先 dry_run（计划非空、落在挑中的行上、每笔不超过余额，余额不变；步骤在 writeoff_auto_ops），
  再 max_amount 1.00 执行 → 恰好一批，收款单行和发票行余额各少 1、多一条核销行；用这一批的核销号取消核销 → 余额和核销行复原
  （用例自己清理）。执行一步（hxa_exec，writeoff_run）先把响应里各批的核销号记下再核对响应；之后任何一步失败
  （含取消核销 409、批数不对）时，HXA_CLEANUP 尽力按记下的核销号逐批取消（404 视为已取消）；补偿也失败时留给快照还原。
ar-writeoff-auto-bad：缺 partner、flag 与 targets 的类型不一致，都是登录前 400。

挑单 SQL 在 writeoff_auto_sql；回读沿用 writeoff_sql（hx_remain / hx_arbal / hx_arrows）。
"""

from __future__ import annotations

import re

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, apply_step, keep_step, pred_step, stash_step
from co.e2e.casespec.writeoff import cancel_compensate, cancel_one
from co.e2e.casespec.writeoff_auto_sql import AUTO_KEYS

ROUTE = "/v1/arap/writeoff/auto"
_CANCEL = "/v1/arap/writeoff/cancel"
_DIGITS = re.compile(r"^[0-9]{1,20}\Z")
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_CODE = re.compile(r"^[0-9A-Za-z._-]{1,12}\Z")
_INT_MAX = 2147483647
_BAD = (400, "bad_request")


def _as_id(env: Env, key: str) -> int:
    raw = env.bag.get(key, "")
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError("挑单缺 " + key)
    return int(raw)


def ask_body(env: Env, dry_run: bool) -> dict:
    # 限定挑中的收款单行和发票；dry_run 不设上限，执行时上限 1.00。
    partner = env.bag.get("hxa_dw", "")
    if _CODE.fullmatch(partner or "") is None:
        raise ValueError("挑单缺 hxa_dw")
    body = {
        "flag": "AR",
        "partner": partner,
        "receipt": {"type": "ar_receipt", "id": _as_id(env, "hxa_rc"), "line_id": _as_id(env, "hxa_rcl")},
        "targets": [{"type": "sale_invoice", "id": _as_id(env, "hxa_inv")}],
        "dry_run": dry_run,
    }
    if not dry_run:
        body["max_amount"] = 1
    return body


def run_body(env: Env) -> dict:
    return ask_body(env, False)


def cancel_body(env: Env) -> dict:
    digits = env.bag.get("hxa_cno", "")
    if _DIGITS.fullmatch(digits or "") is None:
        raise ValueError("缺核销号")
    return {"flag": "AR", "cancel_no": "HXAR" + digits}


def no_partner_body(_env: Env) -> dict:
    return {"flag": "AR", "dry_run": True}


def bad_flag_body(_env: Env) -> dict:
    return {"flag": "AR", "partner": "C001", "targets": [{"type": "purchase_invoice", "id": 1}], "dry_run": True}


HXA_BUILDERS = {
    "hxa_run": run_body,
    "hxa_cancel": cancel_body,
    "hxa_no_partner": no_partner_body,
    "hxa_bad_flag": bad_flag_body,
}

_LINE = "bag:hxa_rcl"
_DOC = "bag:hxa_invl"


def _preds(remain: str, balance: str, rows: str) -> tuple[Step, Step, Step]:
    return (
        pred_step("hx_remain", remain + "hxa_r0", _LINE),
        pred_step("hx_arbal", balance + "hxa_i0", _DOC),
        pred_step("hx_arrows", rows + "hxa_n0", _DOC),
    )


def _auto_case() -> CaseSpec:
    run_checks = (("ok", "true"), ("flag", "AR"), ("batches#len", "1"))
    steps = (
        stash_step("hx_remain", "hxa_r0", _LINE),
        stash_step("hx_arbal", "hxa_i0", _DOC),
        stash_step("hx_arrows", "hxa_n0", _DOC),
        Step("custom", sql_name="hxa_dry", id_from="none"),
    )
    steps += _preds("same:", "same:", "same:")
    # 执行：先记下响应里各批的核销号再核对（之后失败时补偿按它取消）；取消成功后记下「已取消」。
    steps += (Step("custom", ROUTE, checks=run_checks, sql_name="hxa_exec", id_from="none"),)
    steps += _preds("shift:-1:", "shift:-1:", "plus1:")
    steps += (
        apply_step(_CANCEL, "hxa_cancel", "none", (("ok", "true"), ("cancel_no", "$prefix:HXAR"), ("flag", "AR"))),
        keep_step("hxa_cdone", _DOC),
    )
    steps += _preds("same:", "same:", "same:")
    title = (
        "应收自动核销：限定一行收款单和一张销售发票，先试算（计划落在挑中的行上、不超过余额、不写），"
        "再上限 1.00 执行恰好一批、两边余额各少 1；按该批核销号取消后复原"
    )
    tags = frozenset(("w:ar_receipt", "w:sale_invoice"))
    return CaseSpec("ar-writeoff-auto", title, AUTO_KEYS, steps, True, tags=tags, routes=(ROUTE, _CANCEL))


def _bad_case() -> CaseSpec:
    steps = (apply_step(ROUTE, "hxa_no_partner", "none", (), _BAD), apply_step(ROUTE, "hxa_bad_flag", "none", (), _BAD))
    title = "自动核销缺 partner、flag 与 targets 的类型不一致：登录前 400"
    return CaseSpec("ar-writeoff-auto-bad", title, (), steps, tags=frozenset(("probe",)), routes=(ROUTE,))


def _compensate(env: Env, _case: str) -> str:
    notes = [cancel_compensate(env, ("hxa_cno", "hxa_cdone"), "AR")]
    # 响应里多出的批（本该只有一批）：逐批取消。
    for digits in env.bag.get("hxa_more", "").split(","):
        if _DIGITS.fullmatch(digits) is not None:
            notes.append(cancel_one(env, "AR", digits)[1])
    return "；".join(notes)


HXA_CLEANUP = {"ar-writeoff-auto": _compensate}


def hxa_cases() -> list[CaseSpec]:
    return [_auto_case(), _bad_case()]
