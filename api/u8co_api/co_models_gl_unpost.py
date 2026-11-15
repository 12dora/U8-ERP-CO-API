"""总账取消记账 /v1/co/gl/vouchers/unpost 的请求和响应。字段名与桥一致，响应放行桥多给的字段。"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl_post import GlPostVoucherIn
from u8co_api.co_models_gl import PASS, Scalar

GL_UNPOST_HELP = (
    "撤销本年度最近一次记账（同 U8 客户端「恢复记账前状态 → 最近一次记账」），凭证改回未记账。\n\n"
    "**规则**\n"
    "- 按 U8 记账的汇总口径冲回科目总账、辅助总账和多辅助总账，在一个事务里完成\n"
    "- 范围是 U8 记录的最近一次记账（桥记账和 U8 客户端记账都会留下），不能挑选单张凭证\n"
    "- 给了 period、vouchers 时必须与这次记账的范围完全一致\n"
    "- 冲回后与已记账凭证重新汇总核对，不一致整笔回滚\n\n"
    "**限制**\n"
    "- 默认关闭：桥未开 enableReplicatedWrites 时 403 feature_disabled\n"
    "- 打开后只对桥配置为测试账套的账套开放，其余 403 test_account_only\n\n"
    "**错误**\n"
    "- 409（不写入）：period、vouchers 与这次记账的范围不一致，或冲回后核对不一致\n"
    "- 409：期间或后续期间已结账、凭证已做银行对账或往来两清\n"
    "- 409：凭证已被红字冲销、正被人编辑，或本年度没有可恢复的记账\n"
    "- 504 outcome_unknown：先用 gl/vouchers/load 核对 posted 再决定是否重试"
)

# 与桥 GlUnpostReq.Max 一致：一次最多恢复 500 张（桥记账一次最多 200 张，U8 客户端的一次记账可能更多）。
UNPOST_MAX = 500


class GlUnpostIn(CoAuth):
    fiscal_year: int | None = Field(
        None,
        ge=1900,
        le=9999,
        description="会计年度。缺省为登录日期（date）的年份；year 是账套库年度，不参与判断",
    )
    period: int | None = Field(None, ge=1, le=12, description="核对用：最近一次记账的会计期间；给 vouchers 时必填")
    vouchers: list[GlPostVoucherIn] | None = Field(
        None,
        min_length=1,
        max_length=UNPOST_MAX,
        description="核对用：最近一次记账的全部凭证，1 到 500 张，不能重复；与范围不一致时 409",
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _shape(self) -> GlUnpostIn:
        if self.vouchers is None:
            return self
        if self.period is None:
            raise ValueError("给 vouchers 时必须给 period")
        seen = {(item.sign, item.no) for item in self.vouchers}
        if len(seen) != len(self.vouchers):
            raise ValueError("vouchers 里有重复的凭证")
        return self

    def audit_ref(self) -> str:
        if not self.vouchers:
            return str(self.period) if self.period else "last"
        first = self.vouchers[0]
        more = f"+{len(self.vouchers) - 1}" if len(self.vouchers) > 1 else ""
        return f"{self.period}-{first.sign}-{first.no}{more}"


class GlUnpostedOut(BaseModel):
    model_config = PASS
    sign: Scalar = Field(None, description="凭证类别字")
    no: Scalar = Field(None, description="凭证号")


class GlUnpostOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    fiscal_year: Scalar = Field(None, description="会计年度")
    period: Scalar = Field(None, description="被恢复的记账所在的会计期间")
    count: Scalar = Field(None, description="改回未记账的凭证张数")
    vouchers: list[GlUnpostedOut] | None = Field(None, description="改回未记账的凭证")
