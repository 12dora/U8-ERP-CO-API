"""基础用例：健康检查、签名、名单外账套、登录、销售订单和发货单审核弃审。"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec, Step, call, read_step


def _pair(route: str, read_name: str, first: str, second: str) -> tuple[Step, ...]:
    return (
        call(route, (("id", "$id"), ("action", first))),
        read_step(read_name, "verified" if first == "verify" else "cleared"),
        call(route, (("id", "$id"), ("action", second))),
        read_step(read_name, "verified" if second == "verify" else "cleared"),
    )


def _sale() -> CaseSpec:
    steps = _pair("/v1/sale-orders/verify", "sale", "verify", "unverify")
    return CaseSpec("sale-verify", "销售订单审核与弃审", ("sale_id",), steps, True)


def _dispatch(bag: dict) -> CaseSpec:
    mode = bag.get("dispatch_mode") or "verify-first"
    first, second = ("unverify", "verify") if mode == "unverify-first" else ("verify", "unverify")
    steps = _pair("/v1/dispatches/verify", "dispatch", first, second)
    return CaseSpec("dispatch-verify", "发货单审核与弃审", ("dispatch_id",), steps, True)


def _negatives() -> list[CaseSpec]:
    missing = call(
        "/v1/sale-orders/verify",
        (("id", "2147483000"), ("action", "verify")),
        (),
        404,
        "not_found",
    )
    bad = call(
        "/v1/sale-orders/verify",
        (("id", "1"), ("action", "approve")),
        (),
        400,
        "bad_request",
    )
    again = call(
        "/v1/sale-orders/verify",
        (("id", "$id"), ("action", "unverify")),
        (),
        409,
        "state_mismatch",
    )
    down = (
        call("/v1/dispatches/verify", (("id", "$id"), ("action", "unverify")), (), 409, "u8_rejected"),
        read_step("dispatch", "verified_any"),
    )
    return [
        CaseSpec("missing-404", "不存在的单据", (), (missing,), True),
        CaseSpec("bad-action", "非法动作", (), (bad,), True),
        CaseSpec("repeat-unverify", "重复弃审", ("sale_id",), (again,), True),
        CaseSpec("downstream-unverify", "有下游的发货单弃审被拒绝", ("dispatch_downstream",), down, True),
    ]


def smoke_cases(bag: dict) -> list[CaseSpec]:
    head = [
        CaseSpec("health", "健康检查", (), (Step("health"),), False),
        CaseSpec("bad-signature", "错误签名", (), (Step("badsig"),), False),
        CaseSpec("account-foreign", "名单外账套", (), (Step("accforeign"), Step("audit")), False),
        CaseSpec("login", "登录", (), (Step("login"),), False),
        _sale(),
        _dispatch(bag),
    ]
    return head + _negatives()
