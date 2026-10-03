"""存货核算 /v1/co/ia/post、/v1/co/ia/period_end 的请求和响应。字段名与桥一致，响应放行桥多给的字段。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

_GATE = (
    "默认关闭（桥未开 enableReplicatedWrites 时 403 feature_disabled），打开后只对 CO 桥配置为测试账套（testAccounts）的账套开放，其他账套 403 test_account_only，不登录 U8。"
    "执行前查四项：按仓库核算、每个仓库全月平均法、暂估单到回冲、销售成本按发出商品或销售出库单，"
    "不符 409「接口暂不支持」；标准成本、委外等未实测，也不检查。脚本超过 iaCommandSeconds 时 503 ia_timeout（已回滚）。"
    "fiscal_year 是会计年度，period 是 1 到 12。"
)
_DRY = "dry_run 为 true 时在事务里执行后回滚（rollback 模式），返回 DryRunOut，脚本的诊断计数在 detail.counts。"

IA_POST_SUMMARY = "存货核算记账"
IA_POST_HELP = (
    "存货核算的正常单据记账或恢复记账（U8 存货核算「正常单据记账」「恢复记账」）。" + _GATE
    + "功能权限：正常单据记账 IA2004、恢复记账 IA2005。"
    "action 为 post（把该月已审核、未记账的出入库单据记入存货明细账；销售成本按发出商品时连同已复核的销售发票）"
    "或 unpost（恢复该月的记账）。on_uncosted 只和 post 一起用：refuse（缺省）时有 U8 无法确定成本的存货"
    "（U8 界面会要求手工输入单价）就整笔拒绝 409，error.detail 带 uncosted、uncosted_total；skip 时这些存货不记账（同 U8 界面取消勾选），其余照常记账。"
    "409 state_mismatch：存货核算未启用、该月已结账、恢复记账前已做期末处理、发出商品的销售发票不在本次恢复范围、"
    "直接供应的材料出库接口暂不支持（事务回滚）、有 U8 无法确定成本的存货等；U8 存储过程自己的拒绝 409 u8_rejected「U8 拒绝：…」。"
    "响应带 counts（脚本的诊断计数，如 area 本次记账行数、restore_rows 本次恢复行数）；没有要处理的单据时为 0，带 message。" + _DRY
)
IA_PERIOD_END_SUMMARY = "存货核算期末处理"
IA_PERIOD_END_HELP = (
    "存货核算的期末处理或取消期末处理（U8 存货核算「期末处理」「取消期末处理」）。" + _GATE
    + "功能权限：期末处理、取消期末处理 IA2006。"
    "action 为 run（按全月平均计算该月出库成本、写出库调整，汇总表标记为已期末处理）或 cancel（取消期末处理）。"
    "409 state_mismatch：该月已结账、该月还没有记账数据（先 ia/post）等；U8 存储过程自己的拒绝 409 u8_rejected。"
    "存货核算月末结账用 periods/close（module=ia）。响应带 counts。" + _DRY
)


class _IaMonth(CoAuth):
    fiscal_year: StrictInt = Field(..., ge=1000, le=9999, description="会计年度，4 位")
    period: StrictInt = Field(..., ge=1, le=12, description="期间（月份），1 到 12")
    dry_run: DryRunFlag = False

    def audit_ref(self) -> str:
        return f"{self.fiscal_year}-{self.period}"


class IaPostIn(_IaMonth):
    action: Literal["post", "unpost"] = Field(..., description="post 正常单据记账，unpost 恢复记账")
    on_uncosted: Literal["refuse", "skip"] | None = Field(
        None,
        description="只和 post 一起用。refuse（缺省）：有 U8 无法确定成本的存货就整笔拒绝 409；skip：这些存货不记账，其余照常记账",
    )

    @model_validator(mode="after")
    def _shape(self) -> IaPostIn:
        if self.on_uncosted is not None and self.action != "post":
            raise ValueError("on_uncosted 只能和 action=post 一起用")
        return self


class IaPeriodEndIn(_IaMonth):
    action: Literal["run", "cancel"] = Field(..., description="run 期末处理，cancel 取消期末处理")


class IaOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    action: Scalar = Field(None, description="post、unpost、run 或 cancel")
    fiscal_year: Scalar = Field(None, description="会计年度")
    period: Scalar = Field(None, description="期间")
    counts: dict[str, Any] | None = Field(
        None, description="诊断计数：名称 → 行数（如 area 本次记账行数、restore_rows 本次恢复行数、subsidiary_month 该月明细账行数）"
    )
    message: str | None = Field(None, description="中文说明，例如没有需要记账（恢复）的单据")
