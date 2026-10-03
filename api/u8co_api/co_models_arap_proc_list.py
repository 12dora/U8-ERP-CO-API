"""应收 / 应付处理记录 /v1/co/arap/process/list（事件源）的请求和响应。

校验规则与桥的 ArapProcListReq 一致，响应放行桥多给的字段。两种用法：明细（缺省）按往来明细 Auto_ID 增量读处理行；
摘要（digest=true）按（处理方式、处理号）汇总指定期间的批次。金额是字符串（原币，两位小数）。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import PASS, Scalar
from u8co_api.co_models_reports import After

_ID_MAX = 2147483647
PROC_LIST_MAX = 500
Period = Annotated[StrictInt, Field(ge=1, le=12)]
Since = Annotated[str, Field(pattern=r"^[0-9]{1,10}$")]

PROC_LIST_SUMMARY = "应收应付处理记录"
PROC_LIST_HELP = (
    "只读，只查数据库，不调用 U8 组件。列出应收（flag=AR）或应付（flag=AP）往来明细里的处理行：核销、应收冲应付、"
    "应付冲应收、并账、红票对冲、汇兑损益、票据等，不含单据本身的审核行；本服务做的和 U8 客户端做的都列。"
    "明细（缺省）：按 Auto_ID 升序，changed_since 是上一轮的 watermark（只要更大的 Auto_ID），after 是本轮上一页的 next；"
    "watermark 是查询前已提交可见的最大 Auto_ID，ident 是 IDENT_CURRENT。往来明细没有 rowversion：在途事务可能先拿到较小的号、"
    "后提交，下一轮要从 watermark 往回退 max(滞后量, ident − watermark) 重读；制单（pz_id）和取消（删除）在明细里看不出来，"
    "用摘要比对。open_only 只要未结账期间的行，增量（changed_since > 0）时缺省 true。keys_only 每行只有 id、flag、style、code。"
    "摘要（digest=true）：每批一项 flag、style、code、min_id、max_id、pz（未制单为 null）、sum_d_f、sum_c_f、rows、partners；"
    "periods 省略时取该侧全部未结账期间（给了 fiscal_year 只取该年度），给了就是 fiscal_year（缺省登录年度）的这些期间；"
    "after 是上一页的 next（不透明字符串）。两种用法都返回 open_periods 和 last_closed。"
    "fiscal_year 是处理所在的会计年度（期间小于登记月份时为登记年份加 1），归期间用它和 period，不要用 reg_date 的年份。"
    "功能权限：应收取消操作 AR0807 或应收核销明细表 AR060107，应付 AP0807 / AP060107；数据权限按往来单位、部门、业务员，"
    "过滤后批次可能不完整，被过滤的批次与已取消的看不出区别，用作事件源时操作员必须有全部数据权限。"
)


class CoArapProcListIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统")
    changed_since: StrictInt | Since | None = Field(
        None, description="明细：上一轮的 watermark（十进制字符串或整数），只要 Auto_ID 更大的行；缺省 0 即全量"
    )
    after: StrictInt | After | None = Field(
        None, description="上一页响应的 next，原样传回：明细是整数 Auto_ID，摘要是字符串游标"
    )
    limit: StrictInt | None = Field(
        None, ge=1, le=PROC_LIST_MAX, description="每页条数（摘要是批数），1 到 500，明细缺省 100，摘要缺省 500"
    )
    keys_only: StrictBool | None = Field(None, description="明细：为 true 时每行只有 id、flag、style、code")
    open_only: StrictBool | None = Field(
        None, description="明细：只要登记期间未结账的行。缺省在 changed_since > 0 时为 true，否则 false"
    )
    digest: StrictBool | None = Field(None, description="为 true 时按（处理方式、处理号）返回期间摘要")
    fiscal_year: StrictInt | None = Field(
        None, ge=2000, le=2099, description="摘要：会计年度。缺省为登录日期（date）的年份；year 是账套库年度"
    )
    periods: list[Period] | None = Field(
        None, min_length=1, max_length=12, description="摘要：期间 1 到 12，不能重复。省略时取未结账期间"
    )

    @model_validator(mode="after")
    def _modes(self) -> CoArapProcListIn:
        digest = self.digest is True
        listed = ("changed_since", "keys_only", "open_only")
        summed = ("fiscal_year", "periods")
        for name in listed if digest else summed:
            if getattr(self, name) is not None:
                raise ValueError(f"digest=true 时不能带 {name}" if digest else f"digest=true 时才能带 {name}")
        if isinstance(self.changed_since, int) and not 0 <= self.changed_since <= _ID_MAX:
            raise ValueError("changed_since 必须在 0 到 2147483647 之间")
        if isinstance(self.changed_since, str) and int(self.changed_since) > _ID_MAX:
            raise ValueError("changed_since 必须在 0 到 2147483647 之间")
        self._after(digest)
        if self.periods is not None and len(set(self.periods)) != len(self.periods):
            raise ValueError("periods 不能重复")
        return self

    def _after(self, digest: bool) -> None:
        if self.after is None:
            return
        if digest and not isinstance(self.after, str):
            raise ValueError("digest=true 时 after 是上一页的 next（字符串）")
        if not digest and (not isinstance(self.after, int) or not 0 <= self.after <= _ID_MAX):
            raise ValueError("after 必须是上一页的 next（0 到 2147483647 的整数）")

    def audit_ref(self) -> str:
        return self.flag + (":digest" if self.digest else "")


class ProcPeriodOut(BaseModel):
    model_config = PASS
    year: Scalar = Field(None, description="会计年度")
    period: Scalar = Field(None, description="会计期间")


class CoArapProcListOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    flag: Scalar = Field(None, description="AR 或 AP")
    digest: Scalar = Field(None, description="摘要时为 true")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="明细：每行 id（Auto_ID）、flag、style（处理方式 cProcStyle）、code（处理号 cCancelNo）、vouch_type、"
        "vouch_id、co_vouch_type、co_vouch_id、partner、dept、person、line_id、debit_f、credit_f、pz_id、gl_sign、gl_no、"
        "reg_date、period、fiscal_year（处理所在的会计年度）、row_flag。摘要：每批 flag、style、code、min_id、max_id、pz、sum_d_f、"
        "sum_c_f、rows、fiscal_year、partners",
    )
    next: Scalar = Field(None, description="下一页的 after。最后一页为 null")
    watermark: Scalar = Field(None, description="明细：查询前已提交可见的最大 Auto_ID，十进制字符串")
    ident: Scalar = Field(None, description="明细：往来明细表的 IDENT_CURRENT，十进制字符串，不小于 watermark")
    periods: list[ProcPeriodOut] | None = Field(None, description="摘要：实际汇总的期间")
    open_periods: list[ProcPeriodOut] | None = Field(
        None, description="该侧未结账的期间（GL_mend，1 到 12 期，年度不晚于登录年度）"
    )
    last_closed: ProcPeriodOut | None = Field(None, description="最近一个已结账期间；没有为 null")
