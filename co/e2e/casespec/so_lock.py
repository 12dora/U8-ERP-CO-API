"""销售订单锁定与解锁（vouchers/lock）；采购订单暂不支持。

sale-order-lock：未审核的销售订单 → 锁定（响应 locker 是本操作员姓名，表头 cLocker 同）→ 本人再锁定 200（already）→
  解锁（cLocker 清空）→ 再解锁 409。
sale-order-lock-verified：已审核的销售订单锁定 409 state_mismatch（桥在调用 U8 之前拒绝）。
po-lock：采购订单锁定暂不支持（U8 的 DoLock 实测一律回「本张已经被修改,不能锁定.」），桥在登录前 400 bad_request。
sale-lock-bad-type：发货单不能锁定，登录前 400。

挑单与回读 SQL 在 so_lock_sql（接线在 wiring 汇总模块）。LOCK_CLEANUP 是用例中途失败时的补偿（尽力解锁），
由 runner 在该用例不是 PASS 时调用。别的操作员锁定时的修改 / 删除 / 审核闸门要第二个操作员，这里不测。
"""

from __future__ import annotations

import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, call, call_from, pred_step

_LOCK = "/v1/vouchers/lock"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_CONFLICT = (409, "state_mismatch")
_LOCKED = (("locked", "true"), ("locker", "$opname"))
_FREE = (("locked", "false"), ("locker", ""))


def _ask(kind: str, action: str) -> tuple:
    return (("type", kind), ("id", "$id"), ("action", action))


def _cycle(kind: str, key: str, sql: str) -> tuple:
    # 锁定 → 回读 cLocker → 本人再锁定 200（already）→ 解锁 → 回读为空 → 再解锁 409。
    src = "bag:" + key
    return (
        call_from(_LOCK, _ask(kind, "lock"), src, _LOCKED),
        pred_step(sql, "opname", src),
        call_from(_LOCK, _ask(kind, "lock"), src, _LOCKED + (("already", "true"),)),
        call_from(_LOCK, _ask(kind, "unlock"), src, _FREE),
        pred_step(sql, "empty", src),
        call_from(_LOCK, _ask(kind, "unlock"), src, (), _CONFLICT),
    )


def _so_case() -> CaseSpec:
    title = "销售订单锁定与解锁：锁定人是本操作员，本人重复锁定 200、重复解锁 409"
    steps = _cycle("sale_order", "lock_so", "lock_so_locker")
    return CaseSpec(
        "sale-order-lock", title, ("lock_so",), steps, True, optional=True,
        tags=frozenset(("w:sale_order",)), routes=(_LOCK,),
    )


def _so_verified_case() -> CaseSpec:
    title = "已审核的销售订单不能锁定：409 state_mismatch"
    step = call_from(_LOCK, _ask("sale_order", "lock"), "bag:lock_sov", (), _CONFLICT)
    return CaseSpec(
        "sale-order-lock-verified", title, ("lock_sov",), (step,), True, optional=True,
        tags=frozenset(("w:sale_order",)), routes=(_LOCK,),
    )


def _po_case() -> CaseSpec:
    # 采购订单锁定暂不支持：锁定、解锁都在登录前 400，不动单据。
    title = "采购订单锁定暂不支持：锁定、解锁都 400"
    steps = tuple(
        call(_LOCK, (("type", "purchase_order"), ("id", "1"), ("action", action)), (), 400, "bad_request")
        for action in ("lock", "unlock")
    )
    return CaseSpec("po-lock", title, (), steps, tags=frozenset(("probe",)), routes=(_LOCK,))


def _bad_type_case() -> CaseSpec:
    step = call(_LOCK, (("type", "dispatch"), ("id", "1"), ("action", "lock")), (), 400, "bad_request")
    return CaseSpec("sale-lock-bad-type", "发货单不能锁定：400", (), (step,), tags=frozenset(("probe",)), routes=(_LOCK,))


# ---- 补偿 ----


def _unlock(env: Env, key: str, kind: str) -> str:
    # steps 经 wiring 引用本模块，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.steps import auth_fields, invoke

    raw = env.bag.get(key, "")
    if _ID.fullmatch(raw or "") is None:
        return "没有挑中的单据"
    fields = auth_fields(env)
    fields.update({"type": kind, "id": int(raw), "action": "unlock"})
    try:
        status = invoke(env.client, _LOCK, fields)[0]
    except (U8CoError, OSError, RuntimeError, ValueError) as exc:
        return f"{kind} {raw} 解锁出错：{str(exc)[:60]}"
    if status == 200:
        return f"{kind} {raw} 已解锁"
    return f"{kind} {raw} 解锁返回 {status}（未锁定时 409 属正常）"


def lock_so_compensate(env: Env, _case: str) -> str:
    return _unlock(env, "lock_so", "sale_order")


LOCK_CLEANUP = {"sale-order-lock": lock_so_compensate}


def lock_cases() -> list[CaseSpec]:
    return [_so_case(), _so_verified_case(), _po_case(), _bad_type_case()]
