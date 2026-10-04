"""只读报表 /v1/co/reports/* 的请求和响应。字段名与桥一致，响应放行桥多给的字段。

金额是数字（本币，两位小数），物料清单用量是数字（六位小数）。after 是桥给的不透明游标，原样传回。
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, ValidationInfo, field_validator, model_validator

from u8co_api.co_doctext import field_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import DATE, PASS, Scalar

_FISCAL = "会计年度，2000 到 2099。缺省为登录日期（date）的年份；year 是账套库年度，不参与判断"
_AFTER = "上一页响应的 next，原样传回"
_NONZERO = "为 true 时去掉期初、本期、累计、期末全为 0 的行"
_NEXT = "下一页的 after。最后一页为 null"
_MONEY = "金额为数字（本币，两位小数）"
_BUCKETS_TEXT = "buckets 必须是 1 到 10 个递增的天数"

CodePrefix = Annotated[str, Field(pattern=r"^[0-9A-Za-z.\-]{1,40}$")]
# 与桥一致：去掉首尾空白后不能为空，不含控制字符（含 \x7f–\x9f）。
Word = Annotated[
    str,
    Field(max_length=60, pattern=r"^[^\x00-\x1f\x7f-\x9f]*[^\s\x00-\x1f\x7f-\x9f][^\x00-\x1f\x7f-\x9f]*$"),
]
PersonCode = Annotated[
    str,
    Field(max_length=20, pattern=r"^[^\x00-\x1f\x7f-\x9f]*[^\s\x00-\x1f\x7f-\x9f][^\x00-\x1f\x7f-\x9f]*$"),
]
Day = Annotated[StrictInt, Field(ge=1, le=3650)]
# 桥的 next 游标是 base64url，最长 512 个可见 ASCII 字符。
After = Annotated[str, Field(pattern=r"^[\x21-\x7e]{1,512}$")]
Dim = Literal["customer", "vendor", "dept", "person", "project"]
Side = Literal["ar", "ap"]


def _real_date(value: str | None) -> str | None:
    if value is None:
        return value
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError("日期无效") from None
    return value


def _periods(low: int, high: int) -> None:
    if low > high:
        raise ValueError("period_from 不能大于 period_to")


class ReportCloseStatusIn(CoAuth):
    fiscal_year: StrictInt | None = Field(None, ge=2000, le=2099, description=_FISCAL)


class ReportGlBalanceIn(CoAuth):
    fiscal_year: StrictInt | None = Field(None, ge=2000, le=2099, description=_FISCAL)
    period_from: StrictInt = Field(..., ge=1, le=12, description="起始期间，1 到 12")
    period_to: StrictInt = Field(..., ge=1, le=12, description="截止期间，1 到 12，不小于 period_from")
    grade_from: StrictInt | None = Field(None, ge=1, le=9, description="科目级次下限，1 到 9，缺省 1")
    grade_to: StrictInt | None = Field(None, ge=1, le=9, description="科目级次上限，1 到 9，缺省 9，不小于 grade_from")
    code_prefix: CodePrefix | None = Field(None, description="科目编码前缀，1 到 40 位数字、字母、点或短横")
    leaf_only: StrictBool | None = Field(None, description="为 true 时只要末级科目")
    include_unposted: StrictBool | None = Field(
        None,
        description="为 true 时含未记账凭证（不含作废），汇总到上级科目：period_from 之前的计入期初，"
        "period_from 到 period_to 的计入本期，累计和期末都含。只有未记账凭证的科目也列出",
    )
    nonzero: StrictBool | None = Field(None, description=_NONZERO)
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description="每页条数，1 到 1000，缺省 200")

    @model_validator(mode="after")
    def _ranges(self) -> ReportGlBalanceIn:
        _periods(self.period_from, self.period_to)
        low = 1 if self.grade_from is None else self.grade_from
        high = 9 if self.grade_to is None else self.grade_to
        if low > high:
            raise ValueError("grade_from 不能大于 grade_to")
        return self


class ReportGlAuxIn(CoAuth):
    dim: Dim = Field(
        ...,
        description="辅助核算维度：customer 客户、vendor 供应商、dept 部门、person 个人、project 项目",
    )
    fiscal_year: StrictInt | None = Field(None, ge=2000, le=2099, description=_FISCAL)
    period_from: StrictInt = Field(..., ge=1, le=12, description="起始期间，1 到 12")
    period_to: StrictInt = Field(..., ge=1, le=12, description="截止期间，1 到 12，不小于 period_from")
    code_prefix: CodePrefix | None = Field(None, description="科目编码前缀，规则同 gl_balance")
    dim_code: Word | None = Field(None, description="维度编码等于，1 到 60 个字符")
    project_class: str | None = Field(
        None,
        pattern=r"^[0-9A-Za-z]{1,20}$",
        description="项目大类编码，只能和 dim=project 一起用",
    )
    nonzero: StrictBool | None = Field(None, description=_NONZERO)
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description="每页条数，1 到 1000，缺省 200")

    @model_validator(mode="after")
    def _ranges(self) -> ReportGlAuxIn:
        _periods(self.period_from, self.period_to)
        if self.project_class is not None and self.dim != "project":
            raise ValueError("project_class 只能和 dim=project 一起用")
        return self

    def audit_ref(self) -> str:
        return self.dim


class ReportArapIn(CoAuth):
    side: Side = Field(..., description="ar 应收（按客户），ap 应付（按供应商）")
    as_of: str | None = Field(None, pattern=DATE, description="截止日期 yyyy-MM-dd，按登记日期含当天。缺省为 date")
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
    partner: Word | None = Field(None, description="客户或供应商编码等于")
    nonzero: StrictBool | None = Field(None, description="缺省 true：去掉余额为 0 的往来单位")
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description="每页条数，1 到 1000，缺省 200")

    @field_validator("as_of")
    @classmethod
    def _as_of(cls, value: str | None) -> str | None:
        return _real_date(value)

    def audit_ref(self) -> str:
        return self.side


class ReportAgingIn(ReportArapIn):
    basis: Literal["document", "due"] | None = Field(
        None,
        description="账龄起算：document 单据日期（缺省）；due 到期日（收款日期；否则信用期不为 0 时信用起算日加信用期，"
        "没有信用起算日取单据日期；信用期为 0 或空时信用起算日或单据日期加 default_credit_days）",
    )
    buckets: list[Day] | None = Field(
        None,
        min_length=1,
        max_length=10,
        description="账龄区间的上限天数，1 到 10 个严格递增的整数，每个 1 到 3650。缺省 [30,60,90,180,365]",
    )

    group_by: Literal["partner", "person", "partner_person"] | None = Field(
        None,
        description="分组：partner 往来单位（缺省）；person 业务员；partner_person 往来单位 + 业务员。"
        "业务员取单据上的业务员，单据没有时取客户或供应商档案的专管业务员",
    )
    person: list[PersonCode] | None = Field(
        None,
        min_length=1,
        max_length=20,
        description="业务员编码，1 到 20 个，按解析后的业务员（单据，其次档案）过滤。任何分组都可用",
    )
    overdue_only: StrictBool | None = Field(
        None,
        description="为 true 时只留逾期金额（overdue）大于 0 且余额（balance）大于 0 的行。只能和 basis=due 一起用",
    )
    default_credit_days: StrictInt | None = Field(
        None,
        ge=0,
        le=3650,
        description=field_doc(
            "缺省信用天数，0 到 3650，只能和 basis=due 一起用。",
            (
                "",
                (
                    "单据没有收款日期、信用期为 0 或空时：到期日 = 信用起算日 + 本值",
                    "信用期不为 0 的单据仍按信用期：到期日 = 信用起算日 + 信用期",
                    "没有信用起算日时取单据日期",
                    "省略或为 0 时不加天数",
                ),
            ),
        ),
        examples=[30],
    )

    @field_validator("buckets")
    @classmethod
    def _ascending(cls, value: list[int] | None) -> list[int] | None:
        if value is not None and any(a >= b for a, b in zip(value, value[1:])):
            raise ValueError(_BUCKETS_TEXT)
        return value

    @model_validator(mode="after")
    def _overdue(self) -> ReportAgingIn:
        if self.overdue_only and self.basis != "due":
            raise ValueError("overdue_only 只能和 basis=due 一起用")
        return self

    @field_validator("default_credit_days")
    @classmethod
    def _credit_days(cls, value: int | None, info: ValidationInfo) -> int | None:
        # 写成字段校验，400 的 field 才是 default_credit_days（basis 在它前面声明，info.data 里已有）。
        if value is not None and info.data.get("basis") != "due":
            raise ValueError("default_credit_days 只能和 basis=due 一起用")
        return value


class ReportBomIn(CoAuth):
    parent: Word = Field(..., description="母件存货编码，1 到 60 个字符")
    as_of: str | None = Field(
        None,
        pattern=DATE,
        description="生效日期 yyyy-MM-dd。取当天有效的已审核版本（同时有效时取最高版本）和当天有效的子件。缺省为 date",
    )
    levels: StrictInt | None = Field(None, ge=1, le=10, description="展开层数，1 到 10，缺省 1（单层）")
    limit: StrictInt | None = Field(
        None,
        ge=1,
        le=5000,
        description="最多返回的行数，1 到 5000，缺省 1000。超出时 truncated 为 true",
    )

    @field_validator("as_of")
    @classmethod
    def _as_of(cls, value: str | None) -> str | None:
        return _real_date(value)

    def audit_ref(self) -> str:
        return self.parent


class ReportCloseStatusOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    fiscal_year: Scalar = Field(None, description="会计年度")
    modules: list[str] | None = Field(
        None,
        description="模块代码：SA 销售、PU 采购、ST 库存、IA 存货核算、GL 总账、AR 应收、AP 应付、CA 成本、FA 固定资产",
    )
    periods: list[dict[str, Any]] | None = Field(
        None,
        description="该年度已建立的期间 1 到 12，升序：period 和 closed（模块代码 → 是否已结账）。年度未建立时为空数组",
    )


class ReportGlBalanceOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    fiscal_year: Scalar = Field(None, description="会计年度")
    period_from: Scalar = Field(None, description="起始期间")
    period_to: Scalar = Field(None, description="截止期间")
    include_unposted: Scalar = Field(None, description="是否含未记账凭证")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按科目编码排序：code、name、grade、leaf、class、natural_dir、open_dir、open_debit、open_credit、"
        "period_debit、period_credit、ytd_debit、ytd_credit、close_dir、close_debit、close_credit。"
        "上级科目已含下级，不要跨级相加。" + _MONEY,
    )
    next: Scalar = Field(None, description=_NEXT)


class ReportGlAuxOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    fiscal_year: Scalar = Field(None, description="会计年度")
    dim: Scalar = Field(None, description="辅助核算维度")
    period_from: Scalar = Field(None, description="起始期间")
    period_to: Scalar = Field(None, description="截止期间")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按科目、项目大类、维度编码排序：code、name、dim_code、dim_name、project_class，"
        "以及与 gl_balance 相同的期初、本期、累计、期末列。项目的 dim_name 为 null。" + _MONEY,
    )
    next: Scalar = Field(None, description=_NEXT)


class ReportArapOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    side: Scalar = Field(None, description="ar 或 ap")
    as_of: Scalar = Field(None, description="截止日期")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按往来单位编码排序：partner、name、debit、credit、balance。应收 balance = 借 − 贷，"
        "应付 balance = 贷 − 借。" + _MONEY,
    )
    next: Scalar = Field(None, description=_NEXT)


class ReportAgingOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    side: Scalar = Field(None, description="ar 或 ap")
    as_of: Scalar = Field(None, description="截止日期")
    basis: Scalar = Field(None, description="账龄起算：document 或 due")
    group_by: Scalar = Field(None, description="分组：partner、person 或 partner_person")
    default_credit_days: int | None = Field(None, description="请求给了 default_credit_days 时原样返回")
    buckets: list[dict[str, Any]] | None = Field(
        None,
        description="账龄区间：key、from、to（天）。第一项是 not_due（未到期，账龄 ≤ 0），最后一项没有上限",
    )
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按分组键排序（往来单位编码、业务员编码）。partner 分组：partner、name；person 分组："
        "person_code、person_name、person_source（document 单据、customer / vendor 档案专管业务员、mixed 两者都有；"
        "没有业务员时 person_code 等三项为 null）；partner_person 两组都有。之后是 balance、aging（与 buckets "
        "一一对应）、prepaid（未核销的预收或预付，正数），basis=due 时另有 overdue（已逾期金额 = aging 除 not_due "
        "外的合计）。balance = aging 合计 − prepaid。" + _MONEY,
    )
    next: Scalar = Field(None, description=_NEXT)


class ReportBomOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    parent: Scalar = Field(None, description="母件存货编码")
    as_of: Scalar = Field(None, description="生效日期")
    levels: Scalar = Field(None, description="展开层数")
    bom_id: Scalar = Field(None, description="物料清单主键（bom_bom.BomId）")
    version: Scalar = Field(None, description="物料清单版本号")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按层级、母件、序号、子件排序：level、parent、component、name、spec、sort、qty_n、qty_d、qty、path。"
        "qty 是每 1 个根母件累计需要的数量；成环的子件不再展开",
    )
    truncated: Scalar = Field(None, description="行数超过 limit 时为 true（物料清单不分页）")
