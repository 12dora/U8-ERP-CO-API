"""收付款与现金流报表 /v1/co/reports/receipts_payments、notes_due、cash_flow 的请求和响应。
字段名与桥一致，响应放行桥多给的字段。

金额是数字（本币，两位小数）。三张报表都是聚合结果，不分页：前两张用 limit 截断（truncated），合计不受 limit 影响。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import DATE, PASS, Scalar
from u8co_api.co_models_reports import Side, Word, _real_date

_DB = "只查数据库，不调用 U8 组件"
_SIDE = "ar 应收（客户），ap 应付（供应商）"
_PARTNER = "往来单位编码（客户或供应商），1 到 20 个，每个 1 到 60 个字符"
_LIMIT = "返回的最多行数，1 到 1000，缺省 200"
_MONEY = "金额为数字（本币，两位小数）"
_FISCAL = "会计年度，2000 到 2099。缺省为登录日期（date）的年份；year 是账套库年度，不参与判断"

GroupDim = Literal["day", "month", "partner", "person", "department", "settle"]

RECEIPTS_PAYMENTS_SUMMARY = "收付款汇总"
RECEIPTS_PAYMENTS_DOC = op_doc(
    "按日、月、往来单位、业务员、部门、结算方式汇总收款单（应收）或付款单（应付）及其退款。",
    (
        "用法",
        (
            "side=ar 汇总收款单和客户退款，side=ap 汇总付款单和供应商退款",
            "group_by 取 day、month、partner、person、department、settle 中的 0 到 2 个，缺省 [day]；"
            "空数组时 items 为空，只有 totals",
            "分组超过 limit 时保留 amount 最大的 limit 组（truncated 为 true）；group_by 含 day 或 month 时保留的行按日期排序，否则按 amount 从大到小",
            "date_to 缺省为登录日期（date）",
        ),
    ),
    (
        "规则",
        (
            "收款单 = 应收 + 单据类型 48，客户退款 = 应收 + 49；付款单 = 应付 + 49，供应商退款 = 应付 + 48",
            "日期是单据日期；金额取本币，amount 和 refund_amount 都是正数，net_amount = amount − refund_amount",
            "缺省只含已审核单据；include_unverified 为 true 时连未审核的一起算。期初单据不算",
            "docs 是该组的单据数（收付款单加退款单）。业务员、部门、结算方式没填的单据，编码和名称为 null",
            "totals 是全部符合条件的单据的合计（不受 limit 影响）",
            _DB,
        ),
    ),
)

NOTES_DUE_SUMMARY = "到期票据"
NOTES_DUE_DOC = op_doc(
    "列出到期日落在一段日期内的应收票据或应付票据，以及合计。",
    (
        "用法",
        (
            "expire_from 缺省为登录日期（date），expire_to 缺省为 expire_from 后 30 天，两端都含",
            "open_only 缺省 true：只列余额不为 0 的票据；false 时连已结算、贴现、背书、退回的也列出",
            "按到期日、票据号排序；超过 limit 的行不返回，truncated 为 true",
        ),
    ),
    (
        "规则",
        (
            "status：余额不为 0 是 open；余额为 0 时按最近一次处理定：贴现 discounted、背书 endorsed、退回 returned，"
            "其余 settled",
            "days_to_due = 到期日 − 登录日期，已过期为负数",
            "往来单位编码为空的票据（多数应收票据只有名称）partner_code 为 null，partner_name 取票据上的名称",
            "amount、remainder 取本币列；totals 是全部符合条件的票据的合计（不受 limit 影响）",
            _DB,
        ),
    ),
)

CASH_FLOW_SUMMARY = "现金流量统计"
CASH_FLOW_DOC = op_doc(
    "按现金流量项目汇总凭证上指定的现金流量（直接法），分会计期间。",
    (
        "用法",
        (
            "fiscal_year 缺省为登录日期（date）的年份；period_from 缺省 1；"
            "period_to 缺省：会计年度是登录年度时取登录月份，否则取 12",
            "include_unposted 为 true 时连未记账凭证一起算（作废凭证始终不算）",
        ),
    ),
    (
        "规则",
        (
            "只列范围内有数据的项目；amount 是各期之和，periods 是每个期间的金额（没有数据的期间为 0）",
            "流入类项目金额 = 贷方 − 借方，流出类项目金额 = 借方 − 贷方，正常的收入、支出都是正数，冲销和红字为负数",
            "class_code、class_name 是项目的一级分类（经营活动、投资活动、筹资活动、汇率变动、现金及现金等价物）",
            "unassigned：范围内现金账户（1001、1002、1012）有净流入或净流出、却没有指定任何现金流量项目的凭证张数和金额",
            _DB,
        ),
    ),
)


class ReceiptsPaymentsIn(CoAuth):
    side: Side = Field(..., description=_SIDE)
    date_from: str = Field(..., pattern=DATE, description="起始日期 yyyy-MM-dd（含）")
    date_to: str | None = Field(None, pattern=DATE, description="截止日期 yyyy-MM-dd（含），缺省为 date")
    group_by: list[GroupDim] | None = Field(
        None,
        max_length=2,
        description="分组维度：day 日、month 月、partner 往来单位、person 业务员、department 部门、settle 结算方式，"
        "0 到 2 个不重复，缺省 [day]；空数组只出合计",
    )
    partner: list[Word] | None = Field(None, min_length=1, max_length=20, description=_PARTNER)
    person: list[Word] | None = Field(None, min_length=1, max_length=20, description="业务员编码，1 到 20 个")
    dept: list[Word] | None = Field(None, min_length=1, max_length=20, description="部门编码，1 到 20 个")
    include_unverified: StrictBool | None = Field(None, description="为 true 时连未审核的单据一起算。缺省只含已审核")
    limit: StrictInt | None = Field(None, ge=1, le=1000, description=_LIMIT)

    @field_validator("date_from", "date_to")
    @classmethod
    def _dates(cls, value: str | None) -> str | None:
        return _real_date(value)

    @field_validator("group_by")
    @classmethod
    def _distinct(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("group_by 不能重复")
        return value

    @model_validator(mode="after")
    def _range(self) -> ReceiptsPaymentsIn:
        if self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to")
        return self

    def audit_ref(self) -> str:
        return self.side


class NotesDueIn(CoAuth):
    side: Side = Field(..., description=_SIDE)
    expire_from: str | None = Field(None, pattern=DATE, description="到期日起 yyyy-MM-dd（含），缺省为 date")
    expire_to: str | None = Field(
        None, pattern=DATE, description="到期日止 yyyy-MM-dd（含），缺省为 expire_from 后 30 天"
    )
    open_only: StrictBool | None = Field(None, description="缺省 true：只列余额不为 0 的票据")
    partner: list[Word] | None = Field(None, min_length=1, max_length=20, description=_PARTNER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description=_LIMIT)

    @field_validator("expire_from", "expire_to")
    @classmethod
    def _dates(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _range(self) -> NotesDueIn:
        if self.expire_from is not None and self.expire_to is not None and self.expire_from > self.expire_to:
            raise ValueError("expire_from 不能晚于 expire_to")
        return self

    def audit_ref(self) -> str:
        return self.side


class CashFlowIn(CoAuth):
    fiscal_year: StrictInt | None = Field(None, ge=2000, le=2099, description=_FISCAL)
    period_from: StrictInt | None = Field(None, ge=1, le=12, description="起始期间，1 到 12，缺省 1")
    period_to: StrictInt | None = Field(
        None,
        ge=1,
        le=12,
        description="截止期间，1 到 12，不小于 period_from。缺省：会计年度是登录年度时取登录月份，否则取 12",
    )
    include_unposted: StrictBool | None = Field(None, description="为 true 时连未记账凭证一起算。缺省只含已记账")

    @model_validator(mode="after")
    def _periods(self) -> CashFlowIn:
        if self.period_from is not None and self.period_to is not None and self.period_from > self.period_to:
            raise ValueError("period_from 不能大于 period_to")
        return self


class _Out(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")


class ReceiptsPaymentsOut(_Out):
    side: Scalar = Field(None, description="ar 或 ap")
    date_from: Scalar = Field(None, description="起始日期")
    date_to: Scalar = Field(None, description="截止日期（缺省时为登录日期）")
    group_by: list[Any] | None = Field(None, description="实际使用的分组维度")
    include_unverified: Scalar = Field(None, description="是否含未审核单据")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="含 day 或 month 维度时按日期排序（同日期按 amount 从大到小），否则按 amount 从大到小。分组键：date（yyyy-MM-dd）、month（yyyy-MM）、"
        "partner_code 与 partner_name、person_code 与 person_name、dept_code 与 dept_name、"
        "settle_code 与 settle_name（没填为 null）；度量：docs（单据数）、amount（收款或付款合计）、"
        "refund_amount（退款合计）、net_amount（amount − refund_amount）。" + _MONEY,
    )
    count: Scalar = Field(None, description="items 的行数")
    truncated: Scalar = Field(None, description="为 true 时超过 limit 的行没有返回")
    totals: dict[str, Any] | None = Field(
        None,
        description="全部符合条件的单据的合计：docs、amount、refund_amount、net_amount（不受 limit 影响）",
    )


class NotesDueOut(_Out):
    side: Scalar = Field(None, description="ar 或 ap")
    expire_from: Scalar = Field(None, description="到期日起（缺省时为登录日期）")
    expire_to: Scalar = Field(None, description="到期日止（缺省时为 expire_from 后 30 天）")
    open_only: Scalar = Field(None, description="是否只列余额不为 0 的票据")
    as_of: Scalar = Field(None, description="计算 days_to_due 的登录日期")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按到期日、票据号排序：code、note_type（结算方式名称，期初票据为 null）、"
        "partner_code（没填为 null）、partner_name、sign_date、expire_date、amount、remainder、"
        "status（open、settled、discounted、endorsed、returned）、days_to_due（到期日 − 登录日期）。" + _MONEY,
    )
    count: Scalar = Field(None, description="items 的行数")
    truncated: Scalar = Field(None, description="为 true 时超过 limit 的行没有返回")
    totals: dict[str, Any] | None = Field(
        None,
        description="全部符合条件的票据的合计：count、amount、remainder（不受 limit 影响）",
    )


class CashFlowOut(_Out):
    fiscal_year: Scalar = Field(None, description="会计年度")
    period_from: Scalar = Field(None, description="起始期间")
    period_to: Scalar = Field(None, description="截止期间")
    include_unposted: Scalar = Field(None, description="是否含未记账凭证")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按项目编码排序：code、name、class_code、class_name（一级分类）、direction（in 或 out，"
        '项目不在档案里时为 null）、amount（各期之和）、periods（{"1": 金额, …}，范围内每个期间一项）。' + _MONEY,
    )
    unassigned: dict[str, Any] | None = Field(
        None,
        description="范围内现金账户有净流入或净流出却没有指定现金流量项目的凭证：vouchers（张数）、amount（每张取净额的绝对值之和）",
    )
