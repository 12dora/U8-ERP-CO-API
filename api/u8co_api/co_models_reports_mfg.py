"""生产类报表 /v1/co/reports/production_daily、mo_progress、mo_consumption 的请求和响应。字段名与桥一致，
响应放行桥多给的字段。

数量是数字（六位小数）。三张都是聚合结果，没有游标：超过 limit 时 truncated 为 true。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import DATE, PASS, Scalar
from u8co_api.co_models_reports import CodePrefix, Word, _real_date

_QTY = "数量为数字（存货主计量单位，六位小数）"
_TRUNCATED = "为 true 时结果超过 limit，被截掉了一部分；缩小区间或加过滤条件"
_CODES = "1 到 20 个，每个 1 到 60 个字符"
_CLASS = "存货分类编码前缀（含下级），1 到 40 位数字、字母、点或短横"


class _Range(CoAuth):
    date_from: str = Field(..., pattern=DATE, description="起始日期 yyyy-MM-dd（含）")
    date_to: str | None = Field(None, pattern=DATE, description="截止日期 yyyy-MM-dd（含），缺省为 date")

    @field_validator("date_from", "date_to")
    @classmethod
    def _dates(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _order(self) -> _Range:
        if self.date_to is not None and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to")
        return self


class ProductionDailyIn(_Range):
    group_by: Literal["day", "month"] | None = Field(None, description="day 按日、month 按月，缺省 day")
    by: Literal["product", "class"] | None = Field(None, description="product 按产品、class 按存货分类，缺省 product")
    class_grade: StrictInt | None = Field(None, ge=1, le=9, description="by=class 时往上取的分类级次，1 到 9，缺省 2")
    inv: list[Word] | None = Field(None, min_length=1, max_length=20, description="存货编码，" + _CODES)
    inv_class: CodePrefix | None = Field(None, description=_CLASS)
    include_unverified: StrictBool | None = Field(None, description="为 true 时连未审核的入库单一起算。缺省只含已审核")
    limit: StrictInt | None = Field(None, ge=1, le=5000, description="最多返回的行数，1 到 5000，缺省 1000")


class MoProgressIn(_Range):
    inv: list[Word] | None = Field(None, min_length=1, max_length=20, description="产品存货编码，" + _CODES)
    inv_class: CodePrefix | None = Field(None, description="产品所属" + _CLASS)
    open_only: StrictBool | None = Field(None, description="为 true 时只要没关闭的订单行（含未审核的计划行）。缺省全部")
    limit: StrictInt | None = Field(None, ge=1, le=2000, description="最多返回的订单行数，1 到 2000，缺省 500")


class MoConsumptionIn(_Range):
    parent: list[Word] | None = Field(None, min_length=1, max_length=20, description="母件（产品）存货编码，" + _CODES)
    inv_class: CodePrefix | None = Field(None, description="母件所属" + _CLASS)
    limit: StrictInt | None = Field(None, ge=1, le=2000, description="最多返回的（母件，子件）行，1 到 2000，缺省 500")


class _Items(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    count: Scalar = Field(None, description="返回的行数")
    truncated: Scalar = Field(None, description=_TRUNCATED)


class ProductionDailyOut(_Items):
    days: Scalar = Field(None, description="区间内有入库的日期个数（同样的过滤条件，不受 limit 影响）")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按日期、数量从大到小排序：date（yyyy-MM-dd 或 yyyy-MM）、inv_code、inv_name、unit（by=product）"
        "或 class_code、class_name（by=class）、qty、docs（入库单张数）。" + _QTY,
    )


class MoProgressOut(_Items):
    items: list[dict[str, Any]] | None = Field(
        None,
        description="每个订单行一行，按制单日期、订单号、行号排序：mo_code、mo_line、create_date、start_date、due_date、"
        "inv_code、inv_name、unit、dept_name、qty（订单数量）、qualified_in（合格入库数量累计）、closed、"
        "status（open 生产中、closed 已关闭、planned 未审核的计划）。" + _QTY,
    )


class MoConsumptionOut(_Items):
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按母件、子件编码排序：parent_code、parent_name、parent_unit、mo_count（订单张数）、mo_qty（订单数量合计）、"
        "qualified_in（合格入库合计）、comp_code、comp_name、comp_unit、bom_qty（子件标准用量合计）、"
        "issued_qty（子件已领数量合计）。母件的数量在各子件行上重复。" + _QTY,
    )


_DB = "只查数据库，不调用 U8 组件"

PRODUCTION_DAILY_SUMMARY = "产成品入库日报"
PRODUCTION_DAILY_DOC = op_doc(
    "按日（或按月）、按产品（或存货分类）汇总产成品入库的数量和入库单张数。",
    (
        "用法",
        (
            "date_from 必填；date_to 缺省为 date；group_by 选 day 或 month；by 选 product 或 class",
            "by=class 时按 class_grade 级往上取祖先分类（存货的分类比该级次浅时保持自己的分类），缺省 2",
            "可按存货编码 inv、存货分类前缀 inv_class 过滤",
        ),
    ),
    (
        "规则",
        (
            "来源是产成品入库单（rdrecord10 / rdrecords10）；缺省只含已审核，include_unverified 为 true 时含未审核",
            "qty 是存货主计量单位下的数量，unit 为计量单位名称；吨等换算由调用方做",
            "days 是区间内有入库的日期个数，不受 limit 影响",
            _DB,
        ),
    ),
    ("限制", ("最多返回 limit 行；超过时 truncated 为 true，不分页",)),
    ("权限", ("数据权限同产成品入库单：部门、仓库、存货等；存货按行过滤，只统计有权限的行",)),
)

MO_PROGRESS_SUMMARY = "生产订单完工状况"
MO_PROGRESS_DOC = op_doc(
    "按订单行列出生产订单的数量、合格入库数量和状态。",
    (
        "用法",
        (
            "date_from 必填，按订单制单日期取区间；date_to 缺省为 date",
            "可按产品存货编码 inv、产品分类前缀 inv_class 过滤；open_only 只要没关闭的订单行",
        ),
    ),
    (
        "规则",
        (
            "qualified_in 是订单行上的合格入库数量累计（QualifiedInQty），完工率等比值由调用方算",
            "status：closed 已关闭（含合格入库达到订单数量时 U8 自动关闭）、open 已审核生产中、planned 未审核；"
            "U8 订单行没有单独的锁定状态",
            _DB,
        ),
    ),
    ("限制", ("最多返回 limit 个订单行；超过时 truncated 为 true，不分页",)),
    ("权限", ("数据权限同生产订单：存货、部门、仓库，按订单行过滤",)),
)

MO_CONSUMPTION_SUMMARY = "生产订单用料分析"
MO_CONSUMPTION_DOC = op_doc(
    "按母件（产品）和子件（材料）列出区间内已审核生产订单的标准用量和已领数量。",
    (
        "用法",
        (
            "date_from 必填，按订单制单日期取区间；date_to 缺省为 date",
            "可按母件存货编码 parent、母件分类前缀 inv_class 过滤",
        ),
    ),
    (
        "规则",
        (
            "只含已审核（含已关闭）的订单行；副产品不计",
            "bom_qty 是子件上的用量合计（Qty，按订单数量折算后的标准用量）；issued_qty 是子件已领数量累计（IssQty）",
            "qualified_in 是母件订单行的合格入库数量合计",
            "单耗 = issued_qty ÷ qualified_in，标准单耗 = bom_qty ÷ mo_qty，由调用方算",
            _DB,
        ),
    ),
    ("限制", ("最多返回 limit 个（母件，子件）行；超过时 truncated 为 true，不分页",)),
    ("权限", ("数据权限同生产订单：存货、部门、仓库，按母件订单行过滤",)),
)
