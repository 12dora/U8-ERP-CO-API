"""成本核算期间数据报表 /v1/co/reports/ca_inputs 的请求和响应。字段名与桥一致，响应放行桥多给的字段。

工时、金额、系数都是数字（六位小数以内，U8 表上的列原名 → 数值）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, field_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import PASS, Scalar

CA_INPUTS_SUMMARY = "成本核算期间数据"

CaTable = Literal[
    "work_hours",
    "daily_hours",
    "labor",
    "overhead",
    "depreciation",
    "wip_fixed",
    "wip_variable",
    "distr_input",
    "product_qty",
]

CA_INPUTS_DOC = op_doc(
    "查成本管理某个会计期间的成本核算数据：工时、人工费用、制造费用、折旧费用、在产品约当系数、自定义分配项目、完工产品产量，"
    "以及成本计算和结账状态。",
    (
        "用法",
        (
            "period 取 1 到 12，缺省是登录月份（会计年度不是登录年度时取 12）",
            "tables 指定要读的表，缺省读全部有权限的表（没有权限的表列在 skipped）；limit 是每张表最多返回的行数"
            "（1 到 20000，缺省 5000），count 是该期间的全部行数",
            "work_hours 工时统计（CA_TimSt）、daily_hours 工时日报（CA_DayTiS，日期落在该月）、labor 人工费用（CA_DirMA）、"
            "overhead 制造费用（CA_ManCt）、depreciation 折旧费用（CA_DepCs）、wip_fixed 在产品固定约当（CA_OnPQu，按会计年度，"
            "与 period 无关，成本核算对象是基础对象）、wip_variable 在产品变动约当（CA_OnPCE）、distr_input 自定义分配项目（CA_DistrInput）、"
            "product_qty 完工产品产量统计（CA_ComPS）",
        ),
    ),
    (
        "规则",
        (
            "每行带解析好的编码：cost_center（成本中心编码）和 cost_center_name；成本核算对象行（工时、约当、分配项目、产量）带 "
            "object_id（成本核算对象号）、inv_code、inv_name（存货）、mo_code（生产订单号）、batch，解析不到的不输出；"
            "wip_fixed 带 base_object_id（基础对象号，没有生产订单；-1 是缺省系数行，没有对象）、inv_code、inv_name；"
            "overhead 带 item_code、item_name（制造费用要素的成本项目），按项目编码的数值排序；工时行带 type（工时类型）",
            "values 是 U8 表上的列原名 → 数值，空值不输出；depreciation 的 cFrom 是文本（取数来源，空不输出）；"
            "distr_input 给出表上除键列以外的全部数值列",
            "status.calculated_through 是成本核算最近一次成本计算到的期间（yyyy-MM，没算过为 null），"
            "status.closed 是该会计期间成本核算是否已结账",
            "work_hours、product_qty 由成本计算从工时日报、完工产量日报汇总生成，该期间还没计算时为空或不是最新，"
            "对照 status.calculated_through；其余表是录入或取数的结果",
            "daily_hours 按该会计期间的起止日期取（读不到 U8 系统库时按自然月，响应带 warnings）",
            "要有所读各表对应录入窗体的功能权限（人工费用、折旧费用、制造费用、工时日报、完工产量日报、在产品约当、自定义分配项目），"
            "指定的表缺权限就拒绝，没指定 tables 时跳过缺权限的表（skipped: [{table, reason}]）；数据权限：存货（按成本核算对象的存货，解析不到存货的行不过滤）",
            "只查数据库，不调用 U8 组件",
        ),
    ),
)


class CaInputsIn(CoAuth):
    fiscal_year: StrictInt | None = Field(
        None,
        ge=2000,
        le=2099,
        description="会计年度，2000 到 2099。缺省为登录日期（date）的年份；year 是账套库年度，不参与判断",
    )
    period: StrictInt | None = Field(
        None, ge=1, le=12, description="会计期间，1 到 12。缺省：会计年度是登录年度时取登录月份，否则取 12"
    )
    tables: list[CaTable] | None = Field(None, min_length=1, max_length=9, description="要读的表，不重复。缺省读全部有权限的表")
    limit: StrictInt | None = Field(None, ge=1, le=20000, description="每张表最多返回的行数，1 到 20000，缺省 5000")

    @field_validator("tables")
    @classmethod
    def _unique(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(set(value)) != len(value):
            raise ValueError("tables 不能重复")
        return value


class CaInputsOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    fiscal_year: Scalar = Field(None, description="会计年度")
    period: Scalar = Field(None, description="会计期间")
    status: dict[str, Any] | None = Field(
        None,
        description="calculated_through（最近一次成本计算到的期间 yyyy-MM，没算过为 null）、closed（该期间成本核算是否已结账）",
    )
    tables: dict[str, Any] | None = Field(
        None,
        description="表名 → { count（该期间全部行数）、rows }；rows 每行：period（yyyy-MM）或 date（yyyy-MM-dd）或 year、"
        "cost_center、cost_center_name、object_id（wip_fixed 为 base_object_id）、inv_code、inv_name、mo_code、batch、"
        "item_code、item_name、type、values（列原名 → 数值）",
    )
    skipped: list[dict[str, Any]] | None = Field(
        None, description="没指定 tables 时因没有权限而跳过的表：{ table, reason: no_permission }"
    )
    warnings: list[str] | None = Field(None, description="读不到 U8 系统库会计期间时按自然月取工时日报的提醒")
