"""销售订单锁定与解锁（vouchers/lock）。混入 U8CoClient。采购订单锁定暂不支持（U8 的 DoLock 一律拒绝）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

LOCKABLE_KINDS = ("sale_order",)
LOCK_ACTIONS = ("lock", "unlock")
_ID_MAX = 2147483647


def lock_fields(call: U8Call, kind: str, action: str) -> dict[str, Any]:
    # 键顺序同其他单据路由：公共字段、type、id、action。password 由 call() 换成 password_enc。
    if kind not in LOCKABLE_KINDS:
        raise ValueError("该单据类型不支持锁定")
    if action not in LOCK_ACTIONS:
        raise ValueError("action 必须是 lock 或 unlock")
    doc_id = call.doc_id
    if type(doc_id) is not int or not 1 <= doc_id <= _ID_MAX:
        raise ValueError("单据 id 必须是正整数")
    return {
        "acc": call.acc,
        "year": call.year,
        "operator": call.operator,
        "password": call.password,
        "date": call.date,
        "type": kind,
        "id": doc_id,
        "action": action,
    }


class U8CoLockMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def lock_voucher(self, call: U8Call, kind: str, action: str) -> dict[str, Any]:
        return self.call("/v1/vouchers/lock", lock_fields(call, kind, action))
