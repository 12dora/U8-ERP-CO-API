"""采购类报表 /v1/co/reports/purchase_analysis、arrival_otif 的请求和响应。字段名与桥一致，响应放行桥多给的字段。

金额是数字（本币，两位小数），数量是数字（六位小数）。两张报表都不分页：purchase_analysis 用 limit 截断，
arrival_otif 的分组和逾期明细各有上限。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, field_validator, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import DATE, PASS, Scalar
from u8co_api.co_models_reports import CodePrefix, Word, _real_date

_DB = "只查数据库，不调用 U8 组件"
_DATE_FROM = "起始日期 yyyy-MM-dd（含）"
_VENDOR = "供应商编码，1 到 20 个，每个 1 到 60 个字符"
_INV = "存货编码，1 到 20 个，每个 1 到 60 个字符"

PURCHASE_ANALYSIS_SUMMARY = "采购综合统计"
PURCHASE_ANALYSIS_DOC = op_doc(
    "按物料、供应商、月份汇总采购数量和金额。",
    (
        "用法",
        (
            "group_by 取 material、vendor、month 中的 1 到 3 个，缺省按物料",
            "source=invoice（缺省）统计采购发票，source=receipt 统计已审核的采购入库单",
            "date_to 缺省为登录日期（date）；按金额从大到小排序，超过 limit 的行不返回，truncated 为 true",
        ),
    ),
    (
        "规则",
        (
            "发票：不含期初发票，amount 无税、amount_tax 价税合计，均为本币（元）；红字发票为负数，照实相加",
            "入库单：只含已审核、非期初的入库行，amount 取无税金额，amount_tax 为 null",
            "数量是存货主计量单位的数量；group_by 里没有 material 时各行混有不同计量单位，qty 为 null",
            "totals 是全部明细的合计（不受 limit 影响），docs 是去重后的单据张数",
            "单价由调用方用 amount ÷ qty 自己算",
            _DB,
        ),
    ),
)

ARRIVAL_OTIF_SUMMARY = "采购到货及时率"
ARRIVAL_OTIF_DOC = op_doc(
    "已审核采购订单行按计划到货日统计准时、延迟、逾期未到，并列出逾期未到货的订单行。",
    (
        "用法",
        (
            "date_from、date_to 是计划到货日（订单行的到货日期）的窗口，date_to 缺省不设上限",
            "group_by 取 vendor（缺省）、material 或 none；none 时 groups 为空，只有 totals",
            "按登录日期（date）判断逾期，响应的 as_of 就是它",
        ),
    ),
    (
        "规则",
        (
            "首次到货取已审核到货单和已审核采购入库单里最早的日期（多数订单行直接入库、不开到货单），"
            "只比日期：不晚于计划日为 on_time，晚于为 late",
            "只认已审核单据，每个订单行恰好落在三类之一：arrived_lines（有首次到货，部分到货也算）、"
            "open_overdue（没有首次到货，计划日早于 as_of）、not_due（没有首次到货，计划日不早于 as_of）；"
            "lines = arrived_lines + open_overdue + not_due",
            "overdue 明细的 arrived_qty 是订单行上记录的到货累计，含未审核单据，仅供参考",
            "已关闭且从未到货的订单行视为作废，不进统计",
            "groups 按订单行数从多到少，最多 1000 组，超出时 truncated 为 true",
            "overdue 按计划日从早到晚，最多 overdue_limit 行；overdue_total 是逾期未到货行的总数",
            _DB,
        ),
    ),
)


def _order(low: str, high: str | None) -> None:
    if high is not None and low > high:
        raise ValueError("date_from 不能晚于 date_to")


class _Range(CoAuth):
    date_from: str = Field(..., pattern=DATE, description=_DATE_FROM)

    @field_validator("date_from")
    @classmethod
    def _from(cls, value: str) -> str:
        _real_date(value)
        return value


class PurchaseAnalysisIn(_Range):
    date_to: str | None = Field(None, pattern=DATE, description="截止日期 yyyy-MM-dd（含），缺省为 date")
    group_by: list[Literal["material", "vendor", "month"]] | None = Field(
        None,
        min_length=1,
        max_length=3,
        description="分组维度：material 物料、vendor 供应商、month 月份，1 到 3 个不重复，缺省 [material]",
    )
    inv: list[Word] | None = Field(None, min_length=1, max_length=20, description=_INV)
    inv_class: CodePrefix | None = Field(None, description="存货分类编码前缀（含下级），1 到 40 位数字、字母、点或短横")
    vendor: list[Word] | None = Field(None, min_length=1, max_length=20, description=_VENDOR)
    source: Literal["invoice", "receipt"] | None = Field(
        None,
        description="invoice 采购发票（缺省）；receipt 已审核的采购入库单",
    )
    limit: StrictInt | None = Field(None, ge=1, le=1000, description="返回的最多行数，1 到 1000，缺省 200")

    @field_validator("date_to")
    @classmethod
    def _to(cls, value: str | None) -> str | None:
        return _real_date(value)

    @field_validator("group_by")
    @classmethod
    def _distinct(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("group_by 不能重复")
        return value

    @model_validator(mode="after")
    def _range(self) -> PurchaseAnalysisIn:
        _order(self.date_from, self.date_to)
        return self


class ArrivalOtifIn(_Range):
    date_to: str | None = Field(None, pattern=DATE, description="计划到货日截止 yyyy-MM-dd（含），缺省不设上限")
    vendor: list[Word] | None = Field(None, min_length=1, max_length=20, description=_VENDOR)
    inv: list[Word] | None = Field(None, min_length=1, max_length=20, description=_INV)
    group_by: Literal["vendor", "material", "none"] | None = Field(
        None,
        description="分组：vendor 供应商（缺省）、material 物料、none 不分组（只有合计）",
    )
    overdue_limit: StrictInt | None = Field(None, ge=1, le=200, description="逾期明细最多行数，1 到 200，缺省 50")

    @field_validator("date_to")
    @classmethod
    def _to(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _range(self) -> ArrivalOtifIn:
        _order(self.date_from, self.date_to)
        return self


class _Out(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    date_from: Scalar = Field(None, description="起始日期")
    date_to: Scalar = Field(None, description="截止日期；arrival_otif 没给时为 null")
    group_by: Any = Field(None, description="实际使用的分组")
    truncated: Scalar = Field(None, description="是否因上限而没有返回全部行")
    totals: dict[str, Any] | None = Field(None, description="合计")


class PurchaseAnalysisOut(_Out):
    source: Scalar = Field(None, description="数据来源：invoice 或 receipt")
    count: Scalar = Field(None, description="返回的行数")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按金额从大到小：分组键（inv_code、inv_name、unit；vendor_code、vendor_name；month）、"
        "qty（分组里没有 material 时为 null）、amount、amount_tax（入库单为 null）、docs。"
        "totals 为 amount、amount_tax、docs。金额为数字（本币，两位小数），数量为数字（六位小数）",
    )


class ArrivalOtifOut(_Out):
    as_of: Scalar = Field(None, description="判断逾期的日期（登录日期）")
    arrival_basis: Scalar = Field(None, description="首次到货的取数口径：arrival_or_receipt（到货单与入库单取较早）")
    groups: list[dict[str, Any]] | None = Field(
        None,
        description="按订单行数从多到少：分组键（vendor_code、vendor_name；inv_code、inv_name、unit）、lines、"
        "arrived_lines、on_time、late、open_overdue、not_due。group_by=none 时为空数组",
    )
    overdue: list[dict[str, Any]] | None = Field(
        None,
        description="逾期未到货的订单行，计划日从早到晚：po_code、po_date、vendor_name、inv_code、inv_name、unit、qty、"
        "arrived_qty、planned_date、days_overdue。totals 为 lines、arrived_lines、on_time、late、open_overdue、not_due",
    )
    overdue_total: Scalar = Field(None, description="逾期未到货的订单行总数（可能大于 overdue 的行数）")
