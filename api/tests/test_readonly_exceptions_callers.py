"""只读例外的 callers 行：按调用系统（API 信任项 name）放行，代替 operators。

callers 只能用于 type=vouchers 的 create / update / close / verify / generate；operators 与 callers 恰好写一个。
命中时任何操作员都放行（仍受信任项 operators.allow / deny 约束），路由、单据类型、来源规则与 operators 行相同；
信任项 name 取自已验签令牌，不取自请求体。审计 read_only_match 记 operator 或 caller。假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line, base_claims, entry
from tests.test_co_dryrun import DryBridge
from tests.test_co_gate import FakeBridge, _login
from tests.test_readonly_exceptions import _ITEM, _RO, _exits, _gated, _refused
from u8co_api.auth import Caller
from u8co_api.co_idem import TRUST_FIELD, caller_tag, caller_trust, idempotency_fields
from u8co_api.readonly_exceptions import CALLER_OPS, ReadOnlyException, allows, match_of, parse_exceptions
from u8co_api.trust import OperatorRule

_CRM = "crm"


class _Headers:
    def __init__(self, values: dict[str, list[str]]) -> None:
        self._values = values

    def getlist(self, name: str) -> list[str]:
        return self._values.get(name, [])


_BASE = {"accounts": ["803"], "type": "vouchers"}
_WHO = {"acc": "803", "operator": "op001"}


def _rule(op: str, voucher: str | None, **over) -> tuple:
    item = {**_BASE, "callers": [_CRM], "op": op, **over}
    if voucher is not None:
        item["voucher"] = voucher
    return parse_exceptions(json.dumps([item]), _RO)


def _ask(voucher: str, **extra) -> dict:
    return {**_WHO, "type": voucher, **extra}


# ---- 解析 ----


def test_caller_ops_are_the_non_fixed_registry_rows() -> None:
    assert CALLER_OPS == {"create", "update", "close", "verify", "generate"}


def test_caller_row_parses_and_summarises() -> None:
    (item,) = _rule("create", "sale_order")
    assert (item.callers, item.operators, item.op, item.voucher) == (("crm",), (), "create", "sale_order")
    assert item.summary() == {**_BASE, "callers": ["crm"], "op": "create", "voucher": "sale_order"}
    assert "operators" not in item.summary()


def test_both_keys_stop_the_process() -> None:
    raw = json.dumps([{**_BASE, "op": "create", "voucher": "sale_order", "operators": ["op001"], "callers": [_CRM]}])
    _exits(raw, _RO, "恰好写 operators 或 callers 之一")


def test_neither_key_stops_the_process() -> None:
    _exits(json.dumps([{**_BASE, "op": "create", "voucher": "sale_order"}]), _RO, "恰好写 operators 或 callers 之一")


@pytest.mark.parametrize("value", ([], "crm", None, [""], ["crm", "crm"], [1]))
def test_empty_or_malformed_callers_stop_the_process(value) -> None:
    raw = json.dumps([{**_BASE, "op": "create", "voucher": "sale_order", "callers": value}])
    _exits(raw, _RO, "callers")


@pytest.mark.parametrize("name", ("sha256", "1crm", "crm:x", "crm x", "c" * 64, "客户系统"))
def test_callers_must_be_trust_names(name: str) -> None:
    raw = json.dumps([{**_BASE, "op": "create", "voucher": "sale_order", "callers": [name]}])
    _exits(raw, _RO, "信任项的 name")


@pytest.mark.parametrize(
    "item",
    (
        {**_ITEM, "callers": [_CRM]},
        {**_ITEM, "op": "create", "archives": ["customer"], "callers": [_CRM]},
        {**_BASE, "op": "head_fix", "callers": [_CRM]},
        {**_BASE, "op": "so_contract", "callers": [_CRM]},
    ),
)
def test_callers_are_refused_outside_the_voucher_rows(item: dict) -> None:
    item = {key: value for key, value in item.items() if key != "operators"}
    _exits(json.dumps([item]), _RO, "callers 只能用于 type=vouchers")


def test_caller_rows_still_need_read_only_accounts_and_a_registry_pair() -> None:
    _exits(
        json.dumps([{**_BASE, "callers": [_CRM], "op": "create", "voucher": "sale_order", "accounts": ["801"]}]),
        _RO,
        "不在",
    )
    _exits(json.dumps([{**_BASE, "callers": [_CRM], "op": "create"}]), _RO, "voucher 在 op=create 时必须给出")
    _exits(
        json.dumps([{**_BASE, "callers": [_CRM], "op": "delete", "voucher": "sale_order"}]),
        _RO,
        "type=vouchers 时只能是",
    )


# ---- allows / match_of ----


def test_caller_row_admits_any_operator_for_its_pair() -> None:
    rules = _rule("create", "sale_order")
    for operator in ("op001", "op002", "OP003"):
        assert match_of(rules, "co:vouchers/create", _ask("sale_order", operator=operator), _CRM) == "caller"


def test_wrong_or_missing_trust_name_does_not_match() -> None:
    rules = _rule("create", "sale_order")
    for trust in ("tool", "CRM", "crm ", "", None):
        assert allows(rules, "co:vouchers/create", _ask("sale_order"), trust) is False, trust
    # 请求体里的 caller 不参与判定。
    assert allows(rules, "co:vouchers/create", _ask("sale_order", caller="crm:x")) is False


def test_caller_row_keeps_route_voucher_and_source_rules() -> None:
    create = _rule("create", "sale_order")
    assert allows(create, "co:vouchers/create", _ask("dispatch"), _CRM) is False
    assert allows(create, "co:vouchers/update", _ask("sale_order"), _CRM) is False
    assert allows(create, "co:archives/customer_person", _ask("sale_order"), _CRM) is False
    assert allows(create, "co:vouchers/create", {**_ask("sale_order"), "acc": "801"}, _CRM) is False
    verify = _rule("verify", "sale_invoice")
    assert allows(verify, "co:vouchers/verify", _ask("sale_invoice", action="verify"), _CRM) is True
    for action in ("arap_verify", "arap_unverify"):
        assert allows(verify, "co:vouchers/verify", _ask("sale_invoice", action=action), _CRM) is False, action
    assert allows(verify, "co:vouchers/verify", _ask("dispatch", action="verify"), _CRM) is False
    gen = _rule("generate", "sale_invoice")
    assert allows(gen, "co:vouchers/generate", _ask("sale_invoice", source_type="dispatch"), _CRM) is True
    assert allows(gen, "co:vouchers/generate", _ask("sale_invoice", source_type="sale_return"), _CRM) is False


def test_operator_match_is_reported_before_caller() -> None:
    raw = json.dumps(
        [
            {**_BASE, "callers": [_CRM], "op": "create", "voucher": "sale_order"},
            {**_BASE, "operators": ["svc01"], "op": "create", "voucher": "sale_order"},
        ]
    )
    rules = parse_exceptions(raw, _RO)
    assert match_of(rules, "co:vouchers/create", _ask("sale_order", operator="svc01"), _CRM) == "operator"
    assert match_of(rules, "co:vouchers/create", _ask("sale_order", operator="op001"), _CRM) == "caller"
    assert match_of(rules, "co:vouchers/create", _ask("sale_order", operator="op001"), "tool") is None


def test_operator_rows_do_not_match_on_trust_name() -> None:
    rules = parse_exceptions(
        json.dumps([{**_BASE, "operators": ["svc01"], "op": "create", "voucher": "sale_order"}]), _RO
    )
    assert match_of(rules, "co:vouchers/create", _ask("sale_order"), _CRM) is None
    assert match_of(rules, "co:vouchers/create", _ask("sale_order", operator="svc01"), None) == "operator"


def test_forged_caller_rows_do_not_widen() -> None:
    for type_, op, archives, voucher in (
        ("archives", "customer_person", None, None),
        ("vouchers", "head_fix", None, None),
        ("vouchers", "so_contract", None, None),
    ):
        rule = ReadOnlyException(("803",), (), type_, op, archives, voucher, callers=(_CRM,))
        action = ("co:archives/" if type_ == "archives" else "co:vouchers/") + op
        body = _ask("dispatch" if op == "head_fix" else "sale_order")
        assert allows((rule,), action, body, _CRM) is False, op
    both = ReadOnlyException(("803",), ("svc01",), "vouchers", "create", None, "sale_order", callers=(_CRM,))
    assert allows((both,), "co:vouchers/create", _ask("sale_order"), _CRM) is False


# ---- 闸门（走真实路由） ----

_SO_CREATE = _login(type="sale_order", head={"a": "b"}, lines=[{"a": 1}])


def _crm(fake, rules, **over):
    return _gated(fake, read_only_exceptions=rules, trust=(entry(name=_CRM, **over),))


def test_caller_row_over_http_admits_any_operator_and_audits_the_kind(capsys) -> None:
    fake = DryBridge()
    client = _crm(fake, _rule("create", "sale_order"))
    for operator in ("op001", "op002"):
        ok = client.post("/v1/co/vouchers/create", json={**_SO_CREATE, "operator": operator})
        assert ok.status_code == 200, ok.text
    dry = client.post("/v1/co/vouchers/create", json={**_SO_CREATE, "dry_run": True})
    assert dry.status_code == 200, dry.text
    line = audit_line(capsys.readouterr().out, "/v1/co/vouchers/create")
    assert line["read_only_exception"] is True
    assert line["read_only_match"] == "caller"
    assert line["outcome"] == "read_only_exception"
    # 桥收到的 caller 是验过签的「信任项:客户端」。
    assert all(call[1]["caller"].startswith("crm:") for call in fake.calls)


def test_operator_row_audits_operator(capsys) -> None:
    rules = parse_exceptions(
        json.dumps([{**_BASE, "operators": ["svc01"], "op": "create", "voucher": "sale_order"}]), _RO
    )
    client = _gated(FakeBridge(), read_only_exceptions=rules)
    ok = client.post("/v1/co/vouchers/create", json={**_SO_CREATE, "operator": "svc01"})
    assert ok.status_code == 200, ok.text
    line = audit_line(capsys.readouterr().out, "/v1/co/vouchers/create")
    assert line["read_only_match"] == "operator"


def test_other_trust_entry_is_refused_over_http() -> None:
    fake = FakeBridge()
    # support 的缺省信任项 name 是 tool。
    client = _gated(fake, read_only_exceptions=_rule("create", "sale_order"))
    _refused(client.post("/v1/co/vouchers/create", json=_SO_CREATE))
    assert fake.calls == []


def test_caller_row_refuses_other_routes_and_pairs_over_http() -> None:
    fake = FakeBridge()
    client = _crm(fake, _rule("create", "sale_order") + _rule("verify", "sale_invoice"))
    _refused(client.post("/v1/co/vouchers/create", json={**_SO_CREATE, "type": "purchase_order"}))
    _refused(client.post("/v1/co/vouchers/delete", json=_login(type="sale_order", id=1)))
    _refused(client.post("/v1/co/vouchers/verify", json=_login(type="sale_order", id=1, action="verify")))
    _refused(client.post("/v1/co/vouchers/verify", json=_login(type="sale_invoice", id=1, action="arap_verify")))
    _refused(client.post("/v1/co/archives/customer_person", json=_login(code="C900001", person="EX040001")))
    _refused(client.post("/v1/co/sale-orders/verify", json=_login(id=1, action="verify")))
    assert fake.calls == []
    ok = client.post("/v1/co/vouchers/verify", json=_login(type="sale_invoice", id=1, action="verify"))
    assert ok.status_code == 200, ok.text


def test_trust_operator_rule_still_applies() -> None:
    fake = FakeBridge()
    client = _crm(fake, _rule("create", "sale_order"), operators=OperatorRule(deny=frozenset({"op002"})))
    assert client.post("/v1/co/vouchers/create", json=_SO_CREATE).status_code == 200
    denied = client.post("/v1/co/vouchers/create", json={**_SO_CREATE, "operator": "op002"})
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "operator_forbidden"
    assert len(fake.calls) == 1


def test_health_lists_caller_rows() -> None:
    client = _crm(FakeBridge(), _rule("create", "sale_order"))
    data = client.get("/v1/co/health").json()
    assert data["api_read_only_exceptions"] == [{**_BASE, "callers": ["crm"], "op": "create", "voucher": "sale_order"}]


# ---- 桥收到的身份：caller_trust 与 caller 分开（复审 P2） ----


def test_bridge_gets_the_trust_name_as_its_own_field() -> None:
    fake = FakeBridge()
    client = _crm(fake, _rule("create", "sale_order"))
    assert client.post("/v1/co/vouchers/create", json=_SO_CREATE).status_code == 200
    payload = fake.calls[-1][1]
    assert payload[TRUST_FIELD] == _CRM
    assert payload["caller"] == "crm:tool-a"
    # 读路由不带（桥的读路由照旧拒绝未知字段）。
    assert client.post("/v1/co/login-check", json=_login()).status_code == 200
    assert TRUST_FIELD not in fake.calls[-1][1]


def test_long_client_id_keeps_the_caller_exception_on_both_sides(capsys) -> None:
    # azp 200 个字符：「crm:」+ 客户端标识超过 200，caller 换成摘要；caller_trust 仍是 crm，桥按它判定。
    fake = FakeBridge()
    claims = base_claims(u8co_write=True, azp="a" * 200)
    client = _gated(fake, claims=claims, read_only_exceptions=_rule("create", "sale_order"), trust=(entry(name=_CRM),))
    ok = client.post("/v1/co/vouchers/create", json=_SO_CREATE, headers={"Idempotency-Key": "k-1"})
    assert ok.status_code == 200, ok.text
    payload = fake.calls[-1][1]
    assert payload["caller"].startswith("sha256:")
    assert payload[TRUST_FIELD] == _CRM
    assert audit_line(capsys.readouterr().out, "/v1/co/vouchers/create")["read_only_match"] == "caller"


def test_trust_field_does_not_change_the_idempotency_identity() -> None:
    caller = Caller(name=_CRM, client_id="a" * 200)
    fields = idempotency_fields(_Headers({"Idempotency-Key": ["k-1"]}), caller, "/v1/vouchers/create")
    assert fields == {"idempotency_key": "k-1", "caller": caller_tag(caller)}
    assert caller_tag(Caller(name=_CRM, client_id="c1")) == "crm:c1"


def test_request_body_cannot_supply_the_trust_field() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_rule("create", "sale_order"))
    forged = client.post("/v1/co/vouchers/create", json={**_SO_CREATE, TRUST_FIELD: _CRM})
    assert forged.status_code in (400, 422), forged.text
    assert fake.calls == []


@pytest.mark.parametrize("name", (None, "", "crm:x", "1crm", "c" * 64, "客户系统", 7))
def test_caller_trust_rejects_names_outside_the_trust_pattern(name) -> None:
    assert caller_trust(name) is None


def test_caller_trust_keeps_exact_case() -> None:
    assert caller_trust("crm") == "crm"
    assert caller_trust("CRM") == "CRM"
    assert caller_trust("c" * 63) == "c" * 63
