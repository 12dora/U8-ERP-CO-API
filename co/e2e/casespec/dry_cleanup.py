"""写预演用例中途失败时的补偿（dry_suite.DRY_SUITE_CLEANUP 并入 runner._CLEANUP）：按记下的主键、核销号、外部业务号、凭证键尽力撤回。

预演本身不该写库；dry-* 用例失败时先由 dry_leaks 撤回「预演万一真写了」漏出去的东西，再补偿用例为了预演而真建的单据、
真做的核销和制单。补偿也失败时留给快照还原。
"""

from __future__ import annotations

import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.gl_voucher_bodies import GL_BODIES, slot
from co.e2e.casespec.compensate import compensate
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.writeoff import cancel_compensate, cancel_one
from co.e2e.casespec.dry_bodies import PZ_KEY
from co.e2e.casespec.dry_gaps import GAP_PLANS
from co.e2e.casespec.dry_more import MORE_PLANS
from co.e2e.casespec.dry_sale import SALE_PLANS
from co.e2e.casespec.dry_stock import STOCK_PLANS
from co.e2e.casespec.dry_leaks import leak_cleanup

_DIGITS = re.compile(r"^[0-9]{1,20}\Z")
PLANS = {**SALE_PLANS, **STOCK_PLANS, **MORE_PLANS, **GAP_PLANS}


def _send(env: Env, route: str, extra: dict) -> str:
    # steps 经 wiring 引用各模块的构造器，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.steps import auth_fields, invoke

    fields = auth_fields(env)
    fields.update(extra)
    try:
        return str(invoke(env.client, route, fields)[0])
    except (U8CoError, OSError, RuntimeError, ValueError) as exc:
        return "出错 " + str(exc)[:60]


def _planned(env: Env, case: str) -> str:
    return compensate(env, PLANS[case])


def _recovered(env: Env, case: str) -> str:
    # idempotency-get 新建凭证时响应丢了：按记下的幂等键查回期间 / 类别 / 凭证号。
    from co.e2e.casespec.idem_get import BRIDGE_CREATE, ROUTE, SLOT_KEY
    from co.e2e.casespec.gl_voucher_sql import gl_key, pack_key
    from co.e2e.casespec.steps import auth_fields, invoke

    key = env.bag.get(SLOT_KEY, "")
    if case != "idempotency-get" or not key:
        return ""
    fields = auth_fields(env)
    fields.update({"route": BRIDGE_CREATE, "idempotency_key": key})
    try:
        status, payload = invoke(env.client, ROUTE, fields)
        stored = payload.get("response") if status == 200 and payload.get("found") is True else None
        if not isinstance(stored, dict):
            return ""
        return pack_key(gl_key(stored.get("period"), stored.get("sign"), stored.get("no")))
    except (U8CoError, OSError, RuntimeError, ValueError):
        return ""


def _gl_drop(env: Env, case: str) -> str:
    # 本用例真建的总账凭证：作废再删除（已作废 / 已删除时 409 / 404 属正常）。
    text = env.bag.get(slot(CaseSpec(case, "")), "") or _recovered(env, case)
    try:
        key = GL_BODIES["key"](env, text)
    except ValueError:
        return "没有新建的凭证"
    notes = [op + " " + _send(env, "/v1/gl/vouchers/" + op, key) for op in ("void", "delete")]
    return "凭证补偿：" + "，".join(notes)


def _mo_reopen(env: Env, _case: str) -> str:
    # dry-mo-open 真关闭过生产订单行（记下了 dry_mo_closed）：重新打开（已打开时 409 属正常）。
    doc, line = env.bag.get("dry_mo_closed", ""), env.bag.get("mo_line", "")
    if not (_DIGITS.fullmatch(doc or "") and _DIGITS.fullmatch(line or "")):
        return "没有真关闭过生产订单行"
    body = {"type": "production_order", "id": int(doc), "action": "open", "line_ids": [int(line)]}
    return "重新打开生产订单行 " + _send(env, "/v1/vouchers/close", body)


def _hx(env: Env, _case: str) -> str:
    return cancel_compensate(env, ("dry_hx_cno", "dry_hx_cdone"), "AR")


def _ap_auto(env: Env, _case: str) -> str:
    notes = [cancel_compensate(env, ("dry_ap_cno", "dry_ap_cdone"), "AP")]
    # 响应里多出的批（本该只有一批）：逐批取消。
    for digits in env.bag.get("dry_ap_cno_more", "").split(","):
        if _DIGITS.fullmatch(digits) is not None:
            notes.append(cancel_one(env, "AP", digits)[1])
    return "；".join(notes)


def _ap_voucher(env: Env, _case: str) -> str:
    # 先取消制单（记下了外部业务号、用例自己还没取消才做），再弃审、删除应付单。
    notes = []
    digits = env.bag.get(PZ_KEY, "")
    if _DIGITS.fullmatch(digits or "") and not env.bag.get("dry_apb_pzdone"):
        notes.append("取消制单 " + _send(env, "/v1/arap/voucher/delete", {"flag": "AP", "pz_id": "AP" + digits}))
    notes.append(compensate(env, (("dry_apb", "ap_bill", True),)))
    return "；".join(notes)


SPECIFIC = {case: _planned for case in PLANS}
SPECIFIC.update(
    {
        "dry-gl-ops": _gl_drop,
        "dry-mo-open": _mo_reopen,
        "idempotency-get": _gl_drop,
        "dry-ar-writeoff": _hx,
        "ap-writeoff-auto": _ap_auto,
        "ap-voucher": _ap_voucher,
    }
)


def _chained(env: Env, case: str) -> str:
    # 预演用例：先撤回预演万一漏出去的（dry_leaks），再做本用例自己的补偿（真建的单据、真做的核销）。
    notes = [leak_cleanup(env, case)]
    own = SPECIFIC.get(case)
    if own is not None:
        notes.append(own(env, case))
    return "；".join(note for note in notes if note) or "预演没有漏出东西，也没有要撤回的单据"


def cleanups(names: list[str]) -> dict:
    """用例名 → 补偿函数：dry-* 用例都先查预演漏出去的，其余用 SPECIFIC。"""
    found = {name: _chained for name in names if name.startswith("dry-")}
    found.update({name: fn for name, fn in SPECIFIC.items() if name not in found})
    return found
