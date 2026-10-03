"""月末结账（/v1/periods/close）：各模块的月末结账、取消结账，以及逐月结账（through）。混入 U8CoClient。

第二级写入：桥 config.json 的 enableReplicatedWrites 缺省关闭（403 feature_disabled）；打开后
只对 testAccounts 里的账套开放，其余账套桥在登录前返回 403 test_account_only。
预演用 client.dry()（桥 rollback 模式），幂等键用 client.keyed(key)。字段和取值范围与桥的约定一致；桥还会再查一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

PERIODS_ROUTE = "/v1/periods/close"
PERIOD_MODULES = ("pu", "sa", "st", "ia", "ar", "ap", "gl")
PERIOD_ACTIONS = ("close", "reopen")


@dataclass(frozen=True)
class PeriodClose:
    """一次结账请求。through=True 时 module 可以为空：从最早的年度起逐月结账到 (fiscal_year, period)。"""

    module: str
    fiscal_year: int
    period: int
    action: str = "close"
    through: bool = False


def _check(req: PeriodClose) -> None:
    if req.module not in PERIOD_MODULES and not (req.through and req.module == ""):
        raise ValueError("module 只能是 " + "、".join(PERIOD_MODULES))
    if type(req.fiscal_year) is not int or not 1000 <= req.fiscal_year <= 9999:
        raise ValueError("fiscal_year 必须是 4 位年度")
    if type(req.period) is not int or not 1 <= req.period <= 12:
        raise ValueError("period 必须是 1 到 12")
    if req.action not in PERIOD_ACTIONS:
        raise ValueError("action 只能是 close 或 reopen")
    if req.through and req.action != "close":
        raise ValueError("through 只能和 action=close 一起用")


def periods_close_fields(call: U8Call, req: PeriodClose) -> dict[str, Any]:
    # 键顺序同其他路由：公共字段、module、fiscal_year、period、action、through。password 由 call() 换成 password_enc。
    _check(req)
    fields: dict[str, Any] = {
        "acc": call.acc,
        "year": call.year,
        "operator": call.operator,
        "password": call.password,
        "date": call.date,
    }
    if req.module:
        fields["module"] = req.module
    fields.update({"fiscal_year": req.fiscal_year, "period": req.period, "action": req.action})
    if req.through:
        fields["through"] = True
    return fields


class U8CoPeriodsMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def close_period(self, call: U8Call, req: PeriodClose) -> dict[str, Any]:
        """月末结账（action=close）、取消结账（action=reopen）或逐月结账（through=True）。顺序不对、已结账等由桥返回 409。"""
        return self.call(PERIODS_ROUTE, periods_close_fields(call, req))
