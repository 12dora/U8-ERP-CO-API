"""明细账报表 /v1/co/reports/arap_detail、gl_detail 的请求和响应。字段名与桥一致，响应放行桥多给的字段。

金额是数字（本币，两位小数）。after 是桥给的不透明游标，原样传回。
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import DATE, PASS, Scalar
from u8co_api.co_models_reports import _AFTER, _MONEY, _NEXT, After, CodePrefix, Side, Word, _real_date

_WORD_TEXT = r"^[^\x00-\x1f\x7f-\x9f]*[^\s\x00-\x1f\x7f-\x9f][^\x00-\x1f\x7f-\x9f]*$"
Code20 = Annotated[str, Field(max_length=20, pattern=_WORD_TEXT)]
_LIMIT = "每页条数，1 到 1000，缺省 200"


def _order(low: str | None, high: str | None) -> None:
    if low is not None and high is not None and low > high:
        raise ValueError("date_from 不能晚于 date_to")


class ReportArapDetailIn(CoAuth):
    side: Side = Field(..., description="ar 应收（按客户），ap 应付（按供应商）")
    partner: Word | list[Word] = Field(
        ...,
        description="往来单位编码，或 1 到 20 个编码的数组。按编码排序，每个单位各自从期初滚动余额",
    )
    date_from: str = Field(..., pattern=DATE, description="起始日期 yyyy-MM-dd（含），期初是这一天之前的累计")
    date_to: str | None = Field(None, pattern=DATE, description="截止日期 yyyy-MM-dd（含），缺省为 date")
    basis: Literal["register"] | None = Field(
        None,
        description="日期口径：register 登记日期（缺省，与 arap_balance 的 as_of 相同）。单据日期口径暂不开放",
    )
    accounts: list[CodePrefix] | None = Field(
        None,
        min_length=1,
        max_length=20,
        description="控制科目编码前缀，1 到 20 个。缺省应收 1122，应付 2202",
    )
    exclude_accounts: list[CodePrefix] | None = Field(
        None,
        max_length=20,
        description="要排除的科目编码前缀，最多 20 个",
    )
    dept: Code20 | None = Field(None, description="部门编码等于（明细行上的部门）。期初也只算这个部门的行")
    person: Code20 | None = Field(None, description="业务员编码等于（明细行上的业务员）。期初也只算这个业务员的行")
    include_writeoff: StrictBool | None = Field(
        None,
        description="为 true 时在 items 里列出核销行（cProcStyle=9P），缺省 false。只影响输出哪些行："
        "期初、区间借贷、期末和滚动余额一律含核销行，开关前后相同",
    )
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description=_LIMIT)

    @field_validator("partner")
    @classmethod
    def _partners(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, list) and not 1 <= len(value) <= 20:
            raise ValueError("partner 必须是 1 到 20 个往来单位编码")
        return value

    @field_validator("date_from", "date_to")
    @classmethod
    def _dates(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _range(self) -> ReportArapDetailIn:
        _order(self.date_from, self.date_to)
        return self

    def audit_ref(self) -> str:
        return self.side


class ReportGlDetailIn(CoAuth):
    code: CodePrefix = Field(..., description="科目编码，1 到 40 位数字、字母、点或短横")
    include_sub: StrictBool | None = Field(
        None,
        description="缺省 true：含下级科目（编码以 code 开头的末级科目）；false 只查 code 本身（只对末级科目有意义）",
    )
    fiscal_year: StrictInt | None = Field(
        None,
        ge=2000,
        le=2099,
        description="会计年度。按期间查询时缺省为登录日期（date）的年份；按日期查询时取日期的年份，给了就必须一致",
    )
    period_from: StrictInt | None = Field(None, ge=1, le=12, description="起始期间，1 到 12。与日期二选一")
    period_to: StrictInt | None = Field(None, ge=1, le=12, description="截止期间，1 到 12，不小于 period_from")
    date_from: str | None = Field(None, pattern=DATE, description="起始日期 yyyy-MM-dd（含）。与期间二选一")
    date_to: str | None = Field(None, pattern=DATE, description="截止日期 yyyy-MM-dd（含），与 date_from 同一年")
    include_unposted: StrictBool | None = Field(
        None,
        description="为 true 时含未记账凭证（不含作废）：起始期间之前的计入期初，区间内的列为明细（posted 为 false）",
    )
    customer: Word | None = Field(None, description="客户编码等于（辅助核算）")
    vendor: Word | None = Field(None, description="供应商编码等于（辅助核算）")
    dept: Code20 | None = Field(None, description="部门编码等于（辅助核算）")
    person: Code20 | None = Field(None, description="个人编码等于（辅助核算）")
    project: Word | None = Field(None, description="项目编码等于（辅助核算）")
    project_class: Code20 | None = Field(None, description="项目大类编码，只能和 project 一起用")
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description=_LIMIT)

    @field_validator("date_from", "date_to")
    @classmethod
    def _dates(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _range(self) -> ReportGlDetailIn:
        periods = self.period_from is not None or self.period_to is not None
        dates = self.date_from is not None or self.date_to is not None
        if periods == dates:
            raise ValueError("period_from / period_to 与 date_from / date_to 必须二选一")
        if periods:
            _gl_periods(self.period_from, self.period_to)
        else:
            _gl_dates(self.date_from, self.date_to, self.fiscal_year)
        if self.project_class is not None and self.project is None:
            raise ValueError("project_class 只能和 project 一起用")
        return self

    def audit_ref(self) -> str:
        return self.code


def _gl_periods(low: int | None, high: int | None) -> None:
    if low is None or high is None:
        raise ValueError("period_from 和 period_to 必须同时给出")
    if low > high:
        raise ValueError("period_from 不能大于 period_to")


def _gl_dates(low: str | None, high: str | None, year: int | None) -> None:
    if low is None or high is None:
        raise ValueError("date_from 和 date_to 必须同时给出")
    _order(low, high)
    if low[:4] != high[:4]:
        raise ValueError("date_from 和 date_to 必须在同一年度")
    if year is not None and str(year) != low[:4]:
        raise ValueError("fiscal_year 与 date_from 的年份不一致")


class ReportArapDetailOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    side: Scalar = Field(None, description="ar 或 ap")
    basis: Scalar = Field(None, description="日期口径：register 或 document")
    date_from: Scalar = Field(None, description="起始日期")
    date_to: Scalar = Field(None, description="截止日期")
    partners: list[dict[str, Any]] | None = Field(
        None,
        description="按编码排序的往来单位汇总：partner、name、opening（date_from 之前的余额）、debit、credit"
        "（区间合计）、closing。没有任何记录的单位不列。每页都给全部单位。" + _MONEY,
    )
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按往来单位、日期、登记顺序排序，一行是同一天同一单据（同一处理方式、对方单据）的合计："
        "partner、date（口径日期）、reg_date、doc_date、doc_type、doc_type_name、doc_code、proc_style、"
        "co_doc_type、co_doc_code、digest、account（多个科目时为 null）、dept、person、debit、credit、"
        "balance（本单位的滚动余额，应收借减贷，应付贷减借）。" + _MONEY,
    )
    next: Scalar = Field(None, description=_NEXT)


class ReportGlDetailOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    fiscal_year: Scalar = Field(None, description="会计年度")
    code: Scalar = Field(None, description="科目编码")
    name: Scalar = Field(None, description="科目名称")
    leaf: Scalar = Field(None, description="是否末级科目")
    include_sub: Scalar = Field(None, description="是否含下级科目")
    include_unposted: Scalar = Field(None, description="是否含未记账凭证")
    period_from: Scalar = Field(None, description="起始期间（按日期查询时是 date_from 的月份）")
    period_to: Scalar = Field(None, description="截止期间")
    date_from: Scalar = Field(None, description="起始日期，按期间查询时为 null")
    date_to: Scalar = Field(None, description="截止日期，按期间查询时为 null")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按期间、日期、凭证类别、凭证号、分录号排序：period、date、sign、no、entry、digest、account、"
        "account_name、debit、credit、posted、dept、person、customer、supplier、item_class、item，以及滚动余额"
        " dir（借、贷、平）和 balance（绝对值）。" + _MONEY,
    )
    open_dir: Scalar = Field(None, description="期初方向：借、贷、平。期初、合计、期末是整个区间的，每页相同")
    open_debit: Scalar = Field(None, description="期初借方余额")
    open_credit: Scalar = Field(None, description="期初贷方余额")
    total_debit: Scalar = Field(None, description="区间借方合计")
    total_credit: Scalar = Field(None, description="区间贷方合计")
    close_dir: Scalar = Field(None, description="期末方向")
    close_debit: Scalar = Field(None, description="期末借方余额")
    close_credit: Scalar = Field(None, description="期末贷方余额")
    next: Scalar = Field(None, description=_NEXT)
