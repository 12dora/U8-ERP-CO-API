"""存货核算（/v1/ia/post、/v1/ia/period_end）：正常单据记账、恢复记账、期末处理、取消期末处理。混入 U8CoClient。

第二级写入：桥 config.json 的 enableReplicatedWrites 缺省关闭（403 feature_disabled）；打开后
只对 testAccounts 里的账套开放，其余账套桥在登录前返回 403 test_account_only。
预演用 client.dry()（桥 rollback 模式），幂等键用 client.keyed(key)。字段和取值范围与桥的约定一致；桥还会再查一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

IA_POST_ROUTE = "/v1/ia/post"
IA_PERIOD_END_ROUTE = "/v1/ia/period_end"
IA_POST_ACTIONS = ("post", "unpost")
IA_PERIOD_END_ACTIONS = ("run", "cancel")
IA_UNCOSTED = ("refuse", "skip")
# 长时操作：存货核算记账、期末处理、存货核算期初记账（openings/post module=ia），以及经过存货核算的月末结账（module=ia 或 through）。生产规模的月份要几分钟，
# 桥对它们等 iaCommandSeconds（缺省 900）+ 60 秒；客户端按 long_timeout 读（U8CoClient 缺省 1000 秒）。
LONG_ROUTES = frozenset({IA_POST_ROUTE, IA_PERIOD_END_ROUTE})
_PERIODS_CLOSE = "/v1/periods/close"
_OPENINGS_POST = "/v1/openings/post"


def is_long_call(route: str, fields: dict[str, Any]) -> bool:
    if route in LONG_ROUTES:
        return True
    if route == _OPENINGS_POST:
        return fields.get("module") == "ia"
    return route == _PERIODS_CLOSE and (fields.get("module") == "ia" or fields.get("through") is True)


@dataclass(frozen=True)
class IaMonth:
    """一个存货核算月份上的操作。on_uncosted 只用于记账（post）：空串表示不传，桥按 refuse 处理。"""

    fiscal_year: int
    period: int
    action: str
    on_uncosted: str = ""


def _check_month(req: IaMonth) -> None:
    if type(req.fiscal_year) is not int or not 1000 <= req.fiscal_year <= 9999:
        raise ValueError("fiscal_year 必须是 4 位年度")
    if type(req.period) is not int or not 1 <= req.period <= 12:
        raise ValueError("period 必须是 1 到 12")


def _check_post(req: IaMonth) -> None:
    _check_month(req)
    if req.action not in IA_POST_ACTIONS:
        raise ValueError("action 只能是 post 或 unpost")
    if req.on_uncosted and req.on_uncosted not in IA_UNCOSTED:
        raise ValueError("on_uncosted 只能是 refuse 或 skip")
    if req.on_uncosted and req.action != "post":
        raise ValueError("on_uncosted 只能和 action=post 一起用")


def _check_period_end(req: IaMonth) -> None:
    _check_month(req)
    if req.action not in IA_PERIOD_END_ACTIONS:
        raise ValueError("action 只能是 run 或 cancel")
    if req.on_uncosted:
        raise ValueError("期末处理不收 on_uncosted")


def _fields(call: U8Call, req: IaMonth) -> dict[str, Any]:
    # 键顺序同其他路由：公共字段、fiscal_year、period、action、on_uncosted。password 由 call() 换成 password_enc。
    fields: dict[str, Any] = {
        "acc": call.acc,
        "year": call.year,
        "operator": call.operator,
        "password": call.password,
        "date": call.date,
        "fiscal_year": req.fiscal_year,
        "period": req.period,
        "action": req.action,
    }
    if req.on_uncosted:
        fields["on_uncosted"] = req.on_uncosted
    return fields


def ia_post_fields(call: U8Call, req: IaMonth) -> dict[str, Any]:
    _check_post(req)
    return _fields(call, req)


def ia_period_end_fields(call: U8Call, req: IaMonth) -> dict[str, Any]:
    _check_period_end(req)
    return _fields(call, req)


class U8CoIaMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def ia_post(self, call: U8Call, req: IaMonth) -> dict[str, Any]:
        """正常单据记账（action=post）或恢复记账（action=unpost）。已结账、有 U8 无法确定成本的存货等由桥返回 409。"""
        return self.call(IA_POST_ROUTE, ia_post_fields(call, req))

    def ia_period_end(self, call: U8Call, req: IaMonth) -> dict[str, Any]:
        """期末处理（action=run）或取消期末处理（action=cancel）。已结账、该月没有记账数据等由桥返回 409。"""
        return self.call(IA_PERIOD_END_ROUTE, ia_period_end_fields(call, req))
