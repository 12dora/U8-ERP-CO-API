"""期初记账 /v1/co/openings/post（采购管理 pu、存货核算 ia）和应收应付期初单据 /v1/co/openings/arap 的请求和响应。
字段名与桥一致，响应放行桥多给的字段。"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from u8co_api.co_doctext import TEST_ONLY_GATE
from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

OPENINGS_POST_SUMMARY = "采购 / 存货核算期初记账"
_LIMITS = "**限制**\n" + TEST_ONLY_GATE + "\n"
OPENINGS_POST_HELP = (
    "采购管理或存货核算的期初记账、取消期初记账。\n\n"
    "**用法**\n"
    "- module=pu：采购管理「期初记账」\n"
    "- module=ia：存货核算「期初余额」的记账 / 恢复\n"
    "- 期初年度取该模块的启用年度\n"
    "- dry_run：事务里执行后回滚（rollback 模式），什么都不写入，返回 DryRunOut\n"
    "- 预演的 action 为 opening_post 或 opening_unpost\n"
    "- 预演的操作后状态在 detail.opening：module、posted、opening_year、start_date，存货核算另有 counts\n\n"
    "**规则**\n"
    "- 存货核算记账从已审核的库存期初结存单取数\n"
    "- 写第 0 期期初明细和汇总（IA_Subsidiary / IA_Summary iMonth=0），置启用年度第 0 期 bflag_IA\n"
    "- 存货核算取消只删第 0 期的期初数据、清标志（不是 U8 的取消开账）\n"
    "- 存货核算的登录日期须在启用年度（建议就用启用日期），否则 400\n\n"
    + _LIMITS
    + "**权限**\n"
    "- 功能权限：采购 PU0206，存货核算 ASM3102\n\n"
    "**错误**\n"
    "- 400：module 或 action 不对，不访问 U8\n"
    "- 采购 409 state_mismatch：\n"
    "  - 期初已记账（再记）、期初未记账（取消）\n"
    "  - 已有月份结账、采购管理未启用\n"
    "  - 已有采购发票，或存货核算已期初记账，不能取消\n"
    "- 存货核算 409 state_mismatch：\n"
    "  - 存货核算未启用、期初已记账、期初未记账\n"
    "  - 已有月份结账或已有日常数据（不能取消）\n"
    "  - 库存与存货核算启用日期不一致\n"
    "  - 有先进先出 / 后进先出计价的期初行（接口暂不支持）"
)


class OpeningsPostIn(CoAuth):
    module: Literal["pu", "ia"] = Field(..., description="模块：pu 采购管理，ia 存货核算（也是登录子系统）")
    action: Literal["post", "unpost"] = Field(..., description="post 期初记账，unpost 取消期初记账")
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return self.module


class OpeningsPostOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    module: Scalar = Field(None, description="模块，pu 或 ia")
    action: Scalar = Field(None, description="post 或 unpost")
    posted: bool | None = Field(None, description="操作后的期初记账状态：记账后为 true，取消后为 false")
    opening_year: Scalar = Field(None, description="期初年度（该模块的启用年度）")
    start_date: Scalar = Field(None, description="该模块的启用日期，yyyy-MM-dd")
    counts: dict[str, Any] | None = Field(
        None,
        description="存货核算：脚本的诊断计数。记账有 st34_verified（取数的期初结存单行数）、subsidiary_m0_34、summary_m0、"
        "fifo_lines_qcass_skipped、gl_mend_p0、gl_mend_lt_start；取消有 summary_m0_deleted、subsidiary_m0_deleted、"
        "summary_item_m0_deleted、summary_m0_left、gl_mend_p0、bQCInput_true。采购没有",
    )


OPENINGS_ARAP_SUMMARY = "应收应付期初单据"
OPENINGS_ARAP_HELP = (
    "新增、删除、审核、弃审应收 / 应付期初单据（U8 应收款管理、应付款管理「期初余额」）。\n\n"
    "**用法**\n"
    "- create 收 partner、amount、account，可选 department、person、digest、currency、exch_rate\n"
    "- delete、verify、unverify 只收 id\n"
    "- 响应同应收单 / 应付单的新增、审核（type 为 ar_bill / ap_bill），另带 side、opening=true\n"
    "- 新增响应还带 start_date（启用日期）和 date（单据日期）\n"
    "- dry_run：事务里执行后回滚（rollback 模式），什么都不写入，返回 DryRunOut\n\n"
    "**规则**\n"
    "- 期初单据是只有表头的应收单 / 应付单（bStartFlag=1）\n"
    "- 单据日期固定为该模块启用日期的前一天，不收日期字段\n"
    "- amount 为负数时照 U8 存为正数并取反借贷方向\n"
    "- 审核照 U8 期初形态写往来明细：第 0 期，登记、审核日期为启用日前一天\n"
    "- 普通的 ar_bill / ap_bill 路由不处理期初单据\n\n"
    + _LIMITS
    + "**权限**\n"
    "- 功能权限：应收 AR0306，应付 AP0306\n\n"
    "**错误**\n"
    "- 400：字段组合不对，不访问 U8\n"
    "- 404 not_found：单据不存在\n"
    "- 409 state_mismatch：\n"
    "  - 模块启用的第一个月已经结账，不能修改期初单据\n"
    "  - 模块未启用，或不是期初单据\n"
    "  - 单据已审核，或单据未审核\n"
    "  - 单据已生成凭证、已核销或已有往来明细（不能弃审 / 删除）"
)
_ID_MAX = 2147483647
_AMOUNT_MAX = 1000000000000
# 只有 create 收的字段。
_CREATE_ONLY = ("partner", "amount", "account", "department", "person", "digest", "currency", "exch_rate")
_CREATE_NEED = ("partner", "amount", "account")


class OpeningsArapIn(CoAuth):
    side: Literal["ar", "ap"] = Field(..., description="ar 应收，ap 应付（也是登录子系统）")
    action: Literal["create", "delete", "verify", "unverify"] = Field(
        ..., description="create 新增，delete 删除，verify 审核，unverify 弃审"
    )
    id: StrictInt | None = Field(
        None,
        ge=1,
        le=_ID_MAX,
        description="期初单据主键（Ap_Vouch.Auto_ID）。delete、verify、unverify 必填，create 不收",
    )
    partner: str | None = Field(
        None, min_length=1, max_length=20, description="create 必填：客户编码（ar）或供应商编码（ap）"
    )
    amount: float | None = Field(
        None,
        ge=-_AMOUNT_MAX,
        le=_AMOUNT_MAX,
        allow_inf_nan=False,
        strict=True,
        description="create 必填：原币金额，非 0、最多两位小数；负数表示反方向余额（预收 / 预付）",
    )
    account: str | None = Field(
        None, min_length=1, max_length=40, description="create 必填：科目编码，须为启用年度本系统受控的末级科目"
    )
    department: str | None = Field(None, min_length=1, max_length=12, description="部门编码（末级）")
    person: str | None = Field(None, min_length=1, max_length=20, description="业务员编码")
    digest: str | None = Field(
        None, min_length=1, max_length=120, description="摘要；省略时为「期初应收」或「期初应付」"
    )
    currency: str | None = Field(None, min_length=1, max_length=8, description="币种名称；省略为本位币")
    exch_rate: float | None = Field(
        None,
        gt=0,
        le=1000000,
        allow_inf_nan=False,
        strict=True,
        description="汇率：本位币只能省略或为 1，外币必须给；本币金额 = round(amount 绝对值 × 汇率, 2)",
    )
    dry_run: DryRunFlag = False

    @field_validator("amount")
    @classmethod
    def _cents(cls, value: float | None) -> float | None:
        if value is None:
            return value
        if value == 0:
            raise ValueError("amount 不能为 0")
        if Decimal(str(value)).as_tuple().exponent < -2:
            raise ValueError("amount 最多两位小数")
        return value

    @model_validator(mode="after")
    def _shape(self) -> OpeningsArapIn:
        if self.action == "create":
            if self.id is not None:
                raise ValueError("新增期初单据不收 id")
            missing = [name for name in _CREATE_NEED if getattr(self, name) is None]
            if missing:
                raise ValueError("action=create 缺少字段 " + "、".join(missing))
            return self
        extra = [name for name in _CREATE_ONLY if getattr(self, name) is not None]
        if extra:
            raise ValueError(f"action={self.action} 只收 id，不收 " + "、".join(extra))
        if self.id is None:
            raise ValueError(f"action={self.action} 缺少单据 id")
        return self

    def audit_ref(self) -> str:
        return self.side


class OpeningsArapOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    type: Scalar = Field(None, description="ar_bill（应收单）或 ap_bill（应付单）")
    id: Scalar = Field(None, description="期初单据主键（Ap_Vouch.Auto_ID）")
    code: Scalar = Field(None, description="单据编号")
    state: dict[str, Any] | None = Field(None, description="审核状态（verified、verifier、verified_at）。删除时没有")
    side: Scalar = Field(None, description="ar 或 ap")
    opening: bool | None = Field(None, description="期初单据，总是 true")
    start_date: Scalar = Field(None, description="新增：模块启用日期，yyyy-MM-dd")
    date: Scalar = Field(None, description="新增：单据日期（启用日期前一天），yyyy-MM-dd")
    deleted: bool | None = Field(None, description="删除：表头已不存在则为 true")
