"""应收 / 应付核销（arap/writeoff）和取消核销（arap/writeoff/cancel）。

ar-writeoff：挑一行有余额（≥ 2）的已审核本币收款单和同一客户下已应收审核、该行余额 ≥ 2 的本币销售发票行，
  核销 1.00 → 收款单该行余额少 1、发票该行余额少 1、发票行上多一条 9P 核销行；再用超过发票余额的金额核销 409，
  余额不变；核销记录查询（reports/arap_writeoffs）按新核销号列出恰好一批、可取消、金额和单据行对得上；
  然后按新核销号取消核销 → 两边余额和核销行条数都回到核销前（用例自己清理），核销记录查询不再列出；
  再取消同一核销号 404。核销一步（hx_exec，writeoff_run）先把响应里的核销号记下再核对响应；之后任何一步失败
  （含本用例的取消核销 409）时，HX_CLEANUP 尽力按记下的核销号取消（404 视为已取消）；补偿也失败时留给快照还原。
ap-writeoff：付款单对采购发票，核销 1.00 再取消，同上（本账套没有这类数据时记 N/A）。
ar-writeoff-bad-type：收款单去核销采购发票、取消核销的 flag 与核销号前缀不一致，都是登录前 400。

挑单与回读 SQL 在 writeoff_sql（接线在 wiring 汇总模块）。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from co.client.u8co_client import U8CoError
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, apply_step, keep_step, pred_step, stash_step
from co.e2e.casespec.writeoff_sql import AP_KEYS, AR_KEYS

ROUTE = "/v1/arap/writeoff"
_CANCEL = "/v1/arap/writeoff/cancel"
# 核销记录查询；两步的实现在 writeoff_list（HX_LIST_OPS，并入 ops_run）。
REPORT = "/v1/reports/arap_writeoffs"
_DIGITS = re.compile(r"^[0-9]{1,20}\Z")
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_INT_MAX = 2147483647
_CONFLICT = (409, "state_mismatch")
_BAD = (400, "bad_request")
_GONE = (404, "not_found")
# 每侧 bag 里的（核销号数字、「用例已取消」标记）键。
MARKS = {"AR": ("hx_cno", "hx_cdone"), "AP": ("hx_pno", "hx_pdone")}


def _as_id(env: Env, key: str) -> int:
    raw = env.bag.get(key, "")
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError("挑单缺 " + key)
    return int(raw)


def _ask(env: Env, keys: tuple[str, ...], kinds: tuple[str, str], amount: float) -> dict:
    receipt, line, doc, doc_line = (_as_id(env, key) for key in keys)
    return {
        "receipt": {"type": kinds[0], "id": receipt, "line_id": line},
        "items": [{"type": kinds[1], "id": doc, "line_id": doc_line, "amount": amount}],
    }


def ar_one_body(env: Env) -> dict:
    return _ask(env, AR_KEYS, ("ar_receipt", "sale_invoice"), 1)


def ap_one_body(env: Env) -> dict:
    return _ask(env, AP_KEYS, ("ap_payment", "purchase_invoice"), 1)


def ar_over_body(env: Env) -> dict:
    # 核销前记下的发票行余额再加 1：已经核销掉 1，所以超出 2。
    try:
        before = Decimal(env.bag.get("hx_i0", ""))
    except InvalidOperation as exc:
        raise ValueError("缺核销前余额") from exc
    return _ask(env, AR_KEYS, ("ar_receipt", "sale_invoice"), float(before + 1))


def _cancel(env: Env, key: str, flag: str) -> dict:
    digits = env.bag.get(key, "")
    if _DIGITS.fullmatch(digits or "") is None:
        raise ValueError("缺核销号")
    return {"flag": flag, "cancel_no": "HX" + flag + digits}


def ar_cancel_body(env: Env) -> dict:
    return _cancel(env, "hx_cno", "AR")


def ap_cancel_body(env: Env) -> dict:
    return _cancel(env, "hx_pno", "AP")


def bad_flag_body(_env: Env) -> dict:
    return {"flag": "AP", "cancel_no": "HXAR0000000000001"}


def bad_type_body(_env: Env) -> dict:
    return {
        "receipt": {"type": "ar_receipt", "id": 1},
        "items": [{"type": "purchase_invoice", "id": 1, "amount": 1}],
    }


HX_BUILDERS = {
    "hx_ar_one": ar_one_body,
    "hx_ap_one": ap_one_body,
    "hx_ar_over": ar_over_body,
    "hx_bad_type": bad_type_body,
    "hx_ar_cancel": ar_cancel_body,
    "hx_ap_cancel": ap_cancel_body,
    "hx_bad_flag": bad_flag_body,
}


def _snap(line: str, doc_line: str, balance: str, rows: str, keys: tuple[str, str, str]) -> tuple:
    return (
        stash_step("hx_remain", keys[0], "bag:" + line),
        stash_step(balance, keys[1], "bag:" + doc_line),
        stash_step(rows, keys[2], "bag:" + doc_line),
    )


def _moved(line: str, doc_line: str, balance: str, rows: str, keys: tuple[str, str, str]) -> tuple:
    return (
        pred_step("hx_remain", "shift:-1:" + keys[0], "bag:" + line),
        pred_step(balance, "shift:-1:" + keys[1], "bag:" + doc_line),
        pred_step(rows, "plus1:" + keys[2], "bag:" + doc_line),
    )


def _undone(line: str, doc_line: str, balance: str, rows: str, keys: tuple[str, str, str]) -> tuple:
    # 取消核销之后：收付款单行余额、单据行余额、核销行条数都回到核销前记下的值。
    return (
        pred_step("hx_remain", "same:" + keys[0], "bag:" + line),
        pred_step(balance, "same:" + keys[1], "bag:" + doc_line),
        pred_step(rows, "same:" + keys[2], "bag:" + doc_line),
    )


def list_steps(side: str) -> tuple[Step, Step]:
    # 核销之后按核销号列出一批且可取消（hx_listed）、取消之后不再列出（hx_unlisted）。
    fields = (("side", side),)
    return (
        Step("custom", fields=fields, sql_name="hx_listed", id_from="none"),
        Step("custom", fields=fields, sql_name="hx_unlisted", id_from="none"),
    )


def write_step(side: str) -> Step:
    # 核销：先把响应里的核销号数字记进 bag（MARKS），再核对响应（writeoff_run.hx_run）；之后任何一步失败，补偿都按它取消。
    checks = (("ok", "true"), ("cancel_no", "$prefix:HX" + side), ("items#len", "1"))
    return Step("custom", ROUTE, (("side", side),), checks=checks, sql_name="hx_exec", id_from="none")


def _cancel_steps(side: str, doc_line: str, args: tuple) -> tuple:
    # 核销记录查询按记下的核销号列出一批、可取消（writeoff_list）；按它取消之后记下「已取消」（补偿不再取消），
    # 余额复原，核销记录查询不再列出。
    low = side.lower()
    checks = (("ok", "true"), ("cancel_no", "$prefix:HX" + side), ("flag", side), ("items#len", "1"))
    listed, unlisted = list_steps(side)
    return (
        (
            listed,
            apply_step(_CANCEL, "hx_" + low + "_cancel", "none", checks),
            keep_step(MARKS[side][1], "bag:" + doc_line),
        )
        + _undone(*args)
        + (unlisted,)
    )


def _ar_case() -> CaseSpec:
    keys = ("hx_r0", "hx_i0", "hx_n0")
    args = ("hx_rcl", "hx_invl", "hx_arbal", "hx_arrows", keys)
    steps = _snap(*args) + (write_step("AR"),)
    steps += _moved(*args) + (
        apply_step(ROUTE, "hx_ar_over", "none", (), _CONFLICT),
        pred_step("hx_arbal", "shift:-1:hx_i0", "bag:hx_invl"),
    )
    steps += _cancel_steps("AR", "hx_invl", args) + (apply_step(_CANCEL, "hx_ar_cancel", "none", (), _GONE),)
    title = (
        "应收核销：收款单行对销售发票行核销 1.00，两边余额各少 1、多一条核销行，核销记录列出该批且可取消；超过余额 409；"
        "取消核销后余额和核销行复原、核销记录不再列出，再取消同一核销号 404"
    )
    tags = frozenset(("w:ar_receipt", "w:sale_invoice"))
    return CaseSpec("ar-writeoff", title, AR_KEYS, steps, True, tags=tags, routes=(ROUTE, _CANCEL, REPORT))


def _ap_case() -> CaseSpec:
    keys = ("hx_p0", "hx_q0", "hx_m0")
    args = ("hx_pcl", "hx_pinvl", "hx_aprbal", "hx_aprows", keys)
    steps = _snap(*args) + (write_step("AP"),)
    steps += _moved(*args) + _cancel_steps("AP", "hx_pinvl", args)
    title = "应付核销：付款单行对采购发票行核销 1.00，两边余额各少 1、多一条核销行，核销记录列出该批；取消核销后复原、不再列出"
    tags = frozenset(("w:ap_payment", "w:purchase_invoice"))
    routes = (ROUTE, _CANCEL, REPORT)
    return CaseSpec("ap-writeoff", title, AP_KEYS, steps, True, optional=True, tags=tags, routes=routes)


def _bad_type_case() -> CaseSpec:
    steps = (apply_step(ROUTE, "hx_bad_type", "none", (), _BAD), apply_step(_CANCEL, "hx_bad_flag", "none", (), _BAD))
    title = "收款单不能核销采购发票、取消核销的 flag 与核销号前缀不一致：登录前 400"
    tags = frozenset(("probe",))
    return CaseSpec("ar-writeoff-bad-type", title, (), steps, tags=tags, routes=(ROUTE, _CANCEL))


# ---- 补偿 ----

def cancel_one(env: Env, flag: str, digits: str) -> tuple[bool, str]:
    """按核销号取消一批，返回（已不在、说明）。404 表示这批已经取消了（例如用例自己的取消已成功、之后的核对才失败）。"""
    # steps 经 wiring 引用本模块，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.steps import auth_fields, invoke

    cancel_no = "HX" + flag + digits
    fields = auth_fields(env)
    fields.update({"flag": flag, "cancel_no": cancel_no})
    try:
        status, payload = invoke(env.client, _CANCEL, fields)
    except (U8CoError, OSError, RuntimeError, ValueError) as exc:
        return False, f"补偿取消核销 {cancel_no} 出错：{str(exc)[:60]}，留给快照还原"
    if status == 200:
        return True, f"已补偿取消核销 {cancel_no}"
    if status == 404:
        return True, f"核销号 {cancel_no} 已不存在（已取消），不再补偿"
    return False, f"补偿取消核销 {cancel_no} {status} {payload.get('code', '')}，留给快照还原"


def cancel_compensate(env: Env, keys: tuple[str, str], flag: str) -> str:
    """用例失败时尽力取消本用例做的核销：核销成功、记下了核销号、用例自己还没取消成功才发。
    补偿也失败（例如账套里有未审核的收款单时 409）就留给快照还原。"""
    if env.bag.get(keys[1]):
        return "取消核销已做过，不补偿"
    digits = env.bag.get(keys[0], "")
    if _DIGITS.fullmatch(digits or "") is None:
        return "没有记下核销号，不补偿（核销若已成功，留给快照还原）"
    gone, note = cancel_one(env, flag, digits)
    if gone:
        env.bag[keys[1]] = "1"
    return note


def _ar_compensate(env: Env, _case: str) -> str:
    return cancel_compensate(env, MARKS["AR"], "AR")


def _ap_compensate(env: Env, _case: str) -> str:
    return cancel_compensate(env, MARKS["AP"], "AP")


HX_CLEANUP = {"ar-writeoff": _ar_compensate, "ap-writeoff": _ap_compensate}


def hx_cases() -> list[CaseSpec]:
    return [_ar_case(), _ap_case(), _bad_type_case()]
