"""库龄分析 /v1/co/reports/stock_age 的请求和响应。字段名与桥一致，响应放行桥多给的字段。

数量是数字（六位小数），金额是数字（本币，两位小数），单价六位小数。没有翻页：items 按 limit 截取，分段合计覆盖全部行。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import PASS, Scalar
from u8co_api.co_models_reports import CodePrefix, Word

STOCK_AGE_SUMMARY = "库龄分析"

STOCK_AGE_DOC = op_doc(
    "按存货（缺省再按仓库）列出现存量的库龄、呆滞和库存金额，并给出库龄分段合计、呆滞合计和总计。",
    (
        "用法",
        (
            "库龄自登录日期（date）算起；buckets 给分段上界（天，递增），n 个上界分成 n + 1 段，最后一段没有上界",
            "items 按 sort 排序后取前 limit 行，truncated 为 true 表示还有更多；buckets、idle_total、total 覆盖全部行",
        ),
    ),
    (
        "规则",
        (
            "现存量取现存量表（非零）；库龄 = 登录日期 − 最后一次已审核入库（采购入库、其他入库、产成品入库、库存期初）的日期，"
            "没有入库记录时 age_days 为 null，分段归最后一段",
            "呆滞：最后一次已审核出库（其他出库、材料出库、销售出库）至今不少于 idle_days 天；从没出过库的按最后入库算，"
            "入库也没有记录的算呆滞",
            "金额 = 现存量 × 成本月份（不晚于登录月份的最近一个已记账的存货核算月份）的结存单价；"
            "仓库自己没有成本时用该存货各仓合计的单价，仍没有则 unit_cost、amount 为 null",
            "没有存货核算金额权限（IA3011_01）的操作员金额为 null，items 一律按库龄排序（sort=amount 也一样），不泄露金额高低；"
            "响应的 sort 是实际用的排序",
            "不按仓库分行（by_wh=false）时数量、金额相加，库龄取各仓里最近一次入库",
            "只查数据库，不调用 U8 组件",
        ),
    ),
)

_DAYS = "天数，1 到 3650"
_CODES = "1 到 20 个编码，缺省不限"


class StockAgeIn(CoAuth):
    idle_days: StrictInt | None = Field(None, ge=1, le=3650, description="多少天没有出库算呆滞，1 到 3650，缺省 180")
    buckets: list[StrictInt] | None = Field(
        None,
        min_length=1,
        max_length=6,
        description="库龄分段的上界（天），1 到 6 个，严格递增，每个 1 到 3650。缺省 [30, 90, 180]",
    )
    wh: list[Word] | None = Field(None, min_length=1, max_length=20, description="仓库编码。" + _CODES)
    inv: list[Word] | None = Field(None, min_length=1, max_length=20, description="存货编码。" + _CODES)
    inv_class: CodePrefix | None = Field(None, description="存货分类编码前缀（含下级），1 到 40 位数字、字母、点或短横")
    by_wh: StrictBool | None = Field(None, description="缺省 true：按存货 + 仓库分行；false 时每个存货一行")
    sort: Literal["amount", "age"] | None = Field(
        None,
        description="amount 按金额从大到小（缺省，没有成本的排最后）；age 按库龄从大到小（没有入库记录的最前）",
    )
    limit: StrictInt | None = Field(None, ge=1, le=2000, description="返回的行数，1 到 2000，缺省 200")

    @field_validator("buckets")
    @classmethod
    def _buckets(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return value
        if any(day < 1 or day > 3650 for day in value) or any(a >= b for a, b in zip(value, value[1:], strict=False)):
            raise ValueError("buckets 必须是 1 到 6 个递增的天数（" + _DAYS + "）")
        return value


class StockAgeOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    as_of: Scalar = Field(None, description="计算库龄的日期（登录日期）")
    cost_month: Scalar = Field(None, description="成本取自的存货核算月份 yyyy-MM；没有已记账的月份时为 null，金额全为 null")
    by_wh: Scalar = Field(None, description="是否按仓库分行")
    idle_days: Scalar = Field(None, description="呆滞天数")
    sort: Scalar = Field(None, description="排序方式")
    count: Scalar = Field(None, description="items 的行数")
    truncated: Scalar = Field(None, description="为 true 时全部行超过 limit，items 只是前 limit 行")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="inv_code、inv_name、inv_std、unit（计量单位名）、wh_code、wh_name（by_wh=false 时没有）、qty、"
        "last_in、last_out（yyyy-MM-dd，没有为 null）、age_days、idle、unit_cost、amount（本币）",
    )
    buckets: list[dict[str, Any]] | None = Field(
        None,
        description="库龄分段：from_days（含）、to_days（含，最后一段为 null）、items（行数）、amount",
    )
    idle_total: dict[str, Any] | None = Field(None, description="呆滞合计：items、amount")
    total: dict[str, Any] | None = Field(None, description="全部合计：items、amount")
    uncosted_items: Scalar = Field(None, description="没有成本的行数（金额为 null 的行）；无金额权限时为 null")
    masked_fields: list[str] | None = Field(
        None,
        description="操作员没有存货核算金额查询权限时为 [\"amount\"]：unit_cost、amount 及各合计的 amount 为 null。有权限时省略",
    )
