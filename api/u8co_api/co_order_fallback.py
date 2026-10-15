"""order 参数的转发：桥升级前后都能用（vouchers/search、reports/order_execution）。

- order 没给：照旧转发，响应不变。
- order 为 asc：asc 就是桥的缺省顺序，不转给桥（旧桥也能用），响应带 order_applied: true。
- order 为 desc：带着 order 调桥。旧桥不认识这个字段（400「含未知字段」或「不能设置字段」，field 为 order）时，
  去掉 order 重试一次，响应带 order_applied: false：列表是最旧在前，next 还是旧桥的游标，调用方要知道这一点。
  新桥照常处理，响应带 order_applied: true。field 不是 order 的 400 原样返回，不重试。
"""

from __future__ import annotations

from typing import Any

from u8co_api.co_service import run_call
from u8co_api.errors import ApiError

_ORDER = "order"
# 订单执行文档「用法」里关于 order 的两条。
ORDER_USAGE = (
    "order 为 desc 时最新的订单在前（订单日期、订单 id、行 id 降序），next 是游标，原样传回；游标不能跨 order 使用",
    "给了 order 时响应带 order_applied：false 表示桥还没升级，仍是最旧在前，继续读时去掉 order",
)


def run_ordered(request, caller, action: str, bridge_path: str, body) -> dict[str, Any]:
    order = getattr(body, _ORDER, None)
    if order is None:
        return run_call(request, caller, action, bridge_path, body)
    plain = body.model_copy(update={_ORDER: None})
    if order != "desc":
        return {**run_call(request, caller, action, bridge_path, plain), "order_applied": True}
    try:
        result = run_call(request, caller, action, bridge_path, body)
    except ApiError as exc:
        if not rejected_order(exc):
            raise
        return {**run_call(request, caller, action, bridge_path, plain), "order_applied": False}
    return {**result, "order_applied": result.get(_ORDER) == "desc"}


def rejected_order(error: ApiError) -> bool:
    """旧桥因不认识 order 拒绝了请求：400 且 field 为 order（只在自己加了 order 时才据此去掉字段重试一次）。"""
    return error.status == 400 and error.field == _ORDER
