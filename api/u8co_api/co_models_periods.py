"""月末结账 /v1/co/periods/close 的请求和响应。字段名与桥一致，响应放行桥多给的字段。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictBool, StrictInt, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

PERIODS_CLOSE_SUMMARY = "月末结账"
PERIODS_CLOSE_HELP = (
    "月末结账或取消结账（U8 各模块的「月末结账」）。默认关闭（桥未开 enableReplicatedWrites 时 403 feature_disabled），打开后只对 CO 桥配置为测试账套（testAccounts）的账套开放，"
    "其他账套 403 test_account_only，不登录 U8。module 为 pu、sa、st、ia、ar、ap、gl，fiscal_year 是会计年度，"
    "period 是 1 到 12；action 为 close（结账）或 reopen（取消结账，只能取消最后一个已结账的期间）。"
    "through 为 true 时（只和 close 一起用，module 可省略）按 U8 的顺序（采购、销售 → 库存 → 存货核算 → 应收、应付 → 总账）"
    "把各已启用模块从最早的年度起到 fiscal_year 年 period 期为止的未结账期间逐月结账，全部在一个事务里。"
    "功能权限：采购 PU0207、销售 SA020901、库存 ST0304、存货核算 IA2007、应收 AR0509、应付 AP0509、总账结账 GL1512、"
    "总账反结账 GL1520；through 要所涉每个模块的结账权限。"
    "409 state_mismatch：上一期间还没结账、该期间已结账、同期前置模块还没结账、有未审核 / 未复核的单据、"
    "有未记账的凭证、请先做期间损益结转、存货核算该月未做期末处理或有未记账的单据、库存或存货核算的选项接口暂不支持、"
    "采购未做期初记账、总账结账前固定资产 / 薪资 / 成本还没结账（桥不结这几个模块）等。"
    "库存结账照 U8 写月末结存快照（ST_MonthAccount 等五张表），取消结账删除该月快照；响应和预演 detail 带 stock_rows。"
    "存货核算有数据的月份照 U8 执行月末结账（取消结账），先用 ia/post、ia/period_end 记账并做期末处理；响应带 ia_counts。"
    "dry_run 为 true 时在事务里执行后回滚（rollback 模式），返回 DryRunOut，会结账的期间在 detail.periods。"
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
