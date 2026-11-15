"""应收坏账处理（/v1/arap/bad_debt，第二级写入，默认关闭，打开后仅测试账套）：坏账发生 occur（9G）、坏账收回 recover（9H）、
计提坏账准备 provision（9F）。混入 U8CoClient。

处理日期就是登录日期 call.date。每次返回处理号 cancel_no（HZAR…），取消用 arap_process_cancel(call, "AR", cancel_no)，
制单用 arap_process_voucher（ProcVoucher("AR", [cancel_no, …])，一次只能是同一种坏账处理）。预演用 client.dry()。
这里只查请求的形状（动作、字段、条数）；单据余额、坏账准备参数、期间等由桥再查一遍。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_arap_proc import _auth, _lines, _put, _text

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

ARAP_BAD_DEBT_ROUTE = "/v1/arap/bad_debt"
BAD_DEBT_ACTIONS = ("occur", "recover", "provision")
# 各动作收的可选、必填字段（同桥；登录字段之外）。
_ALLOWED = {
    "occur": ("customer", "currency", "lines", "digest", "dept", "person"),
    "recover": ("customer", "currency", "receipt", "amount", "digest"),
    "provision": (),
}
_REQUIRED = {"occur": ("customer", "lines"), "recover": ("customer", "receipt", "amount"), "provision": ()}
_NAMES = ("customer", "currency", "lines", "receipt", "amount", "digest", "dept", "person")


@dataclass(frozen=True)
class BadDebt:
    """坏账处理。occur：customer 与 lines（1 到 50 项 {type, id, line_id?, amount}，type 为 26 / 27 / 28 / 29 或 R0 到 R9），
    可带 currency、digest、dept、person。recover：customer、receipt（未审核的收款单号）与 amount（等于收款单全部余额），
    可带 currency、digest。provision：不带业务字段。"""

    action: str
    customer: str | None = None
    lines: Any = None
    receipt: str | None = None
    amount: float | None = None
    currency: str | None = None
    digest: str | None = None
    dept: str | None = None
    person: str | None = None


def bad_debt_fields(call: U8Call, ask: BadDebt) -> dict[str, Any]:
    # 键顺序：公共字段、action、customer、currency、lines、receipt、amount、digest、dept、person。值为 None 的不发。
    if ask.action not in BAD_DEBT_ACTIONS:
        raise ValueError("action 只能是 occur、recover 或 provision")
    fields = _auth(call)
    fields["action"] = ask.action
    allowed, required = _ALLOWED[ask.action], _REQUIRED[ask.action]
    for name in _NAMES:
        value = getattr(ask, name)
        if value is None:
            if name in required:
                raise ValueError(f"action 为 {ask.action} 时必须给 {name}")
            continue
        if name not in allowed:
            raise ValueError(f"action 为 {ask.action} 时不能带 {name}")
        if name == "lines":
            fields["lines"] = _lines(value, "lines")
        elif name == "amount":
            if type(value) not in (int, float) or value <= 0:
                raise ValueError("amount 必须是大于 0 的数（原币）")
            fields["amount"] = value
        else:
            _put(fields, name, _text(value, name))
    return fields


class U8CoArapBadMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def arap_bad_debt(self, call: U8Call, ask: BadDebt) -> dict[str, Any]:
        """坏账发生 / 收回 / 计提，返回 cancel_no、style、amount、remain_before、remain_after（坏账准备余额）；计提另带
        base、rate、target。默认关闭（403 feature_disabled），打开后仅测试账套（403 test_account_only）；未设置坏账准备参数、余额不足、本次计提为 0 等由桥返回 409。"""
        return self.call(ARAP_BAD_DEBT_ROUTE, bad_debt_fields(call, ask))
