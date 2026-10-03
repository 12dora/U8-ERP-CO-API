"""订单执行 /v1/co/reports/order_execution、单据追溯 doc_trace 的请求和响应。字段名与桥一致，响应放行桥多给的字段。

数量是数字（六位小数），金额是数字（两位小数，原币；带 nat_ 的是本币）。after 是桥给的不透明游标，原样传回。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import DATE, PASS, Scalar
from u8co_api.co_models_reports import _AFTER, _NEXT, After, _real_date

_TEXT = r"^[^\x00-\x1f\x7f-\x9f]*[^\s\x00-\x1f\x7f-\x9f][^\x00-\x1f\x7f-\x9f]*$"
Code30 = Annotated[str, Field(max_length=30, pattern=_TEXT)]
Code20 = Annotated[str, Field(max_length=20, pattern=_TEXT)]
DocId = Annotated[StrictInt, Field(ge=1, le=2147483647)]
OrderType = Literal["sale_order", "purchase_order"]
# 与桥 ReportsTraceMap 的节点表一致。
TraceKind = Literal[
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
]
TRACE_KINDS = get_args(TraceKind)


class ReportOrderExecIn(CoAuth):
    type: OrderType = Field(..., description="sale_order 销售订单，purchase_order 采购订单")
    ids: list[DocId] | None = Field(
        None,
        min_length=1,
        max_length=100,
        description="订单 id，1 到 100 个。不能和 code、date_from、date_to、partner 一起用",
    )
    code: Code30 | None = Field(None, description="订单号等于，1 到 30 个字符")
    date_from: str | None = Field(None, pattern=DATE, description="订单日期起 yyyy-MM-dd（含）")
    date_to: str | None = Field(None, pattern=DATE, description="订单日期止 yyyy-MM-dd（含）")
    partner: Code20 | None = Field(None, description="客户（销售）或供应商（采购）编码等于")
    only_open: StrictBool | None = Field(
        None,
        description="为 true 时只要未执行完且未关闭的行：订单和行都没关闭，且销售的发货、出库、开票（采购的入库、开票）"
        "有一项未到订单数量",
    )
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description="每页行数，1 到 1000，缺省 200")

    @field_validator("date_from", "date_to")
    @classmethod
    def _dates(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _filters(self) -> ReportOrderExecIn:
        if self.date_from is not None and self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to")
        others = (self.code, self.date_from, self.date_to, self.partner)
        if self.ids is not None and any(value is not None for value in others):
            raise ValueError("ids 不能和 code、date_from、date_to、partner 一起用")
        return self

    def audit_ref(self) -> str:
        return self.type


class ReportDocTraceIn(CoAuth):
    type: TraceKind = Field(..., description="起点单据类型")
    id: DocId = Field(..., description="起点单据 id（表头主键）")
    depth: StrictInt | None = Field(None, ge=1, le=3, description="每个方向最多找几跳，1 到 3，缺省 3")
    direction: Literal["both", "up", "down"] | None = Field(
        None,
        description="both 上下游都找（缺省）；up 只找上游（来源）；down 只找下游（去向）",
    )
    max_nodes: StrictInt | None = Field(
        None,
        ge=1,
        le=200,
        description="最多返回的节点数（含起点），1 到 200，缺省 200。超出时 truncated 为 true",
    )


class ReportOrderExecOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    type: Scalar = Field(None, description="sale_order 或 purchase_order")
    only_open: Scalar = Field(None, description="是否只列未执行完的行")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按订单 id、行 id 排序，每行一项：id、code、date、partner、partner_name、currency、verified、closed、"
        "open、line_id、row_no、inv_code、inv_name、inv_std、due_date、qty、amount、nat_amount、invoiced_qty、"
        "invoiced_amount、returned_qty；销售另有 shipped_qty、shipped_amount、out_qty、received_amount、"
        "received_nat_amount，采购另有 arrived_qty、arrived_amount、in_qty、paid_amount、paid_nat_amount。"
        "执行数取 U8 订单行上的累计列",
    )
    next: Scalar = Field(None, description=_NEXT)


class ReportDocTraceOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    type: Scalar = Field(None, description="起点单据类型")
    id: Scalar = Field(None, description="起点单据 id")
    depth: Scalar = Field(None, description="每个方向的跳数")
    direction: Scalar = Field(None, description="both、up 或 down")
    nodes: list[dict[str, Any]] | None = Field(
        None,
        description="节点：type、id、code、date、state（unverified 未审核、verified 已审核、closed 已关闭）、"
        "level（起点 0，下游为正、上游为负的跳数）。起点在第一项",
    )
    edges: list[dict[str, Any]] | None = Field(
        None,
        description="边（上游 → 下游）：from_type、from_id、to_type、to_id、lines（关联的明细行数，收付款为核销记录数）",
    )
    omitted: Scalar = Field(None, description="因没有数据权限而没有列出的单据数（这些单据也不再往下找）")
    truncated: Scalar = Field(None, description="节点数到 max_nodes 或关联行过多而停止时为 true")
