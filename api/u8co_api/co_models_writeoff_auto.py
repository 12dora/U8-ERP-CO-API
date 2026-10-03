"""自动核销（arap/writeoff/auto）。校验规则与桥的 ArapAutoWriteoffReq 一致，响应放行桥多给的字段。"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_writeoff import ReceiptType, TargetType, WriteoffItemOut, WriteoffReceiptOut

_FORBID = ConfigDict(extra="forbid")
_OUT = ConfigDict(extra="allow")
_ID_MAX = 2147483647
_AMOUNT_MAX = 10**12
_MAX_TARGETS = 200
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_FLAG = {
    "ar_receipt": "AR",
    "ap_payment": "AP",
    "sale_invoice": "AR",
    "ar_bill": "AR",
    "purchase_invoice": "AP",
    "ap_bill": "AP",
}

AUTO_SUMMARY = "应收 / 应付自动核销"
AUTO_HELP = (
    "对一个往来单位（partner，必填，不做整个账套）按 U8 在核销规则 iHxRule=0 时的规则自动配对核销：有未核销余额的收款单"
    "（付款单）行按单据日期、主键、行先后排，有余额的销售发票、应收单（采购发票、应付单）行按单据日期、单号、行先后排，"
    "先进先出，每次取两边余额的较小者，一边用完换下一行，直到任一边用完或到 max_amount。只取已审核、"
    "不受审批流控制、没有网络锁、单据日期不晚于 date_to（缺省登录日期，不能晚于它）的单据；外币只在同币种的"
    "收付款单行和单据行之间配对（单据汇率不比，按收付款单汇率核销，汇兑差额留给期末汇兑损益），max_amount 按原币累计。预收 / 预付行缺省不参加，include_prepay 为 true 时参加。"
    "receipt 限定一张收付款单（可带 line_id），targets 限定若干张单据（1 到 200 项，只带 type、id），类型须与 flag 一致，否则 400。"
    "每行收付款单一批，走与 arap/writeoff 相同的 U8 核销组件 Save（一个 close 下挂若干 vouch）和同一道闸门，"
    "全部批在一个事务里，任何一批失败整体回滚、返回 U8 原文或闸门的 409；提交后逐批回读，不符 409 state_mismatch、"
    "回读失败 504 outcome_unknown。不调用 U8 的 cLsCancel.AutoCancel（直接调用返回成功却什么都不写）。"
    "账套设置了按规则核销（iHxRule 不为 0 或 bAPAutoCancelWithHxRule）时 409「自动核销暂不支持」。"
    "一次最多 20 行收付款单、200 行单据，超出 409，请用 receipt、targets 或 date_to 缩小范围。"
    "dry_run 为 true 时只返回计划 plan（每批的收付款单行和分到的单据行、金额），不写；计划为空时 200，batches 为空、total 为 0。"
    "每批的核销号可用 arap/writeoff/cancel 逐个取消。功能权限：自动核销 AR050202 / AP050202；数据权限同 arap/writeoff。"
)


class AutoReceiptIn(BaseModel):
    model_config = _FORBID
    type: ReceiptType = Field(..., description="收款单 ar_receipt（AR）或付款单 ap_payment（AP）")
    id: int = Field(..., gt=0, le=_ID_MAX, description="收付款单主键 Ap_CloseBill.iID")
    line_id: int | None = Field(None, gt=0, le=_ID_MAX, description="只核这一行 Ap_CloseBills.ID")


class AutoTargetIn(BaseModel):
    model_config = _FORBID
    type: TargetType = Field(..., description="sale_invoice、ar_bill（AR）或 purchase_invoice、ap_bill（AP）")
    id: int = Field(..., gt=0, le=_ID_MAX, description="单据主键（SBVID、PBVID 或 Ap_Vouch.Auto_ID）")


class CoWriteoffAutoIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统")
    partner: str = Field(..., min_length=1, max_length=20, description="客户（AR）或供应商（AP）编码 cDwCode")
    date_to: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="单据日期上限，缺省登录日期")
    receipt: AutoReceiptIn | None = Field(None, description="只用这张收付款单")
    targets: list[AutoTargetIn] | None = Field(
        None, min_length=1, max_length=_MAX_TARGETS, description="只核销这些单据，1 到 200 项"
    )
    max_amount: float | None = Field(
        None, gt=0, le=_AMOUNT_MAX, allow_inf_nan=False, strict=True, description="本次核销合计上限（原币累计），最多两位小数"
    )
    dry_run: StrictBool = Field(False, description="true 只返回计划，不写")
    include_prepay: StrictBool | None = Field(None, description="true 时预收 / 预付行（bPrePay=1）也参加，缺省不参加")

    @field_validator("partner")
    @classmethod
    def _partner(cls, value: str) -> str:
        if value.strip() != value or value == "" or _CONTROL.search(value):
            raise ValueError("partner 无效")
        return value

    @field_validator("max_amount")
    @classmethod
    def _cents(cls, value: float | None) -> float | None:
        if value is not None and Decimal(str(value)).as_tuple().exponent < -2:
            raise ValueError("max_amount 最多两位小数")
        return value

    @model_validator(mode="after")
    def _same_side(self) -> CoWriteoffAutoIn:
        if self.receipt is not None and _FLAG[self.receipt.type] != self.flag:
            raise ValueError("receipt.type 与 flag 不一致")
        seen: set[tuple[str, int]] = set()
        for item in self.targets or ():
            if _FLAG[item.type] != self.flag:
                raise ValueError("targets[].type 与 flag 不一致")
            if (item.type, item.id) in seen:
                raise ValueError("targets 里有重复的单据")
            seen.add((item.type, item.id))
        if self.date_to is not None and self.date is not None and self.date_to > self.date:
            raise ValueError("date_to 不能晚于核销日期 date")
        return self

    def audit_ref(self) -> str:
        return f"{self.flag}:{self.partner}"


class AutoBatchOut(BaseModel):
    model_config = _OUT
    cancel_no: str = Field(description="这一批的 U8 核销号（HXAR… / HXAP…）")
    receipt: WriteoffReceiptOut
    items: list[WriteoffItemOut]
    amount: float = Field(description="这一批的核销合计")


class AutoPlanOut(BaseModel):
    model_config = _OUT
    receipt: dict = Field(description="收付款单行：type、id、line_id、code、date、remaining（核销前余额）")
    targets: list[dict] = Field(description="分到的单据行：type、id、line_id、code、date、balance（分配前余额）、amount")
    amount: float = Field(description="这一批的计划合计")


class CoWriteoffAutoOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否成功（计划为空也是 true）")
    acc: str | None = Field(None, description="账套号")
    flag: str = Field(description="AR 或 AP")
    partner: str = Field(description="往来单位编码")
    dry_run: bool | None = Field(None, description="试算时为 true")
    batches: list[AutoBatchOut] | None = Field(None, description="已核销的批（执行时）")
    plan: list[AutoPlanOut] | None = Field(None, description="计划（dry_run 时）")
    total: float = Field(description="核销（或计划）合计")
