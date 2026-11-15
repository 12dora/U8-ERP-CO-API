"""账务核对报表 /v1/co/reports/close_check、ia_anomalies、cost_unit 的请求和响应。
字段名与桥一致，响应放行桥多给的字段。

金额是数字（本币，两位小数），数量是数字（六位小数），单位成本是数字（六位小数，元 / 存货主计量单位）。
三张报表都不分页：close_check 的示例单号最多 5 个，ia_anomalies 的两张清单各用 limit 截断，cost_unit 按期间范围取。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, StrictInt, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import PASS, Scalar
from u8co_api.co_models_reports import CodePrefix, Word

_DB = "只查数据库，不调用 U8 组件"
_FISCAL = "会计年度，2000 到 2099。缺省为登录日期（date）的年份；year 是账套库年度，不参与判断"
_PERIOD_DEFAULT = "缺省：会计年度是登录年度时取登录月份，否则取 12"
_MONEY = "金额为数字（本币，两位小数）"
_INV = "存货编码，1 到 20 个，每个 1 到 60 个字符"

CLOSE_CHECK_SUMMARY = "月末结账检查"
CLOSE_CHECK_DOC = op_doc(
    "查某个会计期间各模块是否已结账，以及没结账的模块还有多少单据挡着结账（未审核、未记账、未制单等），每项最多给 5 个示例单号。",
    (
        "用法",
        (
            "period 取 1 到 12，缺省是登录月份（会计年度不是登录年度时取 12）",
            "closed 是销售 SA、采购 PU、库存 ST、存货核算 IA、总账 GL、应收 AR、应付 AP、成本 CA、固定资产 FA 在该期间的结账标志",
            "checks 只列还没结账的模块相关的检查项，个数为 0 的也列出；模块全部结账时 checks 为空",
        ),
    ),
    (
        "规则",
        (
            "检查项的条件同月末结账（periods/close）的结账前检查：发货单（退货单）未审核 unaudited_dispatch、销售发票未复核 "
            "unaudited_sale_invoice、采购发票未复核 unaudited_purchase_invoice（日期在本期末及以前）、库存单据未审核 "
            "unverified_purchase_in、unverified_product_in、unverified_material_out、unverified_sale_out、unverified_other_in、"
            "unverified_other_out、总账未记账凭证 unposted_vouchers、存货核算未记账单据 unbooked_ia、应收应付未制单 "
            "unvouchered_ar、unvouchered_ap、损益类科目没做期间损益结转 pl_not_transferred",
            "count 是单据张数（unposted_vouchers 按凭证、pl_not_transferred 按科目、unbooked_ia 按单据）；samples 是最多 5 个示例单号"
            "（pl_not_transferred 是科目编码）",
            "应收、应付的未制单只有往来选项要求月末结账前全部制单时才挡结账，这里一律列出个数",
            _DB,
        ),
    ),
)

IA_ANOMALIES_SUMMARY = "存货核算检查"
IA_ANOMALIES_DOC = op_doc(
    "列出暂估未回冲的采购入库行和存货核算还没记账的库存单据行。",
    (
        "用法",
        (
            "period 取 1 到 12，缺省是登录月份（会计年度不是登录年度时取 12）；period_end 是该期间的最后一天",
            "unbilled 和 unbooked 各自最多返回 limit 行（缺省 50），count 是全部行数；unbilled.est_amount 是全部行的估价合计",
        ),
    ),
    (
        "规则",
        (
            "暂估未回冲：已审核的采购入库单行（不含库存期初），日期在期末及以前，入库数量不等于已开票数量；"
            "residual_qty = qty − billed_qty，est_amount = residual_qty × 入库单无税单价，days = 期末日 − 入库日期；"
            "按估价从大到小排。采购发票还没到的正常入库也在其中；红字入库未开红字发票时 residual_qty、est_amount 为负数，照实相加",
            "开票状态（billed_qty）是查询时（billing_as_of，即登录日期）的，不是期末当天的：查过去的期间时，之后才开的发票已经算进 billed_qty",
            "未记账单据：已审核的采购入库 purchase_in、其他入库 other_in、其他出库 other_out、产成品入库 product_in、"
            "材料出库 material_out、销售出库 sale_out 单据行，日期在本期，存货核算里没有对应记录；数量为 0 的行、"
            "仓库不参与成本核算（bInCost = 0）的单据存货核算不记，不算；"
            "按日期从早到晚排",
            "数据权限：供应商、存货、仓库，两张清单都按；" + _MONEY,
            _DB,
        ),
    ),
)

COST_UNIT_SUMMARY = "完工产品单位成本"
COST_UNIT_DOC = op_doc(
    "查成本核算模块每个产品每个会计期间的单位成本，按成本要素（材料、人工、辅助、制造费用等）列出。",
    (
        "用法",
        (
            "period_from、period_to 取 1 到 12；都不给时取最近一个成本核算已结账的期间（不晚于登录月份，"
            "会计年度不是登录年度时不晚于 12 月）；只给 period_from 时截止期间取最近已结账的期间，比 period_from 早就取 period_from",
            "没有已结账期间又没给期间时，响应里没有 period_from、period_to，items 为空",
            "inv 指定存货编码，inv_class 是存货分类编码前缀（含下级）",
        ),
    ),
    (
        "规则",
        (
            "elements 的键是成本要素编码，值是每个主计量单位的单位成本（元）；element_names 给出出现过的要素编码对应的名称；"
            "unit_total 是各要素之和",
            "qty 是该期间完工数量（成本核算的生产订单完工数量按产品合计，与产成品入库数量一致），没有时为 null",
            "未结账的期间只要有成本数据也照列；closed_periods 是该会计年度成本核算已结账的期间",
            "数据权限：存货。" + _MONEY,
            _DB,
        ),
    ),
)


class CloseCheckIn(CoAuth):
    fiscal_year: StrictInt | None = Field(None, ge=2000, le=2099, description=_FISCAL)
    period: StrictInt | None = Field(None, ge=1, le=12, description="会计期间，1 到 12。" + _PERIOD_DEFAULT)


class IaAnomaliesIn(CoAuth):
    fiscal_year: StrictInt | None = Field(None, ge=2000, le=2099, description=_FISCAL)
    period: StrictInt | None = Field(None, ge=1, le=12, description="会计期间，1 到 12。" + _PERIOD_DEFAULT)
    limit: StrictInt | None = Field(None, ge=1, le=200, description="两张清单各自最多返回的行数，1 到 200，缺省 50")


class CostUnitIn(CoAuth):
    fiscal_year: StrictInt | None = Field(None, ge=2000, le=2099, description=_FISCAL)
    period_from: StrictInt | None = Field(None, ge=1, le=12, description="起始期间，1 到 12。缺省取截止期间")
    period_to: StrictInt | None = Field(
        None,
        ge=1,
        le=12,
        description="截止期间，1 到 12，不小于 period_from。缺省取最近一个成本核算已结账的期间",
    )
    inv: list[Word] | None = Field(None, min_length=1, max_length=20, description=_INV)
    inv_class: CodePrefix | None = Field(None, description="存货分类编码前缀（含下级），1 到 40 位数字、字母、点或短横")

    @model_validator(mode="after")
    def _periods(self) -> CostUnitIn:
        if self.period_from is not None and self.period_to is not None and self.period_from > self.period_to:
            raise ValueError("period_from 不能大于 period_to")
        return self


class _Out(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")


class CloseCheckOut(_Out):
    fiscal_year: Scalar = Field(None, description="会计年度")
    period: Scalar = Field(None, description="会计期间")
    closed: dict[str, Any] | None = Field(
        None,
        description="各模块在该期间是否已结账：SA、PU、ST、IA、GL、AR、AP、CA、FA（布尔）；期间没有记录时都是 false",
    )
    checks: list[dict[str, Any]] | None = Field(
        None,
        description="还没结账的模块相关的检查项：module（模块代码）、item（检查项）、count（单据张数，0 也列出）、"
        "samples（最多 5 个示例单号，pl_not_transferred 为科目编码）",
    )


class IaAnomaliesOut(_Out):
    fiscal_year: Scalar = Field(None, description="会计年度")
    period: Scalar = Field(None, description="会计期间")
    period_end: Scalar = Field(None, description="期间最后一天 yyyy-MM-dd")
    billing_as_of: Scalar = Field(None, description="开票状态（billed_qty）的时点：查询时的登录日期，不是期末当天")
    unbilled: dict[str, Any] | None = Field(
        None,
        description="暂估未回冲：count（全部行数）、est_amount（全部行的估价合计）、items（按估价从大到小，最多 limit 行）："
        "code、date、vendor_name、inv_code、inv_name、unit、qty、billed_qty、residual_qty、est_amount、days。" + _MONEY,
    )
    unbooked: dict[str, Any] | None = Field(
        None,
        description="存货核算未记账的库存单据行：count（全部行数）、items（按日期从早到晚，最多 limit 行）："
        "type（purchase_in、other_in、other_out、product_in、material_out、sale_out）、code、date、wh_name、"
        "inv_code、inv_name、unit、qty",
    )


class CostUnitOut(_Out):
    fiscal_year: Scalar = Field(None, description="会计年度")
    period_from: Scalar = Field(None, description="实际查询的起始期间；没有可查的期间时不出现")
    period_to: Scalar = Field(None, description="实际查询的截止期间；没有可查的期间时不出现")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按存货编码、期间排序：inv_code、inv_name、unit、period（yyyy-MM）、qty（完工数量，没有为 null）、"
        "elements（成本要素编码 → 单位成本，元 / 主计量单位）、unit_total（各要素之和）",
    )
    element_names: dict[str, Any] | None = Field(
        None,
        description="出现过的成本要素编码 → 名称（如 0 材料费用、1 人工费用、2 辅助费用、3 制造费用）",
    )
    closed_periods: list[Any] | None = Field(None, description="该会计年度成本核算已结账的期间，yyyy-MM，升序")
