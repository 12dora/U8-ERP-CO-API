"""写入分类：co_access 的每条写路由都登记了唯一的（type, op），op 都在封闭词表里；与桥 WriteClass.cs 的族名一致。"""

from __future__ import annotations

from decimal import Decimal

import pytest
from u8co_api.co_access import ACCESS, READ, WRITE
from u8co_api.write_class import RULES, classify, rule_ops
from u8co_api.write_policy_parse import OP_NAMES

_WRITE_ROUTES = sorted(action.removeprefix("co:") for action, level in ACCESS.items() if level == WRITE)
_READ_ROUTES = sorted(action.removeprefix("co:") for action, level in ACCESS.items() if level == READ)
_FAMILIES = {"gl", "archives", "arap", "notes", "openings", "periods", "ia", "sale_order", "dispatch", None}


def test_every_write_route_classifies() -> None:
    assert sorted(RULES) == _WRITE_ROUTES


@pytest.mark.parametrize("route", _WRITE_ROUTES)
def test_rule_ops_are_in_the_vocabulary(route: str) -> None:
    ops = rule_ops(route)
    assert ops and all(op in OP_NAMES for op in ops)
    assert RULES[route][0] in _FAMILIES


@pytest.mark.parametrize("route", _READ_ROUTES)
def test_read_routes_do_not_classify(route: str) -> None:
    assert classify("co:" + route, {"acc": "803", "type": "sale_order"}) is None


@pytest.mark.parametrize(
    ("route", "payload", "expected"),
    [
        ("vouchers/create", {"type": "sale_order"}, ("sale_order", "create")),
        ("vouchers/verify", {"type": "sale_invoice", "action": "arap_verify"}, ("sale_invoice", "verify")),
        ("vouchers/verify", {"type": "sale_order", "action": "unverify"}, ("sale_order", "unverify")),
        ("vouchers/close", {"type": "sale_order", "action": "open"}, ("sale_order", "open")),
        ("vouchers/lock", {"type": "sale_order", "action": "unlock"}, ("sale_order", "unlock")),
        ("vouchers/generate", {"type": "sale_out"}, ("sale_out", "generate")),
        ("intercompany/generate_buyer", {"type": "arrival"}, ("arrival", "generate")),
        ("workflow/approve", {"type": "qm_incoming_check"}, ("qm_incoming_check", "workflow")),
        ("sale-orders/verify", {"action": "verify"}, ("sale_order", "verify")),
        ("dispatches/verify", {"action": "unverify"}, ("dispatch", "unverify")),
        ("gl/vouchers/post", {}, ("gl", "post")),
        ("gl/vouchers/sign", {}, ("gl", "other")),
        ("archives/update", {"archive": "customer"}, ("archives", "update")),
        ("arap/writeoff/auto", {}, ("arap", "writeoff")),
        ("arap/writeoff/cancel", {}, ("arap", "other")),
        ("arap/process/voucher", {}, ("arap", "voucher")),
        ("arap/bad_debt", {"action": "occur"}, ("arap", "process")),
        ("notes/process", {}, ("notes", "process")),
        ("openings/post", {"action": "unpost"}, ("openings", "other")),
        ("openings/arap", {"action": "verify"}, ("openings", "verify")),
        ("periods/close", {"action": "reopen"}, ("periods", "open")),
        ("ia/post", {"action": "post"}, ("ia", "post")),
        ("ia/period_end", {"action": "run"}, ("ia", "close")),
        ("ia/period_end", {"action": "bogus"}, ("ia", "other")),
    ],
)
def test_type_and_op(route: str, payload: dict, expected: tuple[str, str]) -> None:
    info = classify("co:" + route, {"acc": "801", "operator": "op001", **payload})
    assert info is not None
    assert (info.type, info.op) == expected
    assert (info.acc, info.operator) == ("801", "op001")


def test_lines_count_only_for_create_update_generate() -> None:
    lines = [{"a": 1}, {"a": 2}, {"a": 3}]
    assert classify("co:vouchers/create", {"type": "sale_order", "lines": lines}).lines == 3
    assert classify("co:vouchers/generate", {"type": "sale_out", "lines": lines}).lines == 3
    assert classify("co:vouchers/delete", {"type": "sale_order", "lines": lines}).lines == 0


