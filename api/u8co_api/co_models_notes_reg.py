"""应收票据登记（notes/create）与删除（notes/delete）的请求和响应；应付票据（flag AP）只对测试账套开放。
校验规则与桥的 NotesRegReq 一致。"""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from u8co_api.co_doctext import TEST_ONLY_GATE
from u8co_api.co_models import _ID_MAX, CoAuth
from u8co_api.co_models_arap_proc import _cents, has_control
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar
from u8co_api.co_models_notes_proc import _SERIAL_MAX, _range_amount
from u8co_api.errors import ApiError

_AMOUNT_MAX = 1000000000000
_TEXT_FIELDS = (
    "note_no",
    "settle_code",
    "customer",
    "vendor",
    "drawer",
    "drawer_bank",
    "dept",
    "person",
    "receiver",
    "receive_bank",
    "receive_account",
    "km",
    "note_km",
    "digest",
)
# 往来单位字段按 flag：应收票据 customer（交票客户），应付票据 vendor（收票供应商）。
_PARTNER = {"AR": ("customer", "vendor"), "AP": ("vendor", "customer")}
_PARTNER_TITLE = {"customer": ("客户编码", "应收票据（flag AR）"), "vendor": ("供应商编码", "应付票据（flag AP）")}
_AP_TIER2 = "- 应付票据（flag AP）是第二级写入，只在测试账套上验证过\n" + TEST_ONLY_GATE

CREATE_SUMMARY = "票据登记"
CREATE_HELP = (
    "登记一张应收（应付）票据并生成对应的收款单（付款单），对应 U8 应收（应付）款管理「票据管理」的登记。\n\n"
    "**用法**\n"
    "- 应收票据给 customer（交票客户），应付票据给 vendor，另一个不收\n"
    "- sub_start / sub_end 成对给出时登记分包票据，区间张数 × 0.01 须等于票面 amount；不给为不分包票据\n"
    "- dry_run 为 true：真实写入、核对后回滚（rollback 模式）\n\n"
    "**规则**\n"
    "- 桥在一个事务里照 U8 登记的结果写票据（AP_Note）和收款单，核对后提交；任何一步失败整笔回滚\n"
    "- 不调用 U8 的票据组件 NoteManageAR（无界面时会挂起），不建条码档案\n"
    "- 收款单（48）：结算方式 = 票据类型，表头科目 = 票据科目，表体一行应收款\n"
    "- 收款单标成票据来源（cSrcFlag C、来源 50、票据号），票据回写收款单主键\n"
    "- 签发、收票日期不晚于登录日期 date；到期日、收票日期不早于签发日期\n"
    "- 收票日期所在期间应收未结账\n"
    "- 生成的收款单未审核（receipt_verified 为 false），票据处理（notes/process）前先审核\n"
    "- 分包票据：标成分包，写子票区间和一行可用区间（Ap_Note_AvailRange）\n"
    "- 分包票据的收款单票据号写成「票据号-起号-止号」\n"
    "- 应付票据生成付款单（49），表体一行应付款，科目缺省取应付科目 kzkm；响应的 receipt_id / receipt_code 是付款单\n"
    "- 应付票据：票据 cFlag AP、cLink AP50+票据号；pjkm 未设置时必须给 note_km（如 2201 的末级）\n\n"
    "**限制**\n" + _AP_TIER2 + "\n"
    "**权限**\n"
    "- 应收票据：功能权限 AR0504；数据权限按客户、部门、业务员\n"
    "- 应付票据：功能权限 AP0504；数据权限按供应商\n\n"
    "**错误**\n"
    "- 409 state_mismatch：票据号已存在、期间已结账、没有设置票据科目等\n\n"
    "**相关**\n"
    "- 审核收款单：vouchers/verify（type ar_receipt）\n"
    "- 票据处理：notes/process"
)
DELETE_SUMMARY = "删除票据"
DELETE_HELP = (
    "删除一张登记后还没有处理的票据，连同登记生成的收款单（应付票据为付款单）。\n\n"
    "**用法**\n"
    "- dry_run 为 true：真实删除、核对后回滚（rollback 模式）\n\n"
    "**规则**\n"
    "- 票据须不是期初、没有结算 / 贴现 / 背书等处理记录、没换过票、余额等于票面\n"
    "- 登记生成的收款单须未审核、未制单、未核销\n"
    "- 分包票据须可用区间仍是登记时的整段，连同可用区间一起删\n"
    "- 桥照 U8 DeletePJ 的做法，在一个事务里删除收款单、保证金、付款申请明细和票据\n"
    "- 核对后提交，任何一步失败整笔回滚\n\n"
    "**限制**\n" + _AP_TIER2 + "\n"
    "**权限**\n"
    "- 功能权限：应收票据 AR2403，应付票据 AP2403\n\n"
    "**错误**\n"
    "- 404 not_found：票据不存在"
)


