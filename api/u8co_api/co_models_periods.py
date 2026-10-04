"""月末结账 /v1/co/periods/close 的请求和响应。字段名与桥一致，响应放行桥多给的字段。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

from u8co_api.co_doctext import TEST_ONLY_GATE
from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

PERIODS_CLOSE_SUMMARY = "月末结账"
PERIODS_CLOSE_HELP = (
    "U8 各模块的月末结账或取消结账（U8「月末结账」）。\n\n"
    "**用法**\n"
    "- reopen 只能取消最后一个已结账的期间\n"
    "- through 时全部在一个事务里，按 U8 顺序：采购、销售 → 库存 → 存货核算 → 应收、应付 → 总账\n"
    "- 库存结账照 U8 写月末结存快照（ST_MonthAccount 等五张表），取消结账删除该月快照\n"
    "- 库存结账的响应和预演 detail 带 stock_rows\n"
    "- 存货核算有数据的月份照 U8 结账（取消结账），先用 ia/post、ia/period_end 记账并做期末处理\n"
    "- dry_run：事务里执行后回滚（rollback 模式），返回 DryRunOut，会结账的期间在 detail.periods\n\n"
    "**限制**\n" + TEST_ONLY_GATE + "\n"
    "**权限**\n\n"
    "| 模块 | 功能权限 |\n"
    "| --- | --- |\n"
    "| 采购 | PU0207 |\n"
    "| 销售 | SA020901 |\n"
    "| 库存 | ST0304 |\n"
    "| 存货核算 | IA2007 |\n"
    "| 应收 | AR0509 |\n"
    "| 应付 | AP0509 |\n"
    "| 总账 | 结账 GL1512，反结账 GL1520 |\n\n"
    "- through 要所涉每个模块的结账权限\n\n"
    "**错误**\n"
    "- 409 state_mismatch，常见原因：\n"
    "  - 上一期间还没结账、该期间已结账、同期前置模块还没结账\n"
    "  - 有未审核 / 未复核的单据、有未记账的凭证\n"
    "  - 请先做期间损益结转\n"
    "  - 存货核算该月未做期末处理或有未记账的单据\n"
    "  - 库存或存货核算的选项接口暂不支持\n"
    "  - 采购未做期初记账\n"
    "  - 总账结账前固定资产 / 薪资 / 成本还没结账（桥不结这几个模块）"
)


class PeriodsCloseIn(CoAuth):
    module: Literal["pu", "sa", "st", "ia", "ar", "ap", "gl"] | None = Field(
        None, description="模块：pu 采购、sa 销售、st 库存、ia 存货核算、ar 应收、ap 应付、gl 总账。through 为 true 时可省略"
    )
    fiscal_year: StrictInt = Field(..., ge=1000, le=9999, description="会计年度，4 位")
    period: StrictInt = Field(..., ge=1, le=12, description="期间（月份），1 到 12")
    action: Literal["close", "reopen"] = Field(..., description="close 结账，reopen 取消结账")
    through: StrictBool | None = Field(
        None, description="true：各已启用模块从最早的年度起逐月结账到 fiscal_year 年 period 期（只和 close 一起用）"
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _shape(self) -> PeriodsCloseIn:
        if self.through and self.action != "close":
            raise ValueError("through 只能和 action=close 一起用")
        if not self.through and self.module is None:
            raise ValueError("缺少 module（只有 through 为 true 时可以省略）")
        return self

    def audit_ref(self) -> str:
        return "through" if self.through else str(self.module)


class PeriodsCloseOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    module: Scalar = Field(None, description="模块（through 时没有）")
    action: Scalar = Field(None, description="close 或 reopen")
    fiscal_year: Scalar = Field(None, description="会计年度（through 时没有）")
    period: Scalar = Field(None, description="期间（through 时没有）")
    closed: bool | None = Field(None, description="操作后的结账状态：结账后为 true，取消结账后为 false（through 时没有）")
    through: list[dict[str, Any]] | None = Field(
        None, description="through 时结了账的期间，按执行顺序：[{module, fiscal_year, period}]"
    )
    count: int | None = Field(None, description="through 时结了账的期间数，没有需要结账的期间时为 0")
    warnings: list[str] | None = Field(
        None, description="提示，以提示码开头（目前只有 ia_opening_not_posted：存货核算未做期初记账仍结了账）。没有则省略"
    )
    ia_counts: dict[str, Any] | None = Field(
        None, description="存货核算有数据的月份：月末结账（取消结账）脚本的诊断计数，名称 → 行数。through 时在各项里"
    )