def test_amount_sums_body_rows_for_closebill_types() -> None:
    payload = {
        "type": "ar_receipt",
        "head": {"iExchRate": "2"},
        "lines": [{"iAmt": "100.00"}, {"iamt_f": 10}, {"cMemo": "无金额"}],
    }
    assert classify("co:vouchers/create", payload).amount == Decimal("120.00")
    assert classify("co:vouchers/create", {**payload, "type": "sale_order"}).amount is None
    assert classify("co:vouchers/create", {"type": "ap_refund", "head": {}, "lines": [{"x": 1}]}).amount is None
    assert classify("co:vouchers/delete", {**payload, "id": 1}).amount is None


def _closebill(head: dict, *lines: dict) -> object:
    return classify("co:vouchers/create", {"type": "ar_receipt", "head": head, "lines": list(lines)})


def test_amount_parses_like_the_bridge_writer() -> None:
    # 桥 ArapReq 按 NumberStyles.Float 收指数写法，这里必须同样算进金额，否则可绕过 maxAmount。
    info = _closebill({}, {"iAmt": "6e6"})
    assert (info.amount, info.amount_bad) == (Decimal(6000000), False)
    info = _closebill({"iexchrate": "7.2e0"}, {"iAmt_f": "1000000"})
    assert (info.amount, info.amount_bad) == (Decimal("7200000.0"), False)
    info = _closebill({}, {"iamt": 6e6}, {"iamt": True})
    assert (info.amount, info.amount_bad) == (Decimal("6000001.0"), False)
    info = _closebill({}, {"iamt": "  "}, {"iamt_f": None})
    assert (info.amount, info.amount_bad) == (None, False)


@pytest.mark.parametrize(
    ("head", "row"),
    [
        ({}, {"iamt": "1,000"}),
        ({}, {"iamt": "1_000"}),
        ({}, {"iamt": "x"}),
        ({}, {"iamt": "NaN"}),
        ({}, {"iamt": "５"}),
        ({}, {"iamt": [1]}),
        ({}, {"iamt": 1, "iamt_f": "abc"}),
        ({"iExchRate": "7,2"}, {"iamt_f": 1}),
        ({"iExchRate": float("inf")}, {"iamt_f": 1}),
    ],
)
def test_unparseable_amount_or_rate_is_flagged(head: dict, row: dict) -> None:
    assert _closebill(head, row).amount_bad is True


@pytest.mark.parametrize(
    ("head", "row"),
    [
        # 只差大小写的重复键：写入方可能取另一个值，从严按超过上限处理。
        ({}, {"iAmt": "", "IAMT": "9000000"}),
        ({}, {"iAmt": "1", "IAMT": "1"}),
        ({}, {"iAmt_f": "100", "IAMT_F": "9000000"}),
        ({"iExchRate": "1", "IEXCHRATE": "900"}, {"iamt_f": "100"}),
        # 超出 .NET decimal 范围（桥解析失败或累加溢出）。
        ({}, {"iamt": "1e1000000"}),
        ({}, {"iamt": "-8e28"}),
        ({"iexchrate": "7e28"}, {"iamt_f": "10"}),
    ],
)
def test_ambiguous_or_overflowing_amount_is_flagged(head: dict, row: dict) -> None:
    info = _closebill(head, row)
    assert info.amount_bad is True


def test_amount_near_decimal_max_and_sum_overflow() -> None:
    info = _closebill({}, {"iamt": "7e28"})
    assert (info.amount, info.amount_bad) == (Decimal("7e28"), False)
    info = _closebill({}, {"iamt": "7e28"}, {"iamt": "7e28"})
    assert (info.amount, info.amount_bad) == (None, True)
    # 中途溢出即整笔不认（与桥逐行累加一致），后面的负数行拉不回来。
    info = _closebill({}, {"iamt": "7e28"}, {"iamt": "7e28"}, {"iamt": "-7e28"})
    assert (info.amount, info.amount_bad) == (None, True)


def test_update_with_foreign_only_line_is_flagged() -> None:
    # 修改不收汇率，写入方按单据已存的汇率折算：只给原币的行从严按超过上限处理。
    payload = {"type": "ap_payment", "lines": [{"iAmt": "100.50"}, {"iamt_f": 10}]}
    info = classify("co:vouchers/update", payload)
    assert (info.amount, info.amount_bad) == (Decimal("110.50"), True)
    info = classify("co:vouchers/create", payload)
    assert (info.amount, info.amount_bad) == (Decimal("110.50"), False)
    info = classify("co:vouchers/update", {"type": "ap_payment", "lines": [{"iAmt": "100.50", "iAmt_f": "14"}]})
    assert (info.amount, info.amount_bad) == (Decimal("100.50"), False)
