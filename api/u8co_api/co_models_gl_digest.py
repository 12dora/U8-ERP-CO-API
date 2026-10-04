"""总账凭证摘要 /v1/co/gl/vouchers/digest（事件源）的请求和响应。字段名与桥一致，响应放行桥多给的字段。"""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import PASS, Scalar

DIGEST_MAX = 500
Period = Annotated[StrictInt, Field(ge=1, le=12)]

GL_DIGEST_HELP = (
    "按会计期间列出每张凭证的指纹，供事件服务发现凭证变化。\n\n"
    "**用法**\n"
    "- 可发现新增、删除、审核、出纳签字、记账、作废和修改（GL_accvouch 没有 rowversion）\n"
    "- 翻页：next 放进 after，响应的 periods 原样放进请求\n\n"
    "**规则**\n"
    "- periods 省略时取该年度全部未结账期间（GL_mend.bflag=0，1 到 12 期）\n"
    "- 另加最近 closed_periods 个已结账期间（缺省 1）\n"
    "- 只在一个年度里取：上年仍未结账的期间、跨年的上年 12 月要显式给 fiscal_year 和 periods\n"
    "- fingerprint 是 SHA-256，取 SUM(md)、SUM(mc)、行数、MAX(i_id)、制单人、审核人、出纳、"
    "记账人、记账标志、作废标志"
)


class GlDigestIn(CoAuth):
    fiscal_year: StrictInt | None = Field(
        None,
        ge=1900,
        le=9999,
        description="会计年度。缺省为登录日期（date）的年份；year 是账套库年度，不参与判断",
    )
    periods: list[Period] | None = Field(
        None,
        min_length=1,
        max_length=12,
        description="要扫描的期间，1 到 12 个，不能重复。省略时由桥按结账状态取",
    )
    closed_periods: StrictInt | None = Field(
        None,
        ge=0,
        le=12,
        description="periods 省略时另扫最近几个已结账期间，0 到 12，缺省 1。不能与 periods 同时给",
    )
    after: str | None = Field(
        None,
        pattern=r"^[0-9]{1,10}\.[0-9]{1,10}\.[0-9]{1,10}$",
        description="上一页响应的 next，原样传回",
    )
    limit: StrictInt | None = Field(None, ge=1, le=DIGEST_MAX, description="每页条数，1 到 500，缺省 200")
    keys_only: StrictBool | None = Field(None, description="为 true 时每张凭证只返回 period、sign、no、fingerprint")

    @model_validator(mode="after")
    def _periods(self) -> GlDigestIn:
        if self.periods is not None and len(set(self.periods)) != len(self.periods):
            raise ValueError("periods 不能重复")
        if self.periods is not None and self.closed_periods is not None:
            raise ValueError("periods 与 closed_periods 不能同时给")
        return self


class GlDigestItem(BaseModel):
    model_config = PASS
    period: Scalar = Field(None, description="会计期间")
    sign: Scalar = Field(None, description="凭证类别字")
    no: Scalar = Field(None, description="凭证号")
    fingerprint: Scalar = Field(None, description="凭证指纹，小写十六进制 SHA-256")
    date: Scalar = Field(None, description="制单日期")
    maker: Scalar = Field(None, description="制单人")
    checker: Scalar = Field(None, description="审核人")
    cashier: Scalar = Field(None, description="出纳签字人")
    bookkeeper: Scalar = Field(None, description="记账人")
    posted: Scalar = Field(None, description="是否已记账")
    void: Scalar = Field(None, description="是否已作废")
    debit_total: Scalar = Field(None, description="借方合计")
    lines: Scalar = Field(None, description="分录行数")


class GlDigestOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到摘要")
    fiscal_year: Scalar = Field(None, description="会计年度")
    periods: list[int] | None = Field(None, description="实际扫描的期间，升序。翻页时原样放进请求的 periods")
    items: list[GlDigestItem] | None = Field(None, description="按期间、类别、凭证号排序的凭证")
    next: Scalar = Field(None, description="下一页游标，原样放进 after。最后一页为空")
    watermark: Scalar = Field(None, description="所扫期间的 MAX(i_id)，十进制字符串，在读这一页之前取")
    ident: Scalar = Field(None, description="GL_accvouch 的 IDENT_CURRENT，十进制字符串；变小说明账套被还原")
