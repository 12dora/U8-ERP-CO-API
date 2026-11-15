"""应收坏账处理 /v1/co/arap/bad_debt：坏账发生（9G）、坏账收回（9H）、计提坏账准备（9F），只对测试账套开放。
校验规则与桥的坏账请求解析一致；取消用 arap/process/cancel，制单用 arap/process/voucher（处理号 HZAR…）。"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from u8co_api.co_doctext import TEST_ONLY_GATE
from u8co_api.co_models import CoAuth
from u8co_api.co_models_arap_proc import DRY_ROLLBACK_NOTE, has_control
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

_ID_MAX = 2147483647
_AMOUNT_MAX = 1000000000000
_LINES_MAX = 50
_CODE_MAX = 20
_DEPT_MAX = 12
_DOC_MAX = 30
_TEXT_MAX = 120
_INVOICES = frozenset({"26", "27", "28", "29"})
# 坏账发生可选的单据：销售发票 26 / 27 / 28 / 29 和应收单 R0 到 R9（同 U8 的选单，不含 RZ 与收付款单）。
BAD_DEBT_TYPES = ("26", "27", "28", "29", *(f"R{n}" for n in range(10)))
# 各动作收的业务字段（登录字段与 dry_run 之外）。
_FIELDS = {
    "occur": frozenset({"customer", "currency", "digest", "dept", "person", "lines"}),
    "recover": frozenset({"customer", "receipt", "amount", "currency", "digest"}),
    "provision": frozenset(),
}
_REQUIRED = {"occur": ("customer", "lines"), "recover": ("customer", "receipt", "amount"), "provision": ()}
_AMOUNT_RULE = "amount 必须大于 0、不超过 1000000000000、最多两位小数"

BAD_DEBT_SUMMARY = "应收坏账发生 / 收回 / 计提"
BAD_DEBT_HELP = (
    "U8 应收款管理「坏账处理」：坏账发生、坏账收回、计提坏账准备。\n\n"
    "**用法**\n\n"
    "| action | 处理 | 处理方式 | 功能权限 |\n"
    "| --- | --- | --- | --- |\n"
    "| occur | 坏账发生 | 9G | AR050602 |\n"
    "| recover | 坏账收回 | 9H | AR050603 |\n"
    "| provision | 计提坏账准备 | 9F | AR050601 |\n\n"
    "- 处理号都是 HZAR + 13 位数字（U8 的 Ap_CancelNo 编号 HZ）\n"
    "- 只做应收（登录子系统 AR）；provision 不带业务字段\n" + DRY_ROLLBACK_NOTE + "\n"
    "**规则**\n"
    "- 处理日期就是登录日期 date：须在 U8 会计期间里、不早于应收启用日期、应收该月未结账\n"
    "- 须已设置该会计年度的坏账准备参数（应收款管理 › 设置 › 坏账准备）\n"
    "- U8 按最后一行参数记坏账准备余额；该行年度与 date 不符时 409，不改别的年度\n"
    "- 坏账发生（occur）：\n"
    "  - 单据须已审核、属于该客户和币种；外币各单据汇率须相同\n"
    "  - amount 不超过该单据（行）的正余额\n"
    "  - 每张单据行写一行贷方往来明细，冲减单据余额；坏账准备余额减去本币合计\n"
    "- 坏账收回（recover）：\n"
    "  - 收款单须属于该客户、只有一行应收款；amount 须等于它的全部余额（U8 整张一起消耗）\n"
    "  - 用 U8 的审核组件审核收款单（审核行贷方），再在收款单上记一行 9H 借方往来明细（科目同收款单行）\n"
    "  - 两者相抵，客户应收余额不变；坏账准备余额加上本币金额\n"
    "  - 9H 制单时一并回写收款单的审核行和表头凭证号\n"
    "- 计提（provision）：\n"
    "  - 按参数里的计提方法算应计坏账准备：1 应收余额百分比、2 账龄分析、3 销售收入百分比\n"
    "  - 销售收入取 date 所在年度 1 月 1 日到 date 已审核、非期初、未作废销售发票的本币价税合计\n"
    "  - U8 的取数窗口以界面为准\n"
    "  - 本次计提 = 应计 − 当前余额\n"
    "  - 只更新该年度的参数行（余额、累计计提、计提日期、处理号），不写往来明细\n"
    "  - 同一年度多次计提时 U8 只记最后一个处理号，只有最后一次能单独取消\n\n"
    "**限制**\n"
    "- 外币坏账发生暂不能用 arap/process/voucher 制单\n" + TEST_ONLY_GATE + "\n"
    "**权限**\n"
    "- 功能权限：见上表\n"
    "- 数据权限：按客户、部门、业务员\n\n"
    "**错误**\n"
    "- 404 not_found：客户、单据或收款单不存在\n"
    "- 409 state_mismatch：应收该月已结账、未设置坏账准备参数、不支持的计提方法\n"
    "- 409 state_mismatch：单据未审核或余额不足、币种或汇率不符\n"
    "- 409 state_mismatch：收款单已审核、已核销或金额不等\n"
    "- 409 state_mismatch：本次计提金额为 0\n"
    "- 409 u8_rejected：核对不符，已回滚\n\n"
    "**相关**\n"
    "- 取消：arap/process/cancel\n"
    "- 制单：arap/process/voucher"
)


def _text(value: str | None, name: str) -> str | None:
    if value is not None and (not value.strip() or has_control(value)):
        raise ValueError(f"{name} 必须是不含控制字符的非空文本")
    return value


def _cents(value: float | None) -> float | None:
    if value is not None and Decimal(str(value)).as_tuple().exponent < -2:
        raise ValueError(_AMOUNT_RULE)
    return value


class BadDebtLineIn(BaseModel):
    """坏账发生的一张单据（或发票的一行）及本次金额。"""

    model_config = ConfigDict(extra="forbid")
    type: str = Field(..., min_length=2, max_length=2, description="26 / 27 / 28 / 29 销售发票，R0 到 R9 应收单")
    id: str = Field(..., min_length=1, max_length=_DOC_MAX, description="单据号")
    line_id: int | None = Field(
        None, gt=0, le=_ID_MAX, description="发票表体行；省略按行主键从小到大依次分摊。应收单按整单，不能带"
    )
    amount: float = Field(
        ..., gt=0, le=_AMOUNT_MAX, allow_inf_nan=False, strict=True, description="本次坏账金额（原币），最多两位小数"
    )

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: float) -> float | None:
        return _cents(value)

    @field_validator("type")
    @classmethod
    def _type(cls, value: str) -> str:
        if value not in BAD_DEBT_TYPES:
            raise ValueError("type 只能是 26、27、28、29（销售发票）或 R0 到 R9（应收单）")
        return value

    @field_validator("id")
    @classmethod
    def _doc(cls, value: str) -> str | None:
        return _text(value, "id")

    @model_validator(mode="after")
    def _whole_bill(self) -> BadDebtLineIn:
        if self.line_id is not None and self.type not in _INVOICES:
            raise ValueError("应收单按整单处理，不能带 line_id")
        return self


class CoArapBadDebtIn(CoAuth):
    action: Literal["occur", "recover", "provision"] = Field(
        ..., description="occur 坏账发生（9G）、recover 坏账收回（9H）、provision 计提坏账准备（9F）"
    )
    customer: str | None = Field(None, min_length=1, max_length=_CODE_MAX, description="客户编码（occur、recover 必填）")
    currency: str | None = Field(None, min_length=1, max_length=20, description="币种名称；省略为本位币")
    digest: str | None = Field(None, max_length=_TEXT_MAX, description="摘要；省略或为空为「坏账发生」「坏账收回」")
    dept: str | None = Field(
        None, min_length=1, max_length=_DEPT_MAX, description="部门编码（只用于 occur）：末级、未停用"
    )
    person: str | None = Field(
        None, min_length=1, max_length=_CODE_MAX, description="业务员编码（只用于 occur）：未停用"
    )
    lines: list[BadDebtLineIn] | None = Field(
        None, min_length=1, max_length=_LINES_MAX, description="坏账发生的单据，1 到 50 项（occur 必填）"
    )
    receipt: str | None = Field(
        None, min_length=1, max_length=_DOC_MAX, description="未审核、未核销的收款单（48）单号（recover 必填）"
    )
    amount: float | None = Field(
        None,
        gt=0,
        le=_AMOUNT_MAX,
        allow_inf_nan=False,
        strict=True,
        description="收回金额（原币），须等于收款单的全部余额（recover 必填）",
    )
    dry_run: DryRunFlag = False

    @field_validator("customer", "currency", "dept", "person", "receipt")
    @classmethod
    def _texts(cls, value: str | None, info) -> str | None:
        return _text(value, info.field_name)

    @field_validator("digest")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        # 空或全空白同省略（桥同样按缺省摘要处理），不往桥上送。
        if value is None or not value.strip():
            return None
        return _text(value, "digest")

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: float | None) -> float | None:
        return _cents(value)

    @model_validator(mode="after")
    def _by_action(self) -> CoArapBadDebtIn:
        allowed = _FIELDS[self.action]
        for name in sorted(_FIELDS["occur"] | _FIELDS["recover"]):
            if getattr(self, name) is not None and name not in allowed:
                raise ValueError(f"action 为 {self.action} 时不能带 {name}")
        for name in _REQUIRED[self.action]:
            if getattr(self, name) is None:
                raise ValueError(f"action 为 {self.action} 时必须给 {name}")
        if self.lines is not None:
            _check_lines(self.lines)
        return self

    def audit_ref(self) -> str:
        # 审计动作已带 action（co:arap/bad_debt:occur#…），这里只写客户；计提写日期。
        return self.customer if self.customer else "date:" + (self.date or "")


def _check_lines(lines: list[BadDebtLineIn]) -> None:
    """同一单据行不能重复；同一单据不能既按行又整单。"""
    seen: set[tuple[str, str, int]] = set()
    for line in lines:
        doc = (line.type, line.id.strip())
        key = (*doc, line.line_id or 0)
        if key in seen:
            raise ValueError("lines 里有重复的单据行")
        if any(old[:2] == doc and (old[2] == 0 or key[2] == 0) for old in seen):
            raise ValueError("同一张单据不能既整单又按行处理")
        seen.add(key)


class BadDebtRowOut(BaseModel):
    model_config = PASS
    type: Scalar = Field(None, description="U8 单据类型代码")
    id: Scalar = Field(None, description="单据号")
    doc_id: Scalar = Field(None, description="单据主键")
    line_id: Scalar = Field(None, description="发票行；整单时省略")
    amount: Scalar = Field(None, description="本次坏账金额（原币）")
    amount_native: Scalar = Field(None, description="本次坏账金额（本币）")
    remaining: Scalar = Field(None, description="处理后的余额（原币）")


class CoArapBadDebtOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    acc: Scalar = Field(None, description="账套号")
    action: Scalar = Field(None, description="occur、recover 或 provision")
    cancel_no: Scalar = Field(None, description="处理号（HZAR + 13 位数字），取消、制单要用")
    style: Scalar = Field(None, description="处理方式：9G 坏账发生、9H 坏账收回、9F 计提坏账准备")
    style_name: Scalar = Field(None, description="处理方式名称：坏账发生、坏账收回、计提坏账")
    date: Scalar = Field(None, description="处理日期")
    customer: Scalar = Field(None, description="客户编码")
    currency: Scalar = Field(None, description="币种")
    amount: Scalar = Field(None, description="本次金额：坏账发生、收回为原币合计，计提为本次计提（本币）")
    remain_before: Scalar = Field(None, description="处理前的坏账准备余额（本币）")
    remain_after: Scalar = Field(None, description="处理后的坏账准备余额（本币）")
    rows: list[BadDebtRowOut] | None = Field(None, description="坏账发生：每张单据行的本次金额和余额")
    base: Scalar = Field(None, description="计提：计提基数（应收余额、账龄合计或销售收入）")
    rate: Scalar = Field(None, description="计提：计提比率；账龄分析法省略")
    target: Scalar = Field(None, description="计提：应计坏账准备")
    method_name: Scalar = Field(None, description="计提：计提方法名称（应收余额百分比法、账龄分析法、销售收入百分比法）")
