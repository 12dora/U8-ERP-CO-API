"""期间损益结转 /v1/co/gl/transfer/pnl、自定义转账 /v1/co/gl/transfer/custom 的请求和响应。

字段名与桥一致，响应放行桥多给的字段。两条都是第二级写入：只对桥配置为测试账套（testAccounts）的账套开放。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import DATE, PASS, Scalar

_GATE = (
    "默认关闭（桥未开 enableReplicatedWrites 时 403 feature_disabled），打开后只对 CO 桥配置为测试账套（testAccounts）的账套开放（含预演），其他账套 403 test_account_only，不登录 U8。"
    "fiscal_year 必须是登录日期（date）的年份，period 是 1 到 12；voucher_date 是凭证日期，缺省取该期间最后一天，"
    "必须在该期间内。功能权限同填制凭证（GL0201）；数据权限同总账查询：科目（开了「明细账查询权限控制到科目」时）、部门、"
    "人员、客户、供应商、项目受控时，取数和入账涉及的科目、辅助项有一个没有查询权限就 403 no_permission（预演同样，不返回金额）。"
    "取数只取已记账的期末余额（同 U8），所以要求：该期间总账未结账、本期没有未作废的未记账凭证，否则 409 state_mismatch。"
    "凭证照 U8 自动转账生成的样子标记：外部业务类型（coutsign）、外部业务号（coutno_id，GL 加 13 位）、附单据数 -1，"
    "制单系统为空；生成的凭证未审核、未记账，之后按 gl/vouchers/verify、gl/vouchers/post 审核、记账，"
    "要重做时作废后删除（gl/vouchers/void、gl/vouchers/delete）。"
    "dry_run 为 true 时算完、校验完停在凭证导入之前（validate 模式），返回 DryRunOut，"
    "要生成的凭证和分录在 detail.transfer.vouchers（每张：sign、digest、debit、credit、lines，期间损益另有 pack，"
    "自定义转账另有 tran_id），跳过的定义在 detail.transfer.skipped。"
    "exclude_existing 只能和 dry_run 一起用：余额里去掉本期已生成的同类结转凭证及其后的结转凭证，不查结账、未记账、重复三道闸门，"
    "用来与 U8 已生成的凭证逐行核对，去掉的凭证在 detail.transfer.excluded。"
    "U8 保存后自己提交；收到 504 outcome_unknown 时先用 gl/vouchers/list 核对再决定是否重试。"
)

PNL_SUMMARY = "期间损益结转"
PNL_HELP = (
    "期间损益结转（U8 总账「期间损益」）：按 U8 的期间损益结转定义，把损益类末级科目（带辅助核算的按辅助项）"
    "已记账的期末余额结平，差额转入本年利润科目。贷方性质的科目（收入）一张凭证、借方性质的（费用）一张，"
    "摘要「期间损益结转」，凭证类别取定义上的类别；余额为反方向的写成同一边的负数。"
    "定义里的科目不存在或不是末级时跳过该定义（响应和预演的 skipped）。本期已做过期间损益结转（有未作废的结转凭证）时 409；"
    "例外是只存了一张（收入或费用）：把它审核、记账后再调用，只补生成缺的那张，响应的 existing 列出已有的凭证。"
    + _GATE
)
CUSTOM_SUMMARY = "自定义转账"
CUSTOM_HELP = (
    "自定义转账（U8 总账「自定义转账」）：按 U8 的自定义转账定义，每个转账序号生成一张凭证；"
    "tran_id 只生成这一个序号，缺省生成全部。行的科目、方向取定义，金额按公式计算："
    "支持 QM(科目, 月|年, [借|贷], [辅助项])（已记账的期末余额，科目可以是非末级，按下级合计）、CE()（借贷差额）、"
    "数字和 + - * / 括号；用了别的函数（FS、JE、QC 等）返回 400 bad_request 并写明函数名，不猜。"
    "金额为 0 的行不写。按年取数（QM 年）的定义只在第 12 期生成。"
    "本期已生成（同期、外部业务类型「自定义转账」、摘要相同的未作废凭证）、科目不存在或不是末级、"
    "行科目要辅助核算而定义和公式都给不出、算出来全为 0 的定义跳过（skipped）；全部跳过时 409。"
    "不给 tran_id 时，后面的定义 QM 取数的科目与前面定义本次生成的分录科目重叠则 409（U8 按序号逐个结转、记账后再取数），"
    "请用 tran_id 逐个生成、审核、记账。" + _GATE
)


class _TransferIn(CoAuth):
    fiscal_year: StrictInt = Field(..., ge=1900, le=9999, description="会计年度，必须是登录日期（date）的年份")
    period: StrictInt = Field(..., ge=1, le=12, description="会计期间，1 到 12")
    voucher_date: str | None = Field(
        None, pattern=DATE, description="凭证日期 yyyy-MM-dd，缺省取该期间最后一天；必须在该期间内"
    )
    exclude_existing: StrictBool | None = Field(
        None, description="true：核对模式，余额里去掉本期已生成的同类结转凭证（只能和 dry_run 一起用）"
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _shape(self) -> _TransferIn:
        if self.exclude_existing and not self.dry_run:
            raise ValueError("exclude_existing 只能和 dry_run 一起用（只核对，不生成）")
        if self.voucher_date is not None and not self.voucher_date.startswith(f"{self.fiscal_year}-{self.period:02d}-"):
            raise ValueError("voucher_date 必须在 fiscal_year 年第 period 期内")
        return self

    def audit_ref(self) -> str:
        return f"{self.fiscal_year}-{self.period}"


class GlTransferPnlIn(_TransferIn):
    pass


class GlTransferCustomIn(_TransferIn):
    tran_id: str | None = Field(
        None, pattern=r"^[0-9A-Za-z]{1,20}$", description="只生成这一个自定义转账序号（U8 的转账序号，如 0001）；缺省生成全部"
    )

    def audit_ref(self) -> str:
        return f"{self.fiscal_year}-{self.period}" + (f"-{self.tran_id}" if self.tran_id else "")


class GlTransferVoucherOut(BaseModel):
    model_config = PASS
    sign: Scalar = Field(None, description="凭证类别字")
    period: Scalar = Field(None, description="会计期间")
    no: Scalar = Field(None, description="凭证号")
    lines: Scalar = Field(None, description="分录行数")
    out_no: Scalar = Field(None, description="外部业务号（coutno_id）")
    digest: Scalar = Field(None, description="摘要")
    debit: Scalar = Field(None, description="借方合计")
    credit: Scalar = Field(None, description="贷方合计")
    tran_id: Scalar = Field(None, description="自定义转账的转账序号")
    pack: Scalar = Field(None, description="期间损益：income（收入）或 expense（费用）")


class GlTransferOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    kind: Scalar = Field(None, description="pnl（期间损益结转）或 custom（自定义转账）")
    fiscal_year: Scalar = Field(None, description="会计年度")
    period: Scalar = Field(None, description="会计期间")
    voucher_date: Scalar = Field(None, description="凭证日期")
    out_sign: Scalar = Field(None, description="凭证上的外部业务类型（coutsign）：期间损益 或 自定义转账")
    count: Scalar = Field(None, description="生成的凭证张数")
    vouchers: list[GlTransferVoucherOut] | None = Field(None, description="生成的凭证")
    skipped: list[dict[str, Any]] | None = Field(None, description="跳过的定义：[{tran_id, account, reason}]")
    existing: list[dict[str, Any]] | None = Field(None, description="期间损益补生成时本期已有的期间损益凭证：[{sign, no, pack}]")
