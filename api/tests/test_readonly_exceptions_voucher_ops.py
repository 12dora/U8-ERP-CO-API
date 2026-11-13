"""只读例外 type=vouchers 的登记表（readonly_voucher_ops）：create / update / close / verify / generate 按 (op, voucher) 一对一放行。
销售发票：verify 只是复核（不含应收审核 arap_*），generate 只认参照发货单（含多来源 sources），不连带放行发货单那一对。
解析（缺 voucher、未知的对、键用错都让进程起不来）、allows 的判定（路径、请求体 type、action、生单来源、操作员、账套）、
走真实路由的闸门与预演。假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.test_co_dryrun import DryBridge
from tests.test_co_gate import FakeBridge, _login
from tests.test_readonly_exceptions import _ITEM, _RO, _exits, _gated, _refused
from u8co_api.readonly_exceptions import ReadOnlyException, allows, parse_exceptions
from u8co_api.readonly_voucher_ops import VOUCHER_OPS

_BASE = {**_ITEM, "type": "vouchers"}
_WHO = {"acc": "803", "operator": "svc01"}
# 登记表里每一对 (op, voucher) 与一个能放行的请求体（type 之外的部分）。
_PAIRS = (
    ("create", "sale_order", {}),
    ("update", "sale_order", {}),
    ("close", "sale_order", {"action": "close"}),
    ("verify", "sale_order", {"action": "verify"}),
    ("verify", "sale_order", {"action": "unverify"}),
    ("verify", "dispatch", {"action": "verify"}),
    ("verify", "dispatch", {"action": "unverify"}),
    ("generate", "dispatch", {}),
    ("generate", "dispatch", {"source_type": "sale_order"}),
    ("verify", "sale_invoice", {"action": "verify"}),
    ("verify", "sale_invoice", {"action": "unverify"}),
    ("generate", "sale_invoice", {}),
    ("generate", "sale_invoice", {"source_type": "dispatch"}),
    ("generate", "sale_invoice", {"sources": [{"id": 7, "lines": [{"source_line_id": 1, "quantity": 1}]}]}),
)


def _rule(op: str, voucher: str | None, **over) -> tuple:
    item = {**_BASE, "op": op, **over}
    if voucher is not None:
        item["voucher"] = voucher
    return parse_exceptions(json.dumps([item]), _RO)


def _ask(voucher: str, **extra) -> dict:
    return {**_WHO, "type": voucher, **extra}


# ---- 解析 ----


def test_registry_holds_the_round_pairs_and_the_two_fixed_ops() -> None:
    pairs = {op: (row.kinds, row.fixed) for op, row in VOUCHER_OPS.items()}
    assert pairs == {
        "head_fix": (("dispatch",), True),
        "so_contract": (("sale_order",), True),
        "create": (("sale_order",), False),
        "update": (("sale_order",), False),
        "close": (("sale_order",), False),
        "verify": (("sale_order", "dispatch", "sale_invoice"), False),
        "generate": (("dispatch", "sale_invoice"), False),
    }
    assert VOUCHER_OPS["generate"].source_of("dispatch") == "sale_order"
    assert VOUCHER_OPS["generate"].source_of("sale_invoice") == "dispatch"
    assert VOUCHER_OPS["generate"].source_of("sale_order") is None
    assert VOUCHER_OPS["verify"].source_of("sale_order") is None
    assert VOUCHER_OPS["verify"].ops == ("verify", "unverify")
    assert VOUCHER_OPS["verify"].actions == ("verify", "unverify")
    assert VOUCHER_OPS["create"].actions is None
    assert VOUCHER_OPS["close"].ops == ("close",)


@pytest.mark.parametrize(("op", "voucher"), sorted({(op, voucher) for op, voucher, _ in _PAIRS}))
def test_each_pair_parses_and_summarises(op: str, voucher: str) -> None:
    (item,) = _rule(op, voucher)
    assert (item.type, item.op, item.voucher, item.archives) == ("vouchers", op, voucher, None)
    assert item.summary() == {**_BASE, "op": op, "voucher": voucher}


@pytest.mark.parametrize("op", ("create", "update", "close", "verify", "generate"))
def test_missing_voucher_stops_the_process(op: str) -> None:
    _exits(json.dumps([{**_BASE, "op": op}]), _RO, f"voucher 在 op={op} 时必须给出")


@pytest.mark.parametrize(
    ("op", "voucher"),
    (
        ("create", "dispatch"),
        ("create", "Sale_Order"),
        ("update", "dispatch"),
        ("close", "purchase_order"),
        ("verify", "purchase_invoice"),
        ("generate", "sale_out"),
        ("create", "sale_invoice"),
        ("close", "sale_invoice"),
        ("generate", "sale_order"),
        ("create", ""),
        ("create", None),
        ("create", ["sale_order"]),
        ("head_fix", "sale_order"),
        ("so_contract", "dispatch"),
    ),
)
def test_unknown_pair_stops_the_process(op: str, voucher) -> None:
    _exits(json.dumps([{**_BASE, "op": op, "voucher": voucher}]), _RO, f"voucher 在 op={op} 时只能是")


@pytest.mark.parametrize("op", ("delete", "lock", "open", "unverify", "Create", "generate "))
def test_unregistered_voucher_op_stops_the_process(op: str) -> None:
    _exits(json.dumps([{**_BASE, "op": op, "voucher": "sale_order"}]), _RO, "type=vouchers 时只能是 head_fix、so_contract")


def test_fixed_ops_keep_their_old_shape_and_accept_the_matching_voucher() -> None:
    assert _rule("head_fix", None)[0].voucher is None
    assert _rule("head_fix", "dispatch")[0].voucher == "dispatch"
    assert _rule("so_contract", "sale_order")[0].summary()["voucher"] == "sale_order"
    assert "voucher" not in _rule("so_contract", None)[0].summary()


def test_voucher_key_is_refused_on_archives_rules() -> None:
    _exits(json.dumps([{**_ITEM, "voucher": "sale_order"}]), _RO, "voucher 只能用于 type=vouchers")


def test_archives_key_is_refused_on_the_new_voucher_ops() -> None:
    raw = json.dumps([{**_BASE, "op": "create", "voucher": "sale_order", "archives": ["customer"]}])
    _exits(raw, _RO, "archives 只能用于 type=archives")


def test_new_pairs_still_need_read_only_accounts() -> None:
    raw = json.dumps([{**_BASE, "op": "create", "voucher": "sale_order", "accounts": ["801"]}])
    _exits(raw, _RO, "不在 U8CO_READONLY_ACCOUNTS")


# ---- allows ----


@pytest.mark.parametrize(("op", "voucher", "extra"), _PAIRS)
def test_each_pair_admits_the_listed_operator_only(op: str, voucher: str, extra: dict) -> None:
    rules = _rule(op, voucher)
    action = "co:vouchers/" + op
    assert allows(rules, action, _ask(voucher, **extra)) is True
    assert allows(rules, action, _ask(voucher, **extra, operator="SVC01")) is True
    assert allows(rules, action, _ask(voucher, **extra, operator="op001")) is False
    assert allows(rules, action, _ask(voucher, **extra, acc="801")) is False
    assert allows(rules, action, _ask("purchase_order", **extra)) is False
    assert allows(rules, action, {**_WHO, **extra}) is False


@pytest.mark.parametrize(("op", "voucher", "extra"), _PAIRS)
def test_each_pair_opens_no_other_path(op: str, voucher: str, extra: dict) -> None:
    rules = _rule(op, voucher)
    payload = _ask(voucher, **extra)
    for path in (
        "create",
        "update",
        "close",
        "verify",
        "generate",
        "delete",
        "lock",
        "head_fix",
        "so_contract",
        op + "/x",
        op + "2",
    ):
        if path != op:
            assert allows(rules, "co:vouchers/" + path, payload) is False, path
    for action in ("co:sale-orders/verify", "co:dispatches/verify", "co:archives/" + op, "vouchers/" + op, ""):
        assert allows(rules, action, payload) is False, action


def test_create_for_sale_order_does_not_open_dispatch_create() -> None:
    rules = _rule("create", "sale_order")
    assert allows(rules, "co:vouchers/create", _ask("sale_order")) is True
    for other in ("dispatch", "sale_invoice", "purchase_order", "Sale_Order", " sale_order"):
        assert allows(rules, "co:vouchers/create", _ask(other)) is False, other
    assert allows(rules, "co:vouchers/create", {**_WHO, "type": None}) is False


def test_verify_opens_only_the_listed_voucher() -> None:
    so = _rule("verify", "sale_order")
    assert allows(so, "co:vouchers/verify", _ask("dispatch", action="verify")) is False
    dispatch = _rule("verify", "dispatch")
    assert allows(dispatch, "co:vouchers/verify", _ask("sale_order", action="verify")) is False


def test_verify_refuses_unknown_actions_and_close_does_not_open_open() -> None:
    verify = _rule("verify", "sale_order")
    for action in (None, "", "Verify", "close"):
        payload = _ask("sale_order") if action is None else _ask("sale_order", action=action)
        assert allows(verify, "co:vouchers/verify", payload) is False, action
    close = _rule("close", "sale_order")
    assert allows(close, "co:vouchers/close", _ask("sale_order", action="open")) is False
    assert allows(close, "co:vouchers/close", _ask("sale_order")) is False


def test_generate_needs_the_sale_order_source() -> None:
    rules = _rule("generate", "dispatch")
    assert allows(rules, "co:vouchers/generate", _ask("dispatch")) is True
    for source in ("dispatch", "sale_return", "Sale_Order", "", None, 1):
        payload = _ask("dispatch", source_type=source)
        assert allows(rules, "co:vouchers/generate", payload) is False, source
    assert allows(rules, "co:vouchers/generate", _ask("sale_out", source_type="dispatch")) is False


def test_sale_invoice_verify_is_review_only() -> None:
    # 销售发票的 arap_verify / arap_unverify（应收审核）写入分类同记 verify，登记表按 action 另外拒绝。
    rules = _rule("verify", "sale_invoice")
    for action in ("arap_verify", "arap_unverify", "close", None):
        payload = _ask("sale_invoice") if action is None else _ask("sale_invoice", action=action)
        assert allows(rules, "co:vouchers/verify", payload) is False, action
    assert allows(rules, "co:vouchers/verify", _ask("dispatch", action="verify")) is False
    assert allows(_rule("verify", "dispatch"), "co:vouchers/verify", _ask("sale_invoice", action="verify")) is False


def test_sale_invoice_generate_needs_the_dispatch_source() -> None:
    rules = _rule("generate", "sale_invoice")
    for source in ("sale_return", "sale_invoice", "sale_order", "Dispatch", "", None):
        payload = _ask("sale_invoice", source_type=source)
        assert allows(rules, "co:vouchers/generate", payload) is False, source
    assert allows(rules, "co:vouchers/generate", _ask("dispatch")) is False
    # 发货单参照订单的那一对不放行销售发票（单张、多来源都不行）。
    dispatch = _rule("generate", "dispatch")
    assert allows(dispatch, "co:vouchers/generate", _ask("sale_invoice")) is False
    many = {"sources": [{"id": 7, "lines": [{"source_line_id": 1, "quantity": 1}]}]}
    assert allows(dispatch, "co:vouchers/generate", _ask("sale_invoice", **many)) is False


def test_forged_op_does_not_widen_a_rule() -> None:
    # 直接构造的规则：未登记的 op、缺 voucher、voucher 不在该行里一律不放行。
    for op, voucher in (("delete", "sale_order"), ("create", None), ("create", "dispatch"), ("lock", "sale_order")):
        rule = ReadOnlyException(("803",), ("svc01",), "vouchers", op, None, voucher)
        assert allows((rule,), "co:vouchers/" + op, _ask(voucher or "sale_order")) is False, op
    forged = ReadOnlyException(("803",), ("svc01",), "archives", "create", ("customer",), "sale_order")
    assert allows((forged,), "co:vouchers/create", _ask("sale_order")) is False


# ---- 闸门（走真实路由） ----

_SO_CREATE = _login(operator="svc01", type="sale_order", head={"a": "b"}, lines=[{"a": 1}])


def test_create_rule_admits_sale_order_create_and_its_dry_run() -> None:
    fake = DryBridge()
    client = _gated(fake, read_only_exceptions=_rule("create", "sale_order"))
    ok = client.post("/v1/co/vouchers/create", json=_SO_CREATE)
    assert ok.status_code == 200, ok.text
    dry = client.post("/v1/co/vouchers/create", json={**_SO_CREATE, "dry_run": True})
    assert dry.status_code == 200, dry.text
    assert [call[0] for call in fake.calls] == ["/v1/vouchers/create", "/v1/vouchers/create"]


def test_create_rule_refuses_other_types_routes_and_operators() -> None:
    fake = DryBridge()
    client = _gated(fake, read_only_exceptions=_rule("create", "sale_order"))
    _refused(client.post("/v1/co/vouchers/create", json={**_SO_CREATE, "operator": "op001"}))
    # 发货单新增在请求模型里另有必填字段（无来源单据校验），HTTP 层用采购订单代表「其它单据类型」；dispatch 见 allows 用例。
    other = {**_SO_CREATE, "type": "purchase_order"}
    _refused(client.post("/v1/co/vouchers/create", json=other))
    _refused(client.post("/v1/co/vouchers/create", json={**other, "dry_run": True}))
    _refused(client.post("/v1/co/vouchers/delete", json=_login(operator="svc01", type="sale_order", id=1)))
    lock = _login(operator="svc01", type="sale_order", id=1, action="lock")
    _refused(client.post("/v1/co/vouchers/lock", json=lock))
    verify = _login(operator="svc01", type="sale_order", id=1, action="verify")
    _refused(client.post("/v1/co/vouchers/verify", json=verify))
    _refused(client.post("/v1/co/sale-orders/verify", json=_login(operator="svc01", id=1, action="verify")))
    _refused(client.post("/v1/co/dispatches/verify", json=_login(operator="svc01", id=1, action="verify")))
    assert fake.calls == []


def test_verify_rule_admits_verify_and_unverify_of_the_listed_voucher() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_rule("verify", "sale_order"))
    for action in ("verify", "unverify"):
        body = _login(operator="svc01", type="sale_order", id=1, action=action)
        ok = client.post("/v1/co/vouchers/verify", json=body)
        assert ok.status_code == 200, ok.text
    dispatch = _login(operator="svc01", type="dispatch", id=1, action="verify")
    _refused(client.post("/v1/co/vouchers/verify", json=dispatch))
    assert len(fake.calls) == 2


def test_close_and_update_rules_admit_their_own_route_only() -> None:
    fake = FakeBridge()
    rules = _rule("close", "sale_order") + _rule("update", "sale_order")
    client = _gated(fake, read_only_exceptions=rules)
    close = _login(operator="svc01", type="sale_order", id=1, action="close")
    assert client.post("/v1/co/vouchers/close", json=close).status_code == 200
    _refused(client.post("/v1/co/vouchers/close", json={**close, "action": "open"}))
    update = _login(operator="svc01", type="sale_order", id=1, head={"cMemo": "a"})
    assert client.post("/v1/co/vouchers/update", json=update).status_code == 200
    _refused(client.post("/v1/co/vouchers/create", json=_SO_CREATE))
    assert len(fake.calls) == 2


def test_idempotent_replay_follows_the_same_rule() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_rule("create", "sale_order"))
    other = {**_SO_CREATE, "type": "purchase_order"}
    _refused(client.post("/v1/co/vouchers/create", json=other, headers={"Idempotency-Key": "replay-ro-other"}))
    assert fake.calls == []


_GEN = _login(operator="svc01", type="dispatch", id=1, lines=[{"source_line_id": 1, "quantity": 1}])


def test_generate_rule_over_http_tells_omitted_from_null_source() -> None:
    # 省略 source_type 即缺省来源 sale_order，放行；显式 null 在请求模型里 400，不会被 exclude_none 抹成「省略」
    # 而穿过只读例外（与桥一致：桥对出现的 null 不放行、生单校验 400）。
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_rule("generate", "dispatch"))
    ok = client.post("/v1/co/vouchers/generate", json=_GEN)
    assert ok.status_code == 200, ok.text
    assert client.post("/v1/co/vouchers/generate", json={**_GEN, "source_type": "sale_order"}).status_code == 200
    denied = client.post("/v1/co/vouchers/generate", json={**_GEN, "source_type": None})
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert denied.json()["error"]["field"] == "source_type"
    dry = client.post("/v1/co/vouchers/generate", json={**_GEN, "source_type": None, "dry_run": True})
    assert dry.status_code == 400, dry.text
    assert len(fake.calls) == 2
    assert "source_type" not in fake.calls[0][1]


_INV = _login(operator="svc01", type="sale_invoice", id=1, lines=[{"source_line_id": 1, "quantity": 1, "cbdefine1": "1.2"}])
_INV_MANY = _login(
    operator="svc01",
    type="sale_invoice",
    sources=[
        {"id": 1, "lines": [{"source_line_id": 11, "quantity": 2}]},
        {"id": 2, "lines": [{"source_line_id": 21, "quantity": 1, "cbdefine1": "1.05"}]},
    ],
)


def test_sale_invoice_rules_over_http() -> None:
    fake = DryBridge()
    rules = _rule("generate", "sale_invoice") + _rule("verify", "sale_invoice")
    client = _gated(fake, read_only_exceptions=rules)
    for body in (_INV, {**_INV, "source_type": "dispatch"}, _INV_MANY, {**_INV_MANY, "dry_run": True}):
        ok = client.post("/v1/co/vouchers/generate", json=body)
        assert ok.status_code == 200, ok.text
    for action in ("verify", "unverify"):
        body = _login(operator="svc01", type="sale_invoice", id=1, action=action)
        assert client.post("/v1/co/vouchers/verify", json=body).status_code == 200
    _refused(client.post("/v1/co/vouchers/verify", json=_login(operator="svc01", type="sale_invoice", id=1, action="arap_verify")))
    red = {**_INV, "source_type": "sale_return", "lines": [{"source_line_id": 1, "quantity": 1}]}
    _refused(client.post("/v1/co/vouchers/generate", json=red))
    _refused(client.post("/v1/co/vouchers/generate", json=_GEN))
    _refused(client.post("/v1/co/vouchers/delete", json=_login(operator="svc01", type="sale_invoice", id=1)))
    assert len(fake.calls) == 6
    assert fake.calls[2][1]["sources"][1]["lines"][0]["cbdefine1"] == "1.05"
    assert "id" not in fake.calls[2][1] and "lines" not in fake.calls[2][1]


def test_null_source_is_refused_on_writable_accounts_too() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_accounts=(), read_only_exceptions=())
    denied = client.post("/v1/co/vouchers/generate", json={**_GEN, "source_type": None})
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


# ---- 操作员比较：只忽略 ASCII 大小写（与桥 AsciiIgnoreCase 一致） ----


def test_ascii_fold_is_the_only_operator_rule() -> None:
    from u8co_api.readonly_exceptions import ascii_fold

    assert ascii_fold("SVC01") == "svc01"
    assert ascii_fold("opß") != ascii_fold("opss")
    assert ascii_fold("opß") != ascii_fold("OPSS")
    assert ascii_fold("opK") != ascii_fold("opk")  # Kelvin 符号不是 K
    assert ascii_fold("opÉ") != ascii_fold("opé")


def test_sharp_s_operator_matches_only_itself() -> None:
    rules = _rule("create", "sale_order", operators=["opß"])
    assert allows(rules, "co:vouchers/create", _ask("sale_order", operator="opß")) is True
    for other in ("opss", "OPSS", "opSS", "opẞ"):
        assert allows(rules, "co:vouchers/create", _ask("sale_order", operator=other)) is False, other


def test_operator_duplicates_use_the_same_rule() -> None:
    (item,) = _rule("create", "sale_order", operators=["opß", "opss"])
    assert item.operators == ("opß", "opss")
    _exits(json.dumps([{**_BASE, "op": "create", "voucher": "sale_order", "operators": ["opA", "opa"]}]), _RO, "重复")


def test_sharp_s_over_http_is_refused_for_ss() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_rule("create", "sale_order", operators=["opß"]))
    _refused(client.post("/v1/co/vouchers/create", json={**_SO_CREATE, "operator": "opss"}))
    assert fake.calls == []
