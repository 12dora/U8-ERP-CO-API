"""库存与销售支持报表 /v1/co/reports/stock_ledger、stock_summary、position_stock、batch_stock、customer_credit、price_list
的请求和响应。字段名与桥一致，响应放行桥多给的字段。

数量是数字（六位小数），金额是数字（本币，两位小数）。after 是桥给的不透明游标，原样传回。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import DATE, PASS, Scalar
from u8co_api.co_models_reports import _AFTER, _NEXT, After, CodePrefix, Word, _real_date

_LIMIT = "每页条数，1 到 1000，缺省 200"
_QTY = "数量为数字（六位小数）"
_UNVERIFIED = "为 true 时连未审核的收发记录一起算（期初也含）。缺省只含已审核，与现存量一致"
_NONZERO = "缺省 true：去掉数量为 0 的行"


def _order(low: str | None, high: str | None) -> None:
    if low is not None and high is not None and low > high:
        raise ValueError("date_from 不能晚于 date_to")


class _Range(CoAuth):
    date_from: str = Field(..., pattern=DATE, description="起始日期 yyyy-MM-dd（含），期初是这一天之前的累计")
    date_to: str | None = Field(None, pattern=DATE, description="截止日期 yyyy-MM-dd（含），缺省为 date")
    include_unverified: StrictBool | None = Field(None, description=_UNVERIFIED)
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description=_LIMIT)

    @field_validator("date_from", "date_to")
    @classmethod
    def _dates(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _range(self) -> _Range:
        _order(self.date_from, self.date_to)
        return self


class ReportStockLedgerIn(_Range):
    inv: Word = Field(..., description="存货编码，1 到 60 个字符")
    wh: Word | None = Field(None, description="仓库编码等于。缺省全部仓库合在一起滚动结存")
    batch: Word | None = Field(None, description="批号等于")

    def audit_ref(self) -> str:
        return self.inv


class ReportStockSummaryIn(_Range):
    wh: Word | None = Field(None, description="仓库编码等于")
    inv: Word | None = Field(None, description="存货编码等于")
    inv_class: CodePrefix | None = Field(None, description="存货分类编码前缀（含下级），1 到 40 位数字、字母、点或短横")
    by_wh: StrictBool | None = Field(None, description="缺省 true：按存货 + 仓库分行；false 时每个存货一行（仓库合计）")
    nonzero: StrictBool | None = Field(None, description="缺省 true：去掉期初、入库、出库都为 0 的行")


class _Stock(CoAuth):
    wh: Word | None = Field(None, description="仓库编码等于")
    inv: Word | None = Field(None, description="存货编码等于")
    batch: Word | None = Field(None, description="批号等于")
    nonzero: StrictBool | None = Field(None, description=_NONZERO)
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description=_LIMIT)


class ReportPositionStockIn(_Stock):
    position: CodePrefix | None = Field(None, description="货位编码前缀（含下级货位）")


class ReportBatchStockIn(_Stock):
    expiring_before: str | None = Field(
        None,
        pattern=DATE,
        description="只要失效日期（valid_until）不晚于这一天的批次 yyyy-MM-dd，用于保质期预警。没有失效日期的批次不列",
    )

    @field_validator("expiring_before")
    @classmethod
    def _date(cls, value: str | None) -> str | None:
        return _real_date(value)


class ReportCustomerCreditIn(CoAuth):
    customer: list[Word] | None = Field(
        None,
        min_length=1,
        max_length=20,
        description="客户编码，1 到 20 个。缺省全部客户（按编码翻页）",
    )
    controlled_only: StrictBool | None = Field(None, description="为 true 时只列档案上勾了信用额度控制的客户")
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=200, description="每页条数，1 到 200，缺省 100")


class ReportPriceListIn(CoAuth):
    kind: Literal["customer", "inventory", "vendor"] = Field(
        ...,
        description="customer 客户价格表、inventory 存货价格表（销售）、vendor 供应商存货价格表（采购）",
    )
    customer: Word | None = Field(None, description="客户编码，只能和 kind=customer 一起用。连同该客户所属分类的价格")
    vendor: Word | None = Field(None, description="供应商编码，只能和 kind=vendor 一起用")
    inv: Word | None = Field(None, description="存货编码等于")
    as_of: str | None = Field(
        None,
        pattern=DATE,
        description="生效日期 yyyy-MM-dd：只列这一天有效、未失效的价格。缺省为 date",
    )
    all_dates: StrictBool | None = Field(None, description="为 true 时不按日期过滤（含已过期、未生效、已失效的行）")
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=1000, description=_LIMIT)

    @field_validator("as_of")
    @classmethod
    def _as_of(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _pairs(self) -> ReportPriceListIn:
        if self.customer is not None and self.kind != "customer":
            raise ValueError("customer 只能和 kind=customer 一起用")
        if self.vendor is not None and self.kind != "vendor":
            raise ValueError("vendor 只能和 kind=vendor 一起用")
        if self.all_dates and self.as_of is not None:
            raise ValueError("as_of 不能和 all_dates 一起用")
        return self

    def audit_ref(self) -> str:
        return self.kind


class _Page(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    next: Scalar = Field(None, description=_NEXT)


class ReportStockLedgerOut(_Page):
    inv: Scalar = Field(None, description="存货编码")
    inv_name: Scalar = Field(None, description="存货名称")
    date_from: Scalar = Field(None, description="起始日期")
    date_to: Scalar = Field(None, description="截止日期")
    opening: Scalar = Field(None, description="date_from 之前的结存（期初）")
    carry: Scalar = Field(None, description="本页第一行之前的结存。第一页等于 opening")
    closing: Scalar = Field(None, description="date_to 的结存；只在最后一页给出，其余页为 null")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按日期、表体行号排序：date、type（purchase_in、other_in、other_out、product_in、material_out、"
        "sale_out、stock_opening）、id、line_id、code、wh_code、wh_name、batch、rd_code、source、verified、in_qty、"
        "out_qty、balance（本行之后的结存）。" + _QTY,
    )


class ReportStockSummaryOut(_Page):
    date_from: Scalar = Field(None, description="起始日期")
    date_to: Scalar = Field(None, description="截止日期")
    by_wh: Scalar = Field(None, description="是否按仓库分行")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按存货、仓库编码排序：inv_code、inv_name、inv_std、inv_class、wh_code、wh_name（by_wh=false 时为 null）、"
        "opening、in_qty、out_qty、closing。只有数量，没有金额。" + _QTY,
    )


class ReportPositionStockOut(_Page):
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按货位存量主键排序：id、wh_code、wh_name、position、position_name、inv_code、inv_name、inv_std、"
        "batch、free1 到 free10、qty、qty_aux、made_date、valid_until、expires。只含已指定货位的数量。" + _QTY,
    )


class ReportBatchStockOut(_Page):
    expiring_before: Scalar = Field(None, description="失效日期上限，没给时为 null")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按仓库、存货、批号排序：wh_code、wh_name、inv_code、inv_name、inv_std、batch、qty、qty_aux、qty_frozen、"
        "made_date、valid_until、expires（多行时取最早）、rows（合并的现存量行数）。" + _QTY,
    )


class ReportCustomerCreditOut(_Page):
    credit_control: Scalar = Field(None, description="销售选项：是否有客户信用额度控制")
    check_point: Scalar = Field(None, description="信用检查点：save 保存（含未审核单据）或 verify 审核")
    balance_table: Scalar = Field(None, description="销售选项：信用余额控制用余额表")
    ar_enabled: Scalar = Field(None, description="应收款管理是否已启用（应收余额只在启用时计入 used）")
    formula: dict[str, Any] | None = Field(None, description="额度检查公式里各项是否打开（order、dispatch、invoice、ar、expense 等）")
    unsupported: list[Any] | None = Field(None, description="公式里打开了、本报表不计算的项（合同结算单、出口单据）")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按客户编码排序：code、name、controlled、credit_line、credit_days、credit_days_controlled、credit_grade、"
        "credit_company、order、dispatch、invoice、ar、expense、used、available（受控客户 = credit_line − used，否则 null）。"
        "金额为数字（本币，两位小数）",
    )


class ReportPriceListOut(_Page):
    kind: Scalar = Field(None, description="价格表种类")
    as_of: Scalar = Field(None, description="生效日期；all_dates 时为 null")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按价格表主键排序：id、inv_code、inv_name、inv_std、currency、start_date、end_date、min_qty、"
        "tax_included、promotion、memo；customer 另有 customer、customer_class、invalid、quote、discount_rate、price、"
        "min_price；inventory 另有 invalid、levels（level、price、tax_price）；vendor 另有 vendor、max_qty、price、"
        "tax_price、tax_rate、supply_type",
    )
