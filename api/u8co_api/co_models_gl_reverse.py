"""总账红字冲销 /v1/co/gl/vouchers/reverse 的请求和响应。字段名与桥一致，响应放行桥多给的字段。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import DATE, PASS, GlKeyIn, Scalar

GL_REVERSE_HELP = (
    "红字冲销：把一张已记账的总账手工凭证复制成一张红字凭证（与 U8 界面「冲销凭证」相同），"
    "金额、外币金额、数量和现金流量取负，科目、辅助核算、结算方式、票号、币种、汇率原样带过来，"
    "摘要前加「[冲销yyyy.mm.dd 类别-NNNN号凭证]」；凭证类别同原凭证，凭证号由 U8 按红字凭证的期间编号。"
    "红字凭证记下被冲销凭证的外部业务号（load 的 voucher.blue_out_no），同一张原凭证不能再冲销。"
    "原凭证用 fiscal_year、period、sign、no 定位，fiscal_year 省略取登录日期的年份，可以是上一年度（跨年冲销）；"
    "voucher_date 是红字凭证日期，省略取登录日期（date），必须在登录年度内、不早于原凭证日期，所在期间不能已结账。"
    "未记账、已作废、已被冲销、本身是红字冲销凭证、其它模块生成、带自定义项的凭证返回 409。"
    "红字凭证未审核、未记账，可以审核、记账，也可以作废后删除，但不能修改。"
    "U8 保存后自己提交；收到 504 outcome_unknown 时先 load 或 list 核对再重试。"
)


class GlReverseIn(GlKeyIn):
    fiscal_year: int | None = Field(
        None,
        ge=1900,
        le=9999,
        description="原凭证的会计年度。缺省为登录日期（date）的年份；不能晚于它",
    )
    voucher_date: str | None = Field(
        None,
        pattern=DATE,
        description="红字凭证日期，yyyy-MM-dd。缺省为登录日期；必须在登录年度内，不早于原凭证日期",
    )
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        year = f"{self.fiscal_year}:" if self.fiscal_year is not None else ""
        return f"{year}{self.period}-{self.sign}-{self.no}"


class GlReversedOut(BaseModel):
    model_config = PASS
    fiscal_year: Scalar = Field(None, description="原凭证的会计年度")
    period: Scalar = Field(None, description="原凭证的会计期间")
    sign: Scalar = Field(None, description="原凭证的类别字")
    no: Scalar = Field(None, description="原凭证号")
    out_no: Scalar = Field(None, description="原凭证的外部业务号（coutno_id），红字凭证的 cblueoutno_id 指向它")
    out_no_assigned: Scalar = Field(None, description="原凭证原来没有外部业务号、本次照 U8 取号补上时为 true")


class GlReverseOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    fiscal_year: Scalar = Field(None, description="红字凭证的会计年度")
    period: Scalar = Field(None, description="红字凭证的会计期间")
    sign: Scalar = Field(None, description="红字凭证的类别字")
    no: Scalar = Field(None, description="红字凭证号")
    voucher_date: Scalar = Field(None, description="红字凭证日期")
    lines: Scalar = Field(None, description="红字凭证的分录行数")
    out_no: Scalar = Field(None, description="红字凭证的外部业务号")
    reversal_of: GlReversedOut | None = Field(None, description="被冲销的原凭证")
