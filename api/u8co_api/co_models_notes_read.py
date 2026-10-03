"""应收 / 应付票据的只读模型：/v1/co/notes/get 的请求和响应，以及 vouchers/list 另收的票据类型。

票据（AP_Note）不是单据类型：只能列表（vouchers/list 的 type=ar_note / ap_note）和按 id 或票据号读取，不能经 vouchers/* 写入。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictInt, model_validator

from u8co_api.co_models import _ID_MAX, CoAuth
from u8co_api.co_models_gl import PASS, Scalar

NoteType = Literal["ar_note", "ap_note"]
NOTE_TYPES = ("ar_note", "ap_note")
NoteCode = Annotated[str, Field(pattern=r"^[^\x00-\x1f\x7f-\x9f]{1,60}$")]

NOTE_GET_SUMMARY = "票据读取"
NOTE_GET_HELP = (
    "只读，只查数据库，不调用 U8 组件。按 id（AP_Note.Auto_ID，即 vouchers/list 的 id）或 code（票据号）读一张应收票据（ar_note）"
    "或应付票据（ap_note），二者给且只给一个。head 是票据表头：金额 amount、余额 remainder（结算、贴现、背书、退回后减少）、"
    "结算方式 settle_code、到期日 expire_date、期初 opening、收款单 close_id、ufts 等；subs 是处理记录（9A 结算、9C 退回、9D 贴现、"
    "9E 背书），带处理号 cancel_no 和凭证号 gl_ref。权限同票据列表；票据不存在返回 404 not_found，越权返回 403 no_permission。"
)


class CoNoteGetIn(CoAuth):
    type: NoteType = Field(..., description="ar_note 应收票据，ap_note 应付票据")
    id: StrictInt | None = Field(None, gt=0, le=_ID_MAX, description="票据主键 AP_Note.Auto_ID，1 到 2147483647；与 code 二选一")
    code: NoteCode | None = Field(None, description="票据号（cVouchID），1 到 60 个字符；与 id 二选一")

    @model_validator(mode="after")
    def _one_key(self) -> CoNoteGetIn:
        if (self.id is None) == (self.code is None):
            raise ValueError("id 和 code 必须给且只给一个")
        return self

    def audit_ref(self) -> str:
        return f"{self.type}:{self.id if self.id is not None else self.code}"


class CoNoteGetOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到票据")
    type: Scalar = Field(None, description="ar_note 或 ap_note")
    head: dict[str, Any] | None = Field(
        None,
        description="id、code（票据号）、flag、doc_date（签发日期）、receipt_date、expire_date、partner_code（常为空）、"
        "dw_name（单位名称）、dep_code、person_code、maker、settle_code、currency、rate、amount、amount_local、remainder、"
        "remainder_local、km_code（票据科目）、bank（出票银行）、digest、close_id、opening、sub_package、sub_start、sub_end、"
        "created_at、modified_at、ufts。金额和 ufts 是字符串",
    )
    subs: list[dict[str, Any]] | None = Field(
        None,
        description="按处理记录主键排序：id、style（9A / 9C / 9D / 9E）、style_name、date、amount、amount_local、interest、expense、"
        "discount_rate、bank（结算银行科目或背书对象编码）、km_code、operator、cancel_no、source_type、source_code、gl_ref、"
        "sub_start、sub_end",
    )
    subs_truncated: Scalar = Field(None, description="处理记录超过 500 条时为 true，只给前 500 条")
