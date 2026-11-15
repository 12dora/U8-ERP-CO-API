"""期初记账（/v1/openings/post）：采购（pu）、存货核算（ia）的期初记账、取消期初记账；应收应付期初单据（/v1/openings/arap）。混入 U8CoClient。

第二级写入：桥 enableReplicatedWrites 缺省关闭（403 feature_disabled），打开后只对测试账套开放（403 test_account_only）。
openings/post 的 module 为 pu 或 ia，action 为 post 或 unpost；ia 是长时操作（按 long_timeout 读）。openings/arap 的 side 为 ar 或 ap，action 为
create、delete、verify、unverify。预演用 client.dry()（桥 rollback 模式），幂等键用 client.keyed(key)。
字段和取值范围与桥的约定一致；桥还会再查一遍。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

OPENINGS_ROUTE = "/v1/openings/post"
OPENING_POST_MODULES = ("pu", "ia")
OPENING_POST_ACTIONS = ("post", "unpost")
OPENINGS_ARAP_ROUTE = "/v1/openings/arap"
OPENING_ARAP_SIDES = ("ar", "ap")
OPENING_ARAP_ACTIONS = ("create", "delete", "verify", "unverify")
# create 收的字段（请求里按这个顺序）；前三个必填。
OPENING_ARAP_CREATE = ("partner", "amount", "account", "department", "person", "digest", "currency", "exch_rate")
_ARAP_NEED = 3


def openings_post_fields(call: U8Call, module: str, action: str) -> dict[str, Any]:
    # 键顺序同其他路由：公共字段、module、action。password 由 call() 换成 password_enc。
    if module not in OPENING_POST_MODULES:
        raise ValueError("module 只能是 " + "、".join(OPENING_POST_MODULES))
    if action not in OPENING_POST_ACTIONS:
        raise ValueError("action 只能是 post 或 unpost")
    return {
        "acc": call.acc,
        "year": call.year,
        "operator": call.operator,
        "password": call.password,
        "date": call.date,
        "module": module,
        "action": action,
    }


def _auth_fields(call: U8Call) -> dict[str, Any]:
    return {
        "acc": call.acc,
        "year": call.year,
        "operator": call.operator,
        "password": call.password,
        "date": call.date,
    }


def openings_arap_fields(
    call: U8Call, side: str, action: str, doc_id: int | None, create: dict[str, Any]
) -> dict[str, Any]:
    # 键顺序：公共字段、side、action，然后 id（delete / verify / unverify）或 create 的字段。值为 None 的字段不发。
    if side not in OPENING_ARAP_SIDES:
        raise ValueError("side 只能是 ar 或 ap")
    if action not in OPENING_ARAP_ACTIONS:
        raise ValueError("action 只能是 " + "、".join(OPENING_ARAP_ACTIONS))
    unknown = sorted(set(create) - set(OPENING_ARAP_CREATE))
    if unknown:
        raise ValueError("不认识的字段：" + "、".join(unknown))
    given = {name: create[name] for name in OPENING_ARAP_CREATE if create.get(name) is not None}
    fields = _auth_fields(call)
    fields.update(side=side, action=action)
    if action != "create":
        if given:
            raise ValueError(f"action={action} 只收 id，不收 " + "、".join(given))
        if type(doc_id) is not int or doc_id < 1:
            raise ValueError(f"action={action} 需要单据 id（正整数）")
        fields["id"] = doc_id
        return fields
    if doc_id is not None:
        raise ValueError("新增期初单据不收 id")
    missing = [name for name in OPENING_ARAP_CREATE[:_ARAP_NEED] if name not in given]
    if missing:
        raise ValueError("action=create 缺少字段 " + "、".join(missing))
    fields.update(given)
    return fields


class U8CoOpeningsMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def post_openings(self, call: U8Call, module: str = "pu", action: str = "post") -> dict[str, Any]:
        """期初记账（action=post）或取消期初记账（action=unpost），module 为 pu 或 ia。已记账再记、未记账取消、
        存货核算已有月份结账不能取消等由桥返回 409；存货核算的登录日期要在其启用年度（call.date）。"""
        return self.call(OPENINGS_ROUTE, openings_post_fields(call, module, action))

    def openings_arap(
        self, call: U8Call, side: str, action: str, doc_id: int | None = None, **create: Any
    ) -> dict[str, Any]:
        """应收（side=ar）/ 应付（side=ap）期初单据。action=create 时以关键字给 partner、amount（负数为反方向余额）、
        account，可选 department、person、digest、currency、exch_rate；delete、verify、unverify 只给 doc_id。
        桥未开 enableReplicatedWrites 时 403 feature_disabled，非测试账套 403 test_account_only；
        启用月已结账、不是期初单据、已审核 / 未审核等由桥返回 409。"""
        return self.call(OPENINGS_ARAP_ROUTE, openings_arap_fields(call, side, action, doc_id, create))
