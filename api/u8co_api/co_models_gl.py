"""总账凭证 /v1/co/gl/vouchers/* 的请求和响应。字段名与桥一致，响应放行桥多给的字段。"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from u8co_api.co_clock import login_defaults
from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag

PASS = ConfigDict(extra="allow")
_FORBID = ConfigDict(extra="forbid")
# GL_accvouch.ino_id 是 smallint。
_NO_MAX = 32767
_AMOUNT_MAX = 10**12
DATE = r"^\d{4}-\d{2}-\d{2}$"
Scalar = str | int | float | bool | None
Amount = Annotated[float, Field(strict=True, allow_inf_nan=False, ge=-_AMOUNT_MAX, le=_AMOUNT_MAX)]
Sign = Annotated[str, Field(pattern=r"^\S{1,2}$", description="凭证类别字（csign），1 到 2 个字符，例如 转")]
GlState = Literal["all", "unaudited", "audited", "posted", "void"]


def _one_side(debit: float | None, credit: float | None) -> None:
    if (debit is None) == (credit is None):
        raise ValueError("debit 和 credit 必须且只能给一个")
    value = debit if debit is not None else credit
    if value == 0:
        raise ValueError("金额不能为 0")


def _sum(values: list[float | None]) -> Decimal:
    total = Decimal(0)
    for value in values:
        if value is not None:
            total += Decimal(str(value))
    return total


class GlKeyIn(CoAuth):
    period: int = Field(..., ge=1, le=12, description="会计期间，1 到 12")
    sign: Sign
    no: int = Field(..., ge=1, le=_NO_MAX, description="凭证号（ino_id），1 到 32767")

    def audit_ref(self) -> str:
        return f"{self.period}-{self.sign}-{self.no}"


class GlKeyActIn(GlKeyIn):
    """作废、审核、签字、删除等写操作：多一个 dry_run（读取、附件列表用 GlKeyIn，不收）。"""

    dry_run: DryRunFlag = False


class GlCashFlowIn(BaseModel):
    model_config = _FORBID
    item: str = Field(..., min_length=1, max_length=20, description="现金流量项目编码，例如 07")
    debit: Amount | None = Field(None, description="流入金额。与 credit 二选一，不能为 0")
    credit: Amount | None = Field(None, description="流出金额。与 debit 二选一，不能为 0")

    @model_validator(mode="after")
    def _checked(self) -> GlCashFlowIn:
        _one_side(self.debit, self.credit)
        return self


class GlLineIn(BaseModel):
    model_config = _FORBID
    account: str = Field(..., min_length=1, max_length=40, description="科目编码，必须是末级科目")
    digest: str = Field(..., min_length=1, max_length=120, description="摘要，最长 120 字")
    debit: Amount | None = Field(None, description="借方金额（本币）。与 credit 二选一，不能为 0；红字用负数")
    credit: Amount | None = Field(None, description="贷方金额（本币）。与 debit 二选一，不能为 0；红字用负数")
    dept: str | None = Field(None, min_length=1, max_length=12, description="部门编码（辅助核算）")
    person: str | None = Field(None, min_length=1, max_length=20, description="人员编码（辅助核算）")
    customer: str | None = Field(None, min_length=1, max_length=20, description="客户编码（辅助核算）")
    supplier: str | None = Field(None, min_length=1, max_length=20, description="供应商编码（辅助核算）")
    item_class: str | None = Field(None, min_length=1, max_length=2, description="项目大类编码")
    item: str | None = Field(None, min_length=1, max_length=60, description="项目编码")
    settle: str | None = Field(None, min_length=1, max_length=3, description="结算方式编码")
    doc_no: str | None = Field(None, min_length=1, max_length=120, description="票号")
    doc_date: str | None = Field(None, pattern=DATE, description="票据日期，yyyy-MM-dd")
    currency: str | None = Field(None, min_length=1, max_length=8, description="外币名称。省略为本币")
    rate: float | None = Field(None, gt=0, le=10**6, allow_inf_nan=False, strict=True, description="汇率")
    qty: Amount | None = Field(None, description="数量，记在金额所在的一方")
    cash_flow: list[GlCashFlowIn] | None = Field(
        None,
        min_length=1,
        max_length=50,
        description="现金流量。现金或银行科目必须给出，1 到 50 项",
    )

    @model_validator(mode="after")
    def _checked(self) -> GlLineIn:
        _one_side(self.debit, self.credit)
        return self


class GlHeadIn(BaseModel):
    model_config = _FORBID
    sign: Sign
    date: str | None = Field(
        None,
        pattern=DATE,
        description="制单日期，yyyy-MM-dd。缺省为登录日期；期间取该日期的月份，年份必须等于登录日期的年份",
    )
    attachments: int | None = Field(None, ge=0, le=_NO_MAX, description="附单据数")


class GlCreateIn(CoAuth):
    head: GlHeadIn = Field(..., description="表头")
    lines: list[GlLineIn] = Field(
        ...,
        min_length=2,
        max_length=200,
        description="分录，2 到 200 行。借方合计必须等于贷方合计且大于 0",
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _balanced(self) -> GlCreateIn:
        debit = _sum([line.debit for line in self.lines])
        credit = _sum([line.credit for line in self.lines])
        if debit <= 0 or debit != credit:
            raise ValueError("借贷不平衡")
        # year 是账套库的起始年度，不是会计年度。会计年度取登录日期（缺省为配置时区的今天）的年份。
        _, login_year = login_defaults(self.date)
        if self.head.date and self.head.date[:4] != login_year:
            raise ValueError("凭证日期的年份必须等于登录日期的年份")
        return self


class GlUpdateIn(GlCreateIn, GlKeyIn):
    """整张替换：表头和全部分录重新提交。"""


class GlListIn(CoAuth):
    period_from: int = Field(..., ge=1, le=12, description="起始期间，1 到 12")
    period_to: int = Field(..., ge=1, le=12, description="截止期间，1 到 12，不小于 period_from")
    sign: Sign | None = None
    date_from: str | None = Field(None, pattern=DATE, description="制单日期下限，yyyy-MM-dd，含当天")
    date_to: str | None = Field(None, pattern=DATE, description="制单日期上限，yyyy-MM-dd，含当天")
    maker: str | None = Field(None, min_length=1, max_length=20, description="制单人姓名")
    state: GlState | None = Field(
        None,
        description="all 全部（缺省），unaudited 未审核，audited 已审核，posted 已记账，void 已作废",
    )
    after: str | None = Field(None, min_length=1, max_length=200, description="上一页响应的 next，原样传回")
    limit: int | None = Field(None, ge=1, le=200, description="每页条数，1 到 200，缺省 50")

    @model_validator(mode="after")
    def _ranges(self) -> GlListIn:
        if self.period_from > self.period_to:
            raise ValueError("period_from 不能大于 period_to")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to")
        return self


class GlVoucherOut(BaseModel):
    model_config = PASS
    period: Scalar = Field(None, description="会计期间")
    sign: Scalar = Field(None, description="凭证类别字")
    no: Scalar = Field(None, description="凭证号")
    date: Scalar = Field(None, description="制单日期")
    attachments: Scalar = Field(None, description="附单据数")
    maker: Scalar = Field(None, description="制单人")
    checker: Scalar = Field(None, description="审核人")
    audit_date: Scalar = Field(None, description="审核日期")
    cashier: Scalar = Field(None, description="出纳签字人")
    poster: Scalar = Field(None, description="记账人")
    posted: Scalar = Field(None, description="是否已记账")
    void: Scalar = Field(None, description="是否已作废")
    error: Scalar = Field(None, description="是否为错误凭证")
    source_system: Scalar = Field(None, description="来源系统（coutsysname）。GL 或空为总账手工凭证")
    source_sign: Scalar = Field(None, description="外部凭证类别（coutsign）")
    source_no: Scalar = Field(None, description="外部凭证号（coutno_id）")
    out_no: Scalar = Field(None, description="外部单据号")
    blue_out_no: Scalar = Field(
        None, description="红字冲销凭证：被冲销的原凭证的外部业务号（cblueoutno_id）；不是红字冲销凭证时为空串"
    )


class GlCashFlowOut(BaseModel):
    model_config = PASS
    item: Scalar = Field(None, description="现金流量项目编码")
    debit: Scalar = Field(None, description="流入金额")
    credit: Scalar = Field(None, description="流出金额")


class GlLineOut(BaseModel):
    model_config = PASS
    entry: Scalar = Field(None, description="分录行号")
    account: Scalar = Field(None, description="科目编码")
    account_name: Scalar = Field(None, description="科目名称")
    digest: Scalar = Field(None, description="摘要")
    debit: Scalar = Field(None, description="借方金额（本币）")
    credit: Scalar = Field(None, description="贷方金额（本币）")
    debit_fc: Scalar = Field(None, description="借方外币金额")
    credit_fc: Scalar = Field(None, description="贷方外币金额")
    qty_debit: Scalar = Field(None, description="借方数量")
    qty_credit: Scalar = Field(None, description="贷方数量")
    currency: Scalar = Field(None, description="外币名称")
    rate: Scalar = Field(None, description="汇率")
    dept: Scalar = Field(None, description="部门编码")
    person: Scalar = Field(None, description="人员编码")
    customer: Scalar = Field(None, description="客户编码")
    supplier: Scalar = Field(None, description="供应商编码")
    item_class: Scalar = Field(None, description="项目大类")
    item: Scalar = Field(None, description="项目编码")
    settle: Scalar = Field(None, description="结算方式")
    doc_no: Scalar = Field(None, description="票号")
    doc_date: Scalar = Field(None, description="票据日期")
    cash_flow: list[GlCashFlowOut] | None = Field(None, description="现金流量")


class GlLoadOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到凭证")
    voucher: GlVoucherOut | None = Field(None, description="凭证表头和状态")
    lines: list[GlLineOut] | None = Field(None, description="分录")


class GlListItem(BaseModel):
    model_config = PASS
    period: Scalar = Field(None, description="会计期间")
    sign: Scalar = Field(None, description="凭证类别字")
    no: Scalar = Field(None, description="凭证号")
    date: Scalar = Field(None, description="制单日期")
    maker: Scalar = Field(None, description="制单人")
    checker: Scalar = Field(None, description="审核人")
    cashier: Scalar = Field(None, description="出纳签字人")
    posted: Scalar = Field(None, description="是否已记账")
    void: Scalar = Field(None, description="是否已作废")
    debit_total: Scalar = Field(None, description="借方合计")
    lines: Scalar = Field(None, description="分录行数")


class GlListOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到列表")
    items: list[GlListItem] | None = Field(None, description="按期间、类别、凭证号排序的凭证")
    next: Scalar = Field(None, description="下一页游标，原样放进 after。最后一页省略")


class GlKeyOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    period: Scalar = Field(None, description="会计期间")
    sign: Scalar = Field(None, description="凭证类别字")
    no: Scalar = Field(None, description="凭证号")
    state: dict[str, Any] | None = Field(
        None,
        description="操作后重新读到的状态（审核、出纳签字、作废等）。新增、修改和删除不返回",
    )
    deleted: bool | None = Field(None, description="删除成功时为 true")
