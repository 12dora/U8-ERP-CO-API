"""应收 / 应付核销（arap/writeoff）和取消核销（arap/writeoff/cancel）。校验规则与桥的 ArapWriteoffReq、ArapUnwriteoffReq 一致。"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag

_FORBID = ConfigDict(extra="forbid")
# 响应模型保留桥多给的字段（co/bridge/CHECKLIST.md「响应与错误码」）。
_OUT = ConfigDict(extra="allow")
_ID_MAX = 2147483647
_AMOUNT_MAX = 10**12
_MAX_ITEMS = 50

ReceiptType = Literal["ar_receipt", "ap_payment"]
TargetType = Literal["sale_invoice", "ar_bill", "purchase_invoice", "ap_bill"]
_FLAG = {
    "ar_receipt": "AR",
    "ap_payment": "AP",
    "sale_invoice": "AR",
    "ar_bill": "AR",
    "purchase_invoice": "AP",
    "ap_bill": "AP",
}
_BILLS = frozenset({"ar_bill", "ap_bill"})

WRITEOFF_SUMMARY = "应收 / 应付核销"
WRITEOFF_HELP = (
    "收款单（付款单）的一行对销售发票、应收单（采购发票、应付单）核销，走 U8 应收应付的核销组件 U8ApCancel.cLsCancel.Save，"
    "在桥的事务里。receipt.type 为 ar_receipt 时 items 只能是 sale_invoice / ar_bill，为 ap_payment 时只能是 "
    "purchase_invoice / ap_bill，否则 400。receipt.id 是 Ap_CloseBill.iID，receipt.line_id 是 Ap_CloseBills.ID，"
    "收付款单只有一行有未核销余额时可省略；发票的 line_id 是表体行（销售发票 SaleBillVouchs.AutoID、采购发票 "
    "PurBillVouchs.ID），只有一行有余额时可省略；应收单、应付单按整单核销，不能带 line_id。items 1 到 50 项，"
    "同一单据行不能重复；amount 大于 0、最多两位小数。核销日期就是登录日期 date（缺省今天）。"
    "400 bad_request：字段、类型、数量、金额不合法（同上）；line_id 不属于该单据「明细行不存在」；没给 receipt.line_id 而收付款单"
    "有多行未核销余额，或没给发票 line_id 而发票有多行未核销余额（请指定 line_id）。"
    "外币：各单据须与收付款单同币种，否则 409 state_mismatch「币种与收付款单不一致」；单据汇率可以不同，"
    "同 U8 一律按收付款单的汇率核销，汇兑差额留给期末汇兑损益（本接口不做）；amount 是原币，本币按收付款单汇率折算"
    "（四舍五入到分，尾差并到第一项）。"
    "预收 / 预付行（bPrePay=1）同样可以核销，U8 若另插收付款单行，核对时一并计入。"
    "桥在事务里带锁检查：收付款单存在且已审核、该行有未核销余额；各单据已应收（应付）审核、往来单位与收付款单相同、"
    "该行未核销余额不小于 amount；amount 合计不超过收付款单该行余额；核销日期不早于收付款单和各单据的日期、不早于应收（应付）"
    "系统启用日期；核销日期所在的 U8 会计期间（UA_Period，不一定是自然月）应收（应付）未结账；采购发票没有被网络锁定。"
    "单据不存在 404；受审批流控制的收付款单或单据 409 workflow_enabled；其余拒绝 409 state_mismatch；U8 拒绝时 409 u8_rejected，"
    "带回 U8 原文。成功后在同一事务里核对余额和新核销号，提交后在新连接上回读本核销号的核销行：不符 409 state_mismatch"
    "（已提交，不要重投），回读失败 504 outcome_unknown（先用 reports/arap_detail 或读取单据核对，不要盲目重试）；"
    "事务被数据库回滚（如死锁）503 u8_unavailable（未写入，可以重试）。"
    "功能权限：手工核销 AR050201 / AP050201，或选择收款 AR0503 / 选择付款 AP0503；数据权限按收付款单和每张单据的往来单位、"
    "部门、业务员。撤销用 arap/writeoff/cancel。"
)

CANCEL_SUMMARY = "取消应收 / 应付核销"
CANCEL_HELP = (
    "按核销号（arap/writeoff 返回的 cancel_no，或 U8 里做的核销的 Ar_Detail / Ap_Detail.cCancelNo）整批取消一次核销，"
    "相当于 U8 应收（应付）款管理「其他处理 → 取消操作」里取消一条核销。U8 这一步没有可调用的组件（界面自己跑 SQL），"
    "桥按 U8 界面执行的 SQL 写（实测核对），在一个事务里做：加回收付款单行余额、应收单 / 应付单余额、发票累计核销（销售发票经 U8 的回写组件），"
    "收付款单全部回到未核销时清核销人，删掉该核销号的核销行；提交前核对每个余额都精确回到核销前。"
    "flag 为 AR 或 AP（也是登录子系统），cancel_no 为 HXAR / HXAP 后接数字，前缀须与 flag 一致，否则 400。"
    "核销号不存在 404。外币核销可以取消（整批同一币种、同一汇率，即收付款单的汇率）。409 state_mismatch：核销已制单（请先删除凭证）；"
    "该核销号不是一张收付款单对发票、应收应付单的本侧核销（涉及合同、收付款单之间对冲、其他单据类型、跨期间、币种或汇率不一致），"
    "请在 U8 客户端取消；"
    "核销所在期间应收（应付）已结账；收付款单或单据在本次核销之后还有核销、汇兑损益、坏账等处理（先取消后面的）；"
    "应收一侧账套里有未审核的收款单（U8 同样不让取消）；采购发票被网络锁定。核对不符回滚、409 u8_rejected；"
    "提交后在新连接上确认核销行已不在，读不出 504 outcome_unknown，仍在 409 state_mismatch（已提交，不要重投）。"
    "功能权限：取消操作 AR0807 / AP0807；数据权限同核销。"
)


class WriteoffReceiptIn(BaseModel):
    model_config = _FORBID
    type: ReceiptType = Field(..., description="收款单 ar_receipt 或付款单 ap_payment")
    id: int = Field(..., gt=0, le=_ID_MAX, description="收付款单主键 Ap_CloseBill.iID")
    line_id: int | None = Field(
        None, gt=0, le=_ID_MAX, description="收付款单表体行 Ap_CloseBills.ID；只有一行有未核销余额时可省略"
    )


class WriteoffItemIn(BaseModel):
    model_config = _FORBID
    type: TargetType = Field(
        ..., description="被核销单据：sale_invoice、ar_bill（应收）或 purchase_invoice、ap_bill（应付）"
    )
    id: int = Field(..., gt=0, le=_ID_MAX, description="单据主键（SBVID、PBVID 或 Ap_Vouch.Auto_ID）")
    line_id: int | None = Field(
        None, gt=0, le=_ID_MAX, description="发票表体行；只有一行有余额时可省略。应收单、应付单不能带"
    )
    amount: float = Field(
        ..., gt=0, le=_AMOUNT_MAX, allow_inf_nan=False, strict=True, description="本次核销金额（原币，即收付款单的币种），最多两位小数"
    )

    @field_validator("amount")
    @classmethod
    def _cents(cls, value: float) -> float:
        if Decimal(str(value)).as_tuple().exponent < -2:
            raise ValueError("amount 最多两位小数")
        return value

    @model_validator(mode="after")
    def _whole_bill(self) -> WriteoffItemIn:
        if self.type in _BILLS and self.line_id is not None:
            raise ValueError("应收单、应付单按整单核销，不能带 line_id")
        return self


class CoWriteoffIn(CoAuth):
    receipt: WriteoffReceiptIn = Field(..., description="收付款单及其一行")
    items: list[WriteoffItemIn] = Field(
        ..., min_length=1, max_length=_MAX_ITEMS, description="被核销的单据行，1 到 50 项"
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _same_side(self) -> CoWriteoffIn:
        flag = _FLAG[self.receipt.type]
        seen: set[tuple[str, int, int | None]] = set()
        for item in self.items:
            if _FLAG[item.type] != flag:
                side = "收款单只能核销销售发票和应收单" if flag == "AR" else "付款单只能核销采购发票和应付单"
                raise ValueError(side)
            key = (item.type, item.id, item.line_id)
            if key in seen:
                raise ValueError("items 里有重复的单据行")
            seen.add(key)
        return self

    def audit_ref(self) -> str:
        return f"{self.receipt.type}:{self.receipt.id}"


class WriteoffReceiptOut(BaseModel):
    model_config = _OUT
    type: str = Field(description="收付款单类型")
    id: int = Field(description="Ap_CloseBill.iID")
    line_id: int = Field(description="实际核销的行 Ap_CloseBills.ID")
    code: str | None = Field(None, description="收付款单号")
    remaining: float = Field(description="该行核销后的未核销余额")


class WriteoffItemOut(BaseModel):
    model_config = _OUT
    type: str = Field(description="单据类型")
    id: int = Field(description="单据主键")
    line_id: int | None = Field(None, description="实际核销的发票行；应收单、应付单省略")
    code: str | None = Field(None, description="单据号")
    amount: float = Field(description="本次核销金额")
    remaining: float = Field(description="该行核销后的未核销余额")


class CoWriteoffOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已核销")
    acc: str | None = Field(None, description="账套号")
    cancel_no: str = Field(description="U8 核销号（Ar_Detail / Ap_Detail.cCancelNo，HXAR… / HXAP…）")
    date: str = Field(description="核销日期（登录日期）")
    receipt: WriteoffReceiptOut
    items: list[WriteoffItemOut]


class CoWriteoffCancelIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统")
    cancel_no: str = Field(
        ..., pattern=r"^HX(AR|AP)[0-9]{1,20}$", description="核销号（HXAR… / HXAP…），前缀须与 flag 一致"
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _same_flag(self) -> CoWriteoffCancelIn:
        if self.cancel_no[2:4] != self.flag:
            raise ValueError("cancel_no 与 flag 不一致（HXAR 是应收、HXAP 是应付）")
        return self

    def audit_ref(self) -> str:
        return self.cancel_no


class CancelLineOut(BaseModel):
    model_config = _OUT
    line_id: int = Field(description="收付款单表体行 Ap_CloseBills.ID")
    amount: float = Field(description="加回的金额")
    remaining: float = Field(description="取消后的未核销余额")


class CancelReceiptOut(BaseModel):
    model_config = _OUT
    type: str = Field(description="收付款单类型 ar_receipt / ap_payment")
    id: int = Field(description="Ap_CloseBill.iID")
    line_id: int | None = Field(None, description="只涉及一行时的行 Ap_CloseBills.ID")
    code: str | None = Field(None, description="收付款单号")
    remaining: float | None = Field(None, description="只涉及一行时，该行取消后的未核销余额")
    lines: list[CancelLineOut] = Field(default_factory=list, description="涉及的每一行")


class CoWriteoffCancelOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已取消")
    acc: str | None = Field(None, description="账套号")
    cancel_no: str = Field(description="已取消的核销号")
    flag: str = Field(description="AR 或 AP")
    receipt: CancelReceiptOut
    items: list[WriteoffItemOut] = Field(description="被核销单据的行：amount 是加回的金额，remaining 是取消后的未核销余额")
