"""写预演（dry_run）、名称 → 编码解析（archives/resolve）、幂等键结果查询（idempotency/get）。混入 U8CoClient。

写预演：client.dry() 返回一个副本，它发出的写调用（WRITE_ROUTES，同桥的写闸门）都带 dry_run: true，读调用原样发出
（桥按路由决定 rollback / validate 模式，不支持的组合 400）。旧路由 sale-orders/verify、dispatches/verify 不支持预演；预演也不能带幂等键，这两种在本地就拒绝。
幂等键：client.keyed(key) 返回一个副本，它发出的写调用都带 idempotency_key（全部写路由都支持）。
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from co.client.u8co_idem import IDEMPOTENT_ROUTES, LEGACY_ROUTES, WRITE_ROUTES, check_key

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

# WRITE_ROUTES、LEGACY_ROUTES 定义在 u8co_idem（幂等键与预演共用一份），这里照旧可以 import。
_ITEMS_MAX = 20
_Q_MAX = 100
_LIMIT_MAX = 20
_CALLER_MAX = 200


def dry_fields(route: str, fields: dict[str, Any]) -> dict[str, Any]:
    """写路由在请求字段后面加 dry_run: true，读路由原样返回。旧路由、带幂等键的写请求直接拒绝（桥也会 400）。"""
    if route in LEGACY_ROUTES:
        raise ValueError("旧路由不支持预演，请用 vouchers/verify")
    if route not in WRITE_ROUTES:
        return fields
    if "idempotency_key" in fields:
        raise ValueError("预演不能带幂等键")
    clean = dict(fields)
    clean["dry_run"] = True
    return clean


def _auth(call: U8Call) -> dict[str, Any]:
    # 键顺序同其他路由：公共字段在前。password 由 call() 换成 password_enc。
    return {
        "acc": call.acc,
        "year": call.year,
        "operator": call.operator,
        "password": call.password,
        "date": call.date,
    }


def _item(row: object) -> dict[str, str]:
    if not isinstance(row, dict) or set(row) != {"archive", "q"}:
        raise ValueError("items 的每一项必须是 {archive, q}")
    archive, text = row["archive"], row["q"]
    if type(archive) is not str or archive == "":
        raise ValueError("archive 必须是档案名")
    if type(text) is not str or not 1 <= len(text.strip()) <= _Q_MAX:
        raise ValueError(f"q 去掉首尾空白后必须是 1 到 {_Q_MAX} 个字符")
    return {"archive": archive, "q": text}


def resolve_fields(call: U8Call, items: object, limit: int | None, include_disabled: bool) -> dict[str, Any]:
    if isinstance(items, (str, bytes)) or not isinstance(items, (list, tuple)) or not 1 <= len(items) <= _ITEMS_MAX:
        raise ValueError(f"items 必须是 1 到 {_ITEMS_MAX} 项")
    fields = _auth(call)
    fields["items"] = [_item(row) for row in items]
    if limit is not None:
        if type(limit) is not int or not 1 <= limit <= _LIMIT_MAX:
            raise ValueError(f"limit 必须是 1 到 {_LIMIT_MAX}")
        fields["limit"] = limit
    if include_disabled:
        fields["include_disabled"] = True
    return fields


def bridge_route(route: str) -> str:
    """写路由写成 /v1/... 或 /u8co/v1/... 都认，返回桥的路径（/u8co/v1/...）。"""
    text = route.strip() if isinstance(route, str) else ""
    if text.startswith("/u8co/"):
        text = text[len("/u8co") :]
    if text not in IDEMPOTENT_ROUTES:
        raise ValueError("route 只能是写路由（例如 /v1/vouchers/verify），读路由没有幂等键")
    return "/u8co" + text


def idem_fields(call: U8Call, route: str, key: str, caller: str | None) -> dict[str, Any]:
    fields = _auth(call)
    fields["route"] = bridge_route(route)
    fields["idempotency_key"] = check_key(key)
    if caller is not None:
        if type(caller) is not str or not 1 <= len(caller) <= _CALLER_MAX:
            raise ValueError(f"caller 必须是 1 到 {_CALLER_MAX} 个字符")
        fields["caller"] = caller
    return fields


class U8CoR9Mixin:
    # client.dry() 的副本置为 True；U8CoClient.call 看它决定是否加 dry_run。
    _dry = False
    # client.keyed(key) 的副本置为该键；U8CoClient.call 看它给写调用加 idempotency_key。
    _idem_key: str | None = None

    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def dry(self) -> U8CoR9Mixin:
        """返回一个预演用的副本：它的写调用都带 dry_run: true，原客户端不受影响。"""
        clone = copy.copy(self)
        clone._dry = True
        return clone

    def keyed(self, key: str) -> U8CoR9Mixin:
        """返回一个带幂等键的副本：它的写调用都带 idempotency_key（一个键只用于一次写操作），读调用原样发出。"""
        clone = copy.copy(self)
        clone._idem_key = check_key(key)
        return clone

    def resolve(
        self, call: U8Call, items: object, limit: int | None = None, include_disabled: bool = False
    ) -> dict[str, Any]:
        """名称 → 编码：items 是 [{archive, q}]，每项按编码、名称、简称、助记码、包含逐级匹配。"""
        return self.call("/v1/archives/resolve", resolve_fields(call, items, limit, include_disabled))

    def idempotency_get(self, call: U8Call, route: str, key: str, caller: str | None = None) -> dict[str, Any]:
        """按幂等键查原请求的结果：found、state（ok / outcome_unknown / in_flight）、status、response。"""
        return self.call("/v1/idempotency/get", idem_fields(call, route, key, caller))