def _day(value: str, name: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{name} 必须是 yyyy-MM-dd") from None


def split_mismatch(start: int, end: int, face: Decimal) -> str:
    """子票区间与票面不符的说明，与桥 NoteSplit.Parse 同文（API 层直接 400，不用笼统的「请求参数无效」）。"""
    count = end - start + 1
    return f"子票区间与金额不符：区间 {count} 张 = {_range_amount(start, end):.2f} 元，票面 {face:.2f}（每张子票 0.01 元）"


class CoNoteCreateIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(
        ..., description="AR 应收票据、AP 应付票据（第二级写入，默认关闭，打开后只对测试账套开放；应付票据写入只在测试账套上验证过），也是登录子系统"
    )
    note_no: str = Field(..., min_length=1, max_length=60, description="票据号（cVouchID），不能已存在")
    settle_code: str = Field(..., min_length=1, max_length=3, description="结算方式（票据类型），如 301 银行承兑、302 商业承兑")
    amount: float = Field(
        ..., gt=0, le=_AMOUNT_MAX, allow_inf_nan=False, strict=True, description="票面金额（本位币），最多两位小数"
    )
    sign_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="签发日期，不晚于登录日期")
    receipt_date: str = Field(
        ..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="收票日期（= 收付款单日期），不晚于登录日期"
    )
    expire_date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="到期日，不早于签发日期")
    customer: str | None = Field(
        None, min_length=1, max_length=20, description="应收票据必填：交票客户编码（票据的 cEndorser，收款单的客户）"
    )
    vendor: str | None = Field(
        None, min_length=1, max_length=20, description="应付票据必填：收票供应商编码（票据的 cEndorser，付款单的供应商）"
    )
    drawer: str | None = Field(None, min_length=1, max_length=60, description="出票人名称；省略为往来单位名称")
    drawer_bank: str | None = Field(None, min_length=1, max_length=100, description="出票人开户银行")
    dept: str = Field(..., min_length=1, max_length=12, description="部门编码（末级）")
    person: str | None = Field(None, min_length=1, max_length=20, description="业务员编码")
    receiver: str = Field(..., min_length=1, max_length=50, description="收款人名称")
    receive_bank: str | None = Field(None, min_length=1, max_length=100, description="收款人开户银行")
    receive_account: str | None = Field(None, min_length=1, max_length=50, description="收款人账号")
    km: str | None = Field(
        None, min_length=1, max_length=40, description="收款单（应付票据为付款单）表体科目；省略取基本科目 kzkm"
    )
    note_km: str | None = Field(
        None,
        min_length=1,
        max_length=40,
        description="票据科目（末级）；省略取基本科目 pjkm，应付票据在 pjkm 没有设置时必填",
    )
    digest: str | None = Field(None, min_length=1, max_length=120, description="摘要；收款单摘要省略为「票据登记」")
    sub_start: StrictInt | None = Field(
        None, ge=1, le=_SERIAL_MAX, description="分包票据的子票区间起号，与 sub_end 成对；省略为不分包票据"
    )
    sub_end: StrictInt | None = Field(
        None, ge=1, le=_SERIAL_MAX, description="子票区间止号，不小于 sub_start；(止号 − 起号 + 1) × 0.01 = amount"
    )
    dry_run: DryRunFlag = False

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: float) -> float:
        return _cents(value)

    @field_validator(*_TEXT_FIELDS)
    @classmethod
    def _text(cls, value: str | None, info) -> str | None:
        if value is not None and (not value.strip() or has_control(value)):
            raise ValueError(f"{info.field_name} 无效")
        return value

    @model_validator(mode="after")
    def _dates(self) -> CoNoteCreateIn:
        self._partner()
        sign = _day(self.sign_date, "sign_date")
        if _day(self.expire_date, "expire_date") < sign:
            raise ValueError("到期日不能早于签发日期")
        if _day(self.receipt_date, "receipt_date") < sign:
            raise ValueError("收票日期不能早于签发日期")
        self._range()
        return self

    def _partner(self) -> None:
        need, other = _PARTNER[self.flag]
        if getattr(self, other) is not None:
            raise ApiError(400, "bad_request", f"{other} 只用于{_PARTNER_TITLE[other][1]}", field=other)
        if getattr(self, need) is None:
            raise ApiError(400, "bad_request", f"缺少 {need}（{_PARTNER_TITLE[need][0]}）", field=need)

    def _range(self) -> None:
        if self.sub_start is None and self.sub_end is None:
            return
        if self.sub_start is None or self.sub_end is None:
            raise ValueError("sub_start 与 sub_end 要同时给出")
        if self.sub_start > self.sub_end:
            raise ValueError("sub_start 不能大于 sub_end")
        face = Decimal(str(self.amount))
        if _range_amount(self.sub_start, self.sub_end) != face:
            raise ApiError(400, "bad_request", split_mismatch(self.sub_start, self.sub_end, face), field="sub_end")

    def audit_ref(self) -> str:
        return self.note_no


class CoNoteDeleteIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="AR 应收票据、AP 应付票据（第二级写入，默认关闭，打开后只对测试账套开放）")
    note_no: str | None = Field(None, min_length=1, max_length=60, description="票据号；与 id 二选一")
    id: StrictInt | None = Field(None, gt=0, le=_ID_MAX, description="票据主键 AP_Note.Auto_ID；与 note_no 二选一")
    dry_run: DryRunFlag = False

    @field_validator("note_no")
    @classmethod
    def _code(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or has_control(value)):
            raise ValueError("note_no 无效")
        return value

    @model_validator(mode="after")
    def _one_key(self) -> CoNoteDeleteIn:
        if (self.id is None) == (self.note_no is None):
            raise ValueError("note_no 和 id 必须给且只给一个")
        return self

    def audit_ref(self) -> str:
        return str(self.id) if self.id is not None else str(self.note_no)


class CoNoteRegOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    acc: Scalar = Field(None, description="账套号")
    flag: Scalar = Field(None, description="AR 或 AP")
    note_id: Scalar = Field(None, description="票据主键 AP_Note.Auto_ID")
    note_no: Scalar = Field(None, description="票据号")
    receipt_id: Scalar = Field(
        None, description="收款单（应付票据为付款单）主键 Ap_CloseBill.iID；删除时是被删的单据，没有为 null"
    )
    receipt_code: Scalar = Field(None, description="收款单号（应付票据为付款单号）")
    amount: Scalar = Field(None, description="登记：票面金额")
    customer: Scalar = Field(None, description="登记（应收票据）：客户编码")
    vendor: Scalar = Field(None, description="登记（应付票据）：供应商编码")
    note_km: Scalar = Field(None, description="登记：票据科目")
    settle_code: Scalar = Field(None, description="登记：结算方式")
    receipt_verified: Scalar = Field(None, description="登记：收付款单是否已审核（总是 false）")
    sub_start: Scalar = Field(None, description="登记：子票区间起号；不是分包票据为 null")
    sub_end: Scalar = Field(None, description="登记：子票区间止号；不是分包票据为 null")
    deleted: Scalar = Field(None, description="删除：true")
