"""总账记账 /v1/co/gl/vouchers/post 的请求和响应。字段名与桥一致，响应放行桥多给的字段。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar, Sign

_FORBID = ConfigDict(extra="forbid")
# GL_accvouch.ino_id 是 smallint；一次最多 200 张（桥 GlPostParse.Max）。
_NO_MAX = 32767
POST_MAX = 200

GL_POST_HELP = (
    "记账：调用 U8 自己的总账记账组件（与 U8 界面「记账」相同），把 period 期的这些凭证记入科目总账和辅助账。"
    "只能记本年第一个未结账的期间；凭证必须已审核、不是错误凭证、未记账，"
    "总账选项要求出纳签字、主管签字时还要已签；作废凭证照 U8 也记账（只打记账标志，不计入科目总账）。"
    "年度首张凭证记账前照 U8 做期初对账和期初试算。"
    "有人正在 U8 里记账（已汇总范围未记完）时返回 409。全部凭证在一个事务里记账，任何一张不合格都不记。"
    "死锁、锁等待或执行超时、事务被中止时已回滚，返回 503 u8_unavailable，可以稍后重试。"
    "fiscal_year 省略时取登录日期的年份。取消记账（恢复最近一次记账）见 gl/vouchers/unpost（第二级写入，默认关闭，打开后只对测试账套开放）。"
    "收到 504 outcome_unknown 时先用 gl/vouchers/load 核对 posted 再决定是否重试。"
)


class GlPostVoucherIn(BaseModel):
    model_config = _FORBID
    sign: Sign
    no: int = Field(..., ge=1, le=_NO_MAX, description="凭证号（ino_id），1 到 32767")


class GlPostIn(CoAuth):
    period: int = Field(..., ge=1, le=12, description="会计期间，1 到 12，必须是该年度第一个未结账的期间")
    vouchers: list[GlPostVoucherIn] = Field(
        ...,
        min_length=1,
        max_length=POST_MAX,
        description="要记账的凭证，1 到 200 张，不能重复",
    )
    fiscal_year: int | None = Field(
        None,
        ge=1900,
        le=9999,
        description="会计年度。缺省为登录日期（date）的年份；year 是账套库年度，不参与判断",
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _unique(self) -> GlPostIn:
        seen = {(item.sign, item.no) for item in self.vouchers}
        if len(seen) != len(self.vouchers):
            raise ValueError("vouchers 里有重复的凭证")
        return self

    def audit_ref(self) -> str:
        first = self.vouchers[0]
        more = f"+{len(self.vouchers) - 1}" if len(self.vouchers) > 1 else ""
        return f"{self.period}-{first.sign}-{first.no}{more}"


class GlPostedOut(BaseModel):
    model_config = PASS
    sign: Scalar = Field(None, description="凭证类别字")
    no: Scalar = Field(None, description="凭证号")
    state: dict[str, Any] | None = Field(
        None,
        description="记账后重新读到的状态：posted 为 true，poster 是记账人（操作员姓名），另有审核、出纳签字、作废等",
    )


class GlPostOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    period: Scalar = Field(None, description="会计期间")
    fiscal_year: Scalar = Field(None, description="会计年度")
    posted: list[GlPostedOut] | None = Field(None, description="已记账的凭证，顺序同请求")
    local_txn: bool | None = Field(None, description="记账事务是本地事务（提交前已核对没有升级 MSDTC），成功时为 true")
