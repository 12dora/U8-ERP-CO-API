"""订单执行 /v1/reports/order_execution、单据追溯 /v1/reports/doc_trace 的查询条件和正文。

方法在 U8CoReportsMixin（report_order_execution、report_doc_trace）。字段和取值范围与桥的约定一致；桥还会再查一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common, _int, _put_optional, _text, _ymd
from co.client.u8co_reports import _paging, _put_flag

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

ORDER_TYPES = ("sale_order", "purchase_order")
TRACE_DIRECTIONS = ("both", "up", "down")
# 与桥 ReportsTraceMap 的节点表一致。
TRACE_KINDS = (
    "sale_order",
    "dispatch",
    "sale_return",
    "sale_return_apply",
    "sale_out",
    "sale_invoice",
    "ar_receipt",
    "purchase_requisition",
    "purchase_order",
    "arrival",
    "purchase_return",
    "purchase_in",
    "purchase_invoice",
    "ap_payment",
    "ap_refund",
    "ar_refund",
    "qm_incoming_inspect",
    "qm_product_inspect",
    "qm_incoming_check",
    "qm_product_check",
    "qm_incoming_reject",
    "qm_product_reject",
    "qm_other_inspect",
    "qm_other_check",
    "production_order",
    "material_out",
    "product_in",
    "bom",
)
_ID_MAX = 2147483647


@dataclass(frozen=True)
class OrderExecQuery:
    """订单执行。ids 与 code、date_from、date_to、partner 二选一；None / 空串的字段不发送。"""

    type: str
    ids: tuple[int, ...] | None = None
    code: str = ""
    date_from: str = ""
    date_to: str = ""
    partner: str = ""
    only_open: bool | None = None
    after: str = ""
    limit: int | None = None


@dataclass(frozen=True)
class DocTraceQuery:
    """单据追溯。depth、direction、max_nodes 不给时桥取 3、both、200。"""

    type: str
    id: int
    depth: int | None = None
    direction: str = ""
    max_nodes: int | None = None


def _ids(values: object) -> list[int] | None:
    if values is None:
        return None
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 100:
        raise ValueError("ids 必须是 1 到 100 个正整数")
    return [_int(value, "ids", 1, _ID_MAX) for value in values]


def _filters(fields: dict[str, Any], query: OrderExecQuery) -> None:
    _put_optional(fields, "code", query.code and _text(query.code, "订单号 code", 30))
    _put_optional(fields, "date_from", query.date_from and _ymd(query.date_from, "date_from"))
    _put_optional(fields, "date_to", query.date_to and _ymd(query.date_to, "date_to"))
    if query.date_from and query.date_to and query.date_from > query.date_to:
        raise ValueError("date_from 不能晚于 date_to")
    _put_optional(fields, "partner", query.partner and _text(query.partner, "往来单位 partner", 20))


def order_exec_fields(call: U8Call, query: OrderExecQuery) -> dict[str, Any]:
    if query.type not in ORDER_TYPES:
        raise ValueError("type 只能是 sale_order 或 purchase_order")
    fields = _common(call)
    fields["type"] = query.type
    ids = _ids(query.ids)
    if ids is not None:
        if query.code or query.date_from or query.date_to or query.partner:
            raise ValueError("ids 不能和 code、date_from、date_to、partner 一起用")
        fields["ids"] = ids
    _filters(fields, query)
    _put_flag(fields, "only_open", query.only_open)
    _paging(fields, query.after, query.limit, 1000)
    return fields


def doc_trace_fields(call: U8Call, query: DocTraceQuery) -> dict[str, Any]:
    if query.type not in TRACE_KINDS:
        raise ValueError("type 不支持追溯")
    if query.direction and query.direction not in TRACE_DIRECTIONS:
        raise ValueError("direction 只能是 both、up 或 down")
    fields = _common(call)
    fields["type"] = query.type
    fields["id"] = _int(query.id, "id", 1, _ID_MAX)
    _put_optional(fields, "depth", None if query.depth is None else _int(query.depth, "depth", 1, 3))
    _put_optional(fields, "direction", query.direction)
    _put_optional(fields, "max_nodes", None if query.max_nodes is None else _int(query.max_nodes, "max_nodes", 1, 200))
    return fields
