"""单据列表 /v1/co/vouchers/list 和现存量 /v1/co/stock/current。按主键续读，watermark 用于增量。"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from u8co_api.co_models import CoAuth, VoucherType
from u8co_api.co_models_arc import Ufts
from u8co_api.co_models_notes_read import NoteType  # 票据只读列表
from u8co_api.co_models_gl import DATE, PASS, Scalar

_ID_MAX = 2147483647
_WATERMARK = "读这一页之前的 MIN_ACTIVE_ROWVERSION()-1。整轮读完后，把第一页的 watermark 作为下一轮的 changed_since"
Word = Annotated[str, Field(pattern=r"^[^\x00-\x1f\x7f]{1,60}$")]


class CoListFilter(BaseModel):
    """每个键追加一个条件。该类型没有对应列时桥返回 400。"""

    model_config = ConfigDict(extra="forbid")
    code: Word | None = Field(None, description="单据编号等于")
    code_from: Word | None = Field(None, description="单据编号下限，含")
    code_to: Word | None = Field(None, description="单据编号上限，含")
    date_from: str | None = Field(None, pattern=DATE, description="单据日期下限，yyyy-MM-dd，含当天")
    date_to: str | None = Field(None, pattern=DATE, description="单据日期上限，yyyy-MM-dd，含当天")
    cus_code: Word | None = Field(None, description="客户编码")
    ven_code: Word | None = Field(None, description="供应商编码")
    dep_code: Word | None = Field(None, description="部门编码")
    person_code: Word | None = Field(None, description="业务员编码")
    wh_code: Word | None = Field(None, description="仓库编码")
    maker: Word | None = Field(None, description="制单人")
    verified: bool | None = Field(None, description="true 只要已审核，false 只要未审核")
    closed: bool | None = Field(None, description="true 只要已关闭，false 只要未关闭")
    red: bool | None = Field(None, description="true 只要红字，false 只要蓝字")

    @model_validator(mode="after")
    def _ranges(self) -> CoListFilter:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to")
        return self


class CoVoucherListIn(CoAuth):
    # 合成一个 Literal，OpenAPI 里是一个平铺的 enum（不是 anyOf），客户端和 MCP 按同一份类型表校验。
    type: Literal[VoucherType, NoteType] = Field(
        ...,
        description="单据类型，与 /v1/co/vouchers/load 相同。总账凭证用 /v1/co/gl/vouchers/list。"
        "另收 ar_note / ap_note（应收 / 应付票据，只读，单张用 /v1/co/notes/get）",
    )
    filter: CoListFilter | None = Field(None, description="过滤条件，全部可省略")
    keys_only: bool | None = Field(None, description="为 true 时每行只返回 id、code 和 ufts，便于比对删除")
    changed_since: Ufts | None = Field(
        None,
        description="只返回表头或明细 ufts 大于该值的单据。用上一轮第一页的 watermark",
    )
    after: int | None = Field(None, ge=0, le=_ID_MAX, description="上一页响应的 next（最后一个主键），按主键续读")
    limit: int | None = Field(None, ge=1, le=500, description="每页条数，1 到 500，缺省 100")


class CoStockIn(CoAuth):
    wh: Word | None = Field(None, description="仓库编码")
    inv: Word | None = Field(None, description="存货编码")
    batch: Word | None = Field(None, description="批号")
    after: int | None = Field(None, ge=0, le=_ID_MAX, description="上一页响应的 next（CurrentStock.AutoID）")
    limit: int | None = Field(None, ge=1, le=500, description="每页条数，1 到 500，缺省 100")


class CoVoucherListOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到列表")
    type: Scalar = Field(None, description="单据类型")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按主键排序的单据表头：id、code、doc_date、往来单位、部门、业务员、maker、verifier、verified_at、"
        "verified、closed、red、ufts 等。检验单和不良品处理单另有 wf、wf_state（iVerifyStateNew：0 未提交，1 审批中，"
        "2 通过，-1 不通过）和 current_auditor（当前审核人姓名）；报检单没有这三列，另有 source、source_code、"
        "source_id、inspect_dep_code 等。票据（ar_note / ap_note）另有 settle_code、amount、remainder、close_id、opening、"
        "expire_date、dw_name、currency，closed 表示余额为 0。采购结算单（purchase_settle）verified 恒为 false，另有 "
        "settle_type、bus_type、pt_code、opening、memo、line_count、accounted_lines、invoice_lines、receipt_count、"
        "first_invoice_code、first_in_code、quantity、amount。出入库调整单（ia_adjust）verified 表示表体每行都已记账，"
        "另有 vouch_type（20 入库调整、21 出库调整等）、rd_flag、rd_code、auto、bus_type、unit_code、vendor_code、handler、"
        "memo、line_count、posted_lines、amount、created_at、modified_at；存货调价单（inventory_price_adjust）另有 memo。"
        "退货申请单（sale_return_apply）另有 sale_type、bus_type、wf、verify_state、currency、memo、line_count、quantity、"
        "amount、created_at、modified_at（数量、金额为负数）。"
        "keys_only 时只有 id、code、ufts。ufts 和金额是字符串",
    )
    next: Scalar = Field(None, description="下一页的 after。最后一页省略")
    watermark: Scalar = Field(None, description=_WATERMARK)


class CoStockOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到现存量")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按 CurrentStock.AutoID 排序：id、wh_code、wh_name、inv_code、inv_name、batch、qty、"
        "qty_frozen、qty_pending_in、qty_pending_out、qty_trans_in、qty_trans_out、qty_available、ufts 等。"
        "qty_available = 现存量 − 冻结量 − 待出库量 − 调拨待出量 + 待入库量 + 调拨待入量"
        "（iQuantity − fStopQuantity − fOutQuantity − fTransOutQuantity + fInQuantity + fTransInQuantity），"
        "未按 U8 的可用量控制选项调整",
    )
    next: Scalar = Field(None, description="下一页的 after。最后一页省略")
    watermark: Scalar = Field(None, description=_WATERMARK)
