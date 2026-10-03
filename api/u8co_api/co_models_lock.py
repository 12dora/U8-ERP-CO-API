"""销售订单锁定与解锁（vouchers/lock）。校验规则与桥的 RequestsLock / VoucherLock 一致；采购订单暂不支持。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag

_OUT = ConfigDict(extra="ignore")
_ID_MAX = 2147483647

# 采购订单锁定暂不支持：U8 采购组件 DoLock 实测一律拒绝（「本张已经被修改,不能锁定.」），在模型上 400。
LockType = Literal["sale_order"]

LOCK_SUMMARY = "锁定或解锁单据"
LOCK_HELP = (
    "只接受销售订单（id 是 SO_SOMain.ID），整单锁定，不按行。采购订单锁定暂不支持（U8 采购组件 DoLock 实测一律拒绝），"
    "type=purchase_order 返回 400；U8 客户端锁定的采购订单仍按下面的规则挡住别的操作员。"
    "action 为 lock 或 unlock。"
    "锁定人写在表头 cLocker（操作员姓名），即读取表头的 clocker、列表的 locker。"
    "已审核的销售订单不能锁定（409 state_mismatch）；别人已锁定再锁定、未锁定去解锁 409 state_mismatch；"
    "本人已锁定再锁定返回 200 且 already 为 true（锁定 504 后可以直接重试）；"
    "只有锁定人本人能解锁，别人解锁 409 state_mismatch「单据由 X 锁定」。"
    "单据被别的操作员锁定时，修改、删除、审核、弃审都返回 409 state_mismatch「单据已被 X 锁定」，锁定人本人不受影响。"
    "U8 拒绝时 409 u8_rejected，带回 U8 原文。已提交但回读锁定状态失败或不符时 504 outcome_unknown，先用 load 核对。"
    "要有 U8 的锁定 / 解锁按钮权限（销售订单 SA03010108 / SA03010109），否则 403。"
)


class CoLockIn(CoAuth):
    type: LockType = Field(..., description="可锁定的单据类型：销售订单（采购订单暂不支持）")
    id: int = Field(..., gt=0, le=_ID_MAX, description="单据主键，1 到 2147483647")
    action: Literal["lock", "unlock"] = Field(..., description="lock 锁定，unlock 解锁")
    dry_run: DryRunFlag = False


class CoLockOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已锁定或解锁")
    type: str = Field(description="单据类型")
    id: int = Field(description="单据主键")
    action: Literal["lock", "unlock"] = Field(description="lock 锁定，unlock 解锁")
    locked: bool = Field(description="回读的表头锁定人是否非空")
    locker: str = Field(description="锁定人（操作员姓名）。解锁后为空")
    already: bool | None = Field(None, description="本人此前已锁定、这次没有再调用 U8 时为 true。其它情况省略")
