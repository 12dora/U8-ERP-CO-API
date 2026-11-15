"""质量统计报表 /v1/co/reports/qm_indicators、qm_pass_rate 的请求和响应。字段名与桥一致，响应放行桥多给的字段。

数量是数字（六位小数）。来料检验单（QM03）和产品检验单（QM04）分开统计，kind 必填。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import DATE, PASS, Scalar
from u8co_api.co_models_reports import CodePrefix, Word, _real_date

Kind = Literal["incoming", "product"]
GroupBy = Literal["month", "vendor", "inventory"]

_KIND = "incoming 来料检验单（QM03）、product 产品检验单（QM04）"
_UNVERIFIED = "为 true 时连未审核的检验单一起算。缺省只含已审核（审核人非空）"
_LIMIT = "最多返回的行数，1 到 1000，缺省 200"
_QTY = "数量为数字（六位小数）"

QM_INDICATORS_SUMMARY = "检验指标统计"
QM_INDICATORS_DOC = op_doc(
    "按存货和检验指标统计来料或产品检验单：检验行数、数值型检验值的均值、最小、最大、样本标准差、不良行数、逐月走势和最近的不良批次。",
    (
        "用法",
        (
            "kind 必填；date_from 必填，date_to 缺省为登录日期（date）",
            "inv、inv_class、vendor（只限 incoming）、guide 缩小范围；guide 是检验指标编码",
            "每个（存货、指标）一行，按存货编码、指标编码排序；超过 limit 时 truncated 为 true",
        ),
    ),
    (
        "规则",
        (
            "缺省只含已审核的检验单；include_unverified 为 true 时连未审核的一起算",
            "只有能解析成小数的检验值进入均值、最小、最大和标准差（numeric_n 是个数），外观等文字结果只计行数",
            "stdev 是样本标准差，数值少于 2 个时为 null；没有数值时均值、最小、最大也为 null",
            "单项判定含「不良」或「不合格」计入 bad_n；bad_batches 取最新的 10 条",
            "standard 取出现次数最多的标准值；guide_unit 是检验行上的计量单位",
            "区间内明细行超过 100000 时 400，请缩小日期范围或指定存货",
            "只查数据库，不调用 U8 组件；按检验统计表的功能权限，存货（来料另有供应商）的数据权限过滤",
        ),
    ),
)

QM_PASS_RATE_SUMMARY = "检验合格率统计"
QM_PASS_RATE_DOC = op_doc(
    "按月份、供应商（仅来料）或存货统计来料或产品检验单的检验单数和各类数量，不含比率。",
    (
        "用法",
        (
            "kind 必填；date_from 必填，date_to 缺省为登录日期（date）",
            "group_by 缺省 month；vendor 只限 kind=incoming",
            "超过 limit 时 truncated 为 true，totals 仍合计全部分组",
        ),
    ),
    (
        "规则",
        (
            "一张检验单表头算一单；结论含「不合格」或「不接受」，或结论为空且不合格数量大于 0，算不合格单（fail_docs）",
            "结论为空且没有不合格数量的算未判定单（undecided_docs），不算合格也不算不合格；其余算合格单",
            "pass_docs = docs − fail_docs − undecided_docs；结论含「让步」且不是不合格单的计入 concession_docs，并按合格计",
            "qty 报检数量、reg_qty 合格数量、con_qty 让步数量、dis_qty 不合格数量，各自按表头合计",
            "缺省只含已审核的检验单；不含其他检验单（QM15）",
            "只查数据库，不调用 U8 组件；按检验统计表的功能权限，存货（来料另有供应商）的数据权限过滤",
        ),
    ),
)


class _QmIn(CoAuth):
    kind: Kind = Field(..., description=_KIND)
    date_from: str = Field(..., pattern=DATE, description="起始日期 yyyy-MM-dd（含）")
    date_to: str | None = Field(None, pattern=DATE, description="截止日期 yyyy-MM-dd（含），缺省为 date")
    inv: list[Word] | None = Field(None, min_length=1, max_length=20, description="存货编码，1 到 20 个")
    inv_class: CodePrefix | None = Field(None, description="存货分类编码前缀（含下级），1 到 40 位数字、字母、点或短横")
    vendor: list[Word] | None = Field(
        None,
        min_length=1,
        max_length=20,
        description="供应商编码，1 到 20 个，只能和 kind=incoming 一起用",
    )
    include_unverified: StrictBool | None = Field(None, description=_UNVERIFIED)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description=_LIMIT)

    @field_validator("date_from", "date_to")
    @classmethod
    def _dates(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _pairs(self) -> _QmIn:
        if self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to")
        if self.vendor is not None and self.kind != "incoming":
            raise ValueError("vendor 只能和 kind=incoming 一起用")
        return self

    def audit_ref(self) -> str:
        return self.kind


class QmIndicatorsIn(_QmIn):
    guide: list[Word] | None = Field(None, min_length=1, max_length=20, description="检验指标编码，1 到 20 个")


class QmPassRateIn(_QmIn):
    group_by: GroupBy | None = Field(
        None,
        description="month 按月份（缺省）、vendor 按供应商（只限 kind=incoming）、inventory 按存货",
    )

    @model_validator(mode="after")
    def _group(self) -> QmPassRateIn:
        if self.group_by == "vendor" and self.kind != "incoming":
            raise ValueError("group_by=vendor 只能和 kind=incoming 一起用")
        return self


class _Out(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    kind: Scalar = Field(None, description="检验单类型")
    date_from: Scalar = Field(None, description="起始日期")
    date_to: Scalar = Field(None, description="截止日期")
    include_unverified: Scalar = Field(None, description="是否含未审核的检验单")
    count: Scalar = Field(None, description="本次返回的行数")
    truncated: Scalar = Field(None, description="为 true 表示还有更多行，缩小范围或加大 limit")


class QmIndicatorsOut(_Out):
    items: list[dict[str, Any]] | None = Field(
        None,
        description="每个（存货、指标）一行：inv_code、inv_name、guide_code、guide_name、guide_unit、standard、n（检验行数）、"
        "numeric_n（数值行数）、mean、min、max、stdev（样本标准差）、bad_n（不良行数）、months（month、n、mean、bad_n）、"
        "bad_batches（最新 10 条：date、check_code、batch、value）。" + _QTY,
    )


class QmPassRateOut(_Out):
    group_by: Scalar = Field(None, description="分组方式")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="分组键（month，或 vendor_code、vendor_name，或 inv_code、inv_name、unit）加 docs、pass_docs、"
        "fail_docs、undecided_docs、concession_docs、qty、reg_qty、con_qty、dis_qty。" + _QTY,
    )
    totals: dict[str, Any] | None = Field(
        None,
        description="全部分组的合计：docs、pass_docs、fail_docs、undecided_docs、concession_docs、qty、reg_qty、con_qty、dis_qty",
    )
