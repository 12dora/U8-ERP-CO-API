"""期间损益结转 /v1/co/gl/transfer/pnl、自定义转账 /v1/co/gl/transfer/custom 的请求和响应。

字段名与桥一致，响应放行桥多给的字段。两条都是第二级写入：只对桥配置为测试账套（testAccounts）的账套开放。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import DATE, PASS, Scalar

_USAGE = (
    "- 跳过的定义在 detail.transfer.skipped\n"
    "- exclude_existing（只和 dry_run 一起用）：余额里去掉本期已生成的同类结转凭证及其后的结转凭证\n"
    "- exclude_existing 不查结账、未记账、重复三道闸门，用来与 U8 已生成的凭证逐行核对\n"
    "- exclude_existing 去掉的凭证在 detail.transfer.excluded\n"
    "- 生成的凭证未审核、未记账：之后用 gl/vouchers/verify、gl/vouchers/post 审核、记账\n"
    "- 要重做时作废后删除：gl/vouchers/void、gl/vouchers/delete\n\n"
)
_RULES = (
    "- 取数只取已记账的期末余额（同 U8）\n"
    "- 凭证照 U8 自动转账标记：外部业务类型（coutsign）、外部业务号（coutno_id，GL 加 13 位）\n"
    "- 附单据数 -1，制单系统为空\n\n"
)
_TAIL = (
    "**限制**\n"
    "- 默认关闭：桥未开 enableReplicatedWrites 时 403 feature_disabled\n"
    "- 打开后只对桥配置的测试账套（testAccounts）开放（含预演）；其他账套 403 test_account_only，不登录 U8\n\n"
    "**权限**\n"
    "- 功能权限同填制凭证（GL0201）\n"
    "- 数据权限同总账查询：科目（开了「明细账查询权限控制到科目」时）、部门、人员、客户、供应商、项目\n"
    "- 取数和入账涉及的科目、辅助项有一个没有查询权限就 403 no_permission（预演同样，不返回金额）\n\n"
)
_ERRORS = (
    "- 409 state_mismatch：该期间总账已结账，或本期有未作废的未记账凭证\n"
    "- 504 outcome_unknown：U8 保存后已自己提交，先用 gl/vouchers/list 核对再决定是否重试"
)


def _help(first: str, usage: str, rules: str, errors: str) -> str:
    # 各节顺序：用法、规则、限制、权限、错误；两条转账共用的条目接在各自条目之后。
    return (
        f"{first}\n\n**用法**\n{usage}{_USAGE}**规则**\n{rules}{_RULES}{_TAIL}**错误**\n{errors}{_ERRORS}"
    )


_DRY = "- dry_run：算完、校验完停在凭证导入之前（validate 模式），返回 DryRunOut\n"

PNL_SUMMARY = "期间损益结转"
PNL_HELP = _help(
    "按 U8 的期间损益结转定义（总账「期间损益」）把损益类末级科目结平，差额转入本年利润科目。",
    _DRY + "- 预演要生成的凭证在 detail.transfer.vouchers：sign、digest、debit、credit、lines、pack\n",
    "- 结平已记账的期末余额；带辅助核算的科目按辅助项结转\n"
    "- 收入（贷方性质）一张凭证、费用（借方性质）一张，摘要「期间损益结转」\n"
    "- 凭证类别取定义上的类别；反方向余额写成同一边的负数\n"
    "- 定义里的科目不存在或不是末级时跳过该定义（skipped）\n"
    "- 本期只存了收入或费用一张时：先审核、记账它，再调用只补生成缺的那张\n"
    "- 补生成时响应的 existing 列出已有的凭证\n",
    "- 409：本期已做过期间损益结转（有未作废的结转凭证），只存一张的补生成除外\n",
)
CUSTOM_SUMMARY = "自定义转账"
CUSTOM_HELP = _help(
    "按 U8 的自定义转账定义（总账「自定义转账」）生成凭证，每个转账序号一张。",
    _DRY + "- 预演要生成的凭证在 detail.transfer.vouchers：sign、digest、debit、credit、lines、tran_id\n"
    "- 定义之间有取数依赖时，用 tran_id 逐个生成、审核、记账\n",
    "- 行的科目、方向取定义，金额按公式计算\n"
    "- 支持 QM(科目, 月|年, [借|贷], [辅助项])：已记账的期末余额，非末级科目按下级合计\n"
    "- 支持 CE()（借贷差额）、数字和 + - * / 括号\n"
    "- 金额为 0 的行不写；按年取数（QM 年）的定义只在第 12 期生成\n"
    "- 跳过（skipped）：本期已生成（同期、外部业务类型「自定义转账」、摘要相同的未作废凭证）\n"
    "- 跳过：科目不存在或不是末级、行科目要辅助核算而定义和公式都给不出、算出来全为 0\n",
    "- 400 bad_request：用了别的函数（FS、JE、QC 等），写明函数名，不猜\n"
    "- 409：全部定义都被跳过\n"
    "- 409：不给 tran_id 时，后面定义 QM 取数的科目与前面定义本次生成的分录科目重叠"
    "（U8 按序号逐个结转、记账后再取数）\n",
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
