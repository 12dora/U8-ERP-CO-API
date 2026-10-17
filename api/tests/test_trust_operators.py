"""信任项的 operators（allow / deny）：调用方能用哪些 U8 登录操作员。假桥，不访问网络。
单账套读写路由、perm、幂等查询、公司间和经营管理多账套都在联系桥之前判定；与写入策略的操作员规则是两层。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from tests.ic_fakes import ic_client, login
from tests.support import base_claims, entry
from tests.test_co_gate import FakeBridge, _client, _login
from tests.test_co_ic_core import _logins, _request
from tests.test_co_ic_match import _body as _match_body
from tests.test_co_ic_match import _goods
from tests.test_co_meta import MetaBridge
from tests.test_co_mgmt import _PNL, MgmtFake, _mgmt_client, _pnl
from u8co_api.auth import Caller, caller_from
from u8co_api.co_ic_core import fanout, remember_ic
from u8co_api.errors import ApiError
from u8co_api.trust import OperatorRule, load_trust_file, parse_entry

_ROW = {
    "name": "app-a",
    "issuer": "https://idp.example.com",
    "audience": "u8co-api",
    "jwks_url": "https://idp.example.com/oauth/jwks",
}
_CHECK = "/v1/co/login-check"
_LOAD = "/v1/co/vouchers/load"
_WRITE = "/v1/co/sale-orders/verify"
_SNAP = "/v1/co/perm/snapshot"
_EVAL = "/v1/co/perm/evaluate"
_IDEM = "/v1/co/idempotency/get"
_MATCH = "/v1/co/reports/intercompany_match"
_BODY = {
    "message": "该调用方不能使用此 U8 操作员",
    "code": "operator_forbidden",
    "retryable": False,
    "field": "operator",
}


def _rule(**lists: list[str]) -> OperatorRule:
    return parse_entry(dict(_ROW, operators=lists)).operators


def _trust(**lists: list[str]):
    return (entry(perm_evaluate=True, operators=_rule(**lists)),)


def _forbidden(response) -> None:
    assert response.status_code == 403, response.text
    assert response.json() == {"error": _BODY}


def _audit_403(out: str, endpoint: str) -> dict:
    for line in out.splitlines():
        if line.startswith("{") and json.loads(line).get("endpoint") == endpoint:
            return json.loads(line)
    raise AssertionError("missing audit line")


# ---- 解析 ----


def test_missing_key_means_no_restriction() -> None:
    assert parse_entry(dict(_ROW)).operators is None
    assert parse_entry(dict(_ROW, operators={})).operators is None
    assert parse_entry(dict(_ROW, operators={"allow": [], "deny": []})).operators is None


def test_lists_are_trimmed_and_case_folded(tmp_path) -> None:
    path = tmp_path / "trust.json"
    rows = [dict(_ROW, operators={"allow": [" OP001 ", "Op002"], "deny": ["SVC-A"]})]
    path.write_text(json.dumps(rows), encoding="utf-8")
    rule = load_trust_file(str(path))[0].operators
    assert rule == OperatorRule(frozenset({"op001", "op002"}), frozenset({"svc-a"}))


@pytest.mark.parametrize(
    "operators",
    ("op001", ["op001"], {"allow": "op001"}, {"allow": [""]}, {"deny": ["  "]}, {"deny": [1]}, {"allow": [None]}, {"block": ["a"]}),
)
def test_bad_operators_stop_the_process(operators: object) -> None:
    with pytest.raises(SystemExit, match="operators"):
        parse_entry(dict(_ROW, operators=operators))


def test_rule_semantics() -> None:
    assert _rule(allow=["a"]).permits("A") and not _rule(allow=["a"]).permits("b")
    assert not _rule(deny=["a"]).permits(" A ") and _rule(deny=["a"]).permits("b")
    assert not _rule(allow=["a"], deny=["a"]).permits("a")


def test_caller_carries_the_rule_from_the_trust_entry() -> None:
    rule = _rule(deny=["svc-a"])
    assert caller_from(base_claims(), entry(operators=rule)).operators == rule
    assert caller_from(base_claims(), entry()).operators is None


# ---- 单账套路由 ----


def test_allow_list_rejects_any_other_operator(capsys) -> None:
    fake = FakeBridge()
    client = _client(fake, trust=_trust(allow=["op009"]))
    _forbidden(client.post(_CHECK, json=_login()))
    _forbidden(client.post(_LOAD, json=_login(type="other_in", id=1)))
    _forbidden(client.post(_WRITE, json=_login(id=1, action="verify")))
    assert fake.calls == []
    assert client.post(_CHECK, json=_login(operator="op009")).status_code == 200
    assert len(fake.calls) == 1


def test_denial_is_audited_as_denied(capsys) -> None:
    client = _client(FakeBridge(), trust=_trust(deny=["op001"]))
    capsys.readouterr()
    _forbidden(client.post(_CHECK, json=_login()))
    line = _audit_403(capsys.readouterr().out, _CHECK)
    assert line["status"] == 403 and line["outcome"] == "denied"
    assert line["operators"] == ["803=op001"] and "password" not in json.dumps(line)


def test_deny_list_rejects_and_wins_over_allow() -> None:
    fake = FakeBridge()
    client = _client(fake, trust=_trust(deny=["op001"]))
    _forbidden(client.post(_CHECK, json=_login()))
    assert client.post(_CHECK, json=_login(operator="op002")).status_code == 200
    both = _client(fake, trust=_trust(allow=["op001"], deny=["op001"]))
    _forbidden(both.post(_CHECK, json=_login()))
    assert len(fake.calls) == 1


@pytest.mark.parametrize("sent", ("OP001", "Op001", "op001"))
def test_matching_is_case_insensitive_and_trimmed(sent: str) -> None:
    fake = FakeBridge()
    denying = _client(fake, trust=_trust(deny=[" Op001 "]))
    _forbidden(denying.post(_CHECK, json=_login(operator=sent)))
    allowing = _client(fake, trust=_trust(allow=[" OP001"]))
    assert allowing.post(_CHECK, json=_login(operator=sent)).status_code == 200
    assert len(fake.calls) == 1


def test_no_operators_key_changes_nothing() -> None:
    fake = FakeBridge()
    client = _client(fake, trust=(entry(perm_evaluate=True),))
    assert client.post(_CHECK, json=_login(operator="svc-a")).status_code == 200


def test_idempotency_lookup_and_snapshot_are_checked() -> None:
    fake = FakeBridge()
    client = _client(fake, claims=base_claims(u8co_read=True), trust=_trust(deny=["svc-a"]))
    body = _login(operator="svc-a")
    _forbidden(client.post(_SNAP, json=body))
    _forbidden(client.post(_IDEM, json=dict(body, path="/v1/co/vouchers/create", key="order-1")))
    assert fake.calls == []


def test_meta_has_no_operator_and_is_not_blocked() -> None:
    fake = MetaBridge()
    client = _client(fake, claims=base_claims(u8co_read=True), trust=_trust(allow=["op009"]))
    assert client.get("/v1/co/meta").status_code == 200
    assert fake.meta_calls == 1


# ---- perm/evaluate：只查登录操作员，不查 subject ----


def test_perm_evaluate_checks_the_login_operator_only() -> None:
    fake = FakeBridge()
    claims = base_claims(u8co_read=True)
    # subject 在 deny 里，登录操作员没有：放行。
    client = _client(fake, claims=claims, trust=_trust(deny=["op002"]))
    ok = client.post(_EVAL, json=_login(subject="op002"))
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["subject"] == "op002"
    # 登录操作员在 deny 里：403，不访问桥。
    fake.calls.clear()
    blocked = _client(fake, claims=claims, trust=_trust(deny=["op001"]))
    _forbidden(blocked.post(_EVAL, json=_login(subject="op002")))
    assert fake.calls == []
    # allow 只列了登录操作员，subject 不在 allow 里也放行。
    listed = _client(fake, claims=claims, trust=_trust(allow=["op001"]))
    assert listed.post(_EVAL, json=_login(subject="op777")).status_code == 200


# ---- 公司间、经营管理 ----


def _mixed() -> dict:
    return {**_match_body(), "logins": [login("802"), login("801", operator="svc-a")]}


def test_intercompany_fanout_with_one_forbidden_login_calls_no_bridge() -> None:
    fake = _goods()
    client = ic_client(fake, trust=(entry(mgmt_claim="u8co_mgmt", operators=_rule(deny=["svc-a"])),))
    _forbidden(client.post(_MATCH, json=_mixed()))
    assert fake.calls == []
    assert client.post(_MATCH, json=_match_body()).status_code == 200


def test_intercompany_allow_list_rejects_a_stranger() -> None:
    fake = _goods()
    client = ic_client(fake, trust=(entry(mgmt_claim="u8co_mgmt", operators=_rule(allow=["op001"])),))
    _forbidden(client.post(_MATCH, json=_mixed()))
    assert fake.calls == []


def test_mgmt_with_one_forbidden_login_calls_no_bridge() -> None:
    fake = MgmtFake()
    trust = (entry(mgmt_claim="u8co_mgmt", operators=_rule(deny=["svc-a"])),)
    client = _mgmt_client(fake, trust=trust)
    body = _pnl(("801", "802"))
    body["logins"][1]["operator"] = "SVC-A"
    _forbidden(client.post(_PNL, json=body))
    assert fake.calls == []
    assert client.post(_PNL, json=_pnl(("801", "802"))).status_code == 200


def test_fanout_helper_refuses_before_any_bridge_call() -> None:
    fake = SimpleNamespace(calls=[])
    request = _request(fake)
    request.state.operator_rule = _rule(allow=["op001"])
    bad = _logins("801", "802")
    bad[1] = bad[1].model_copy(update={"operator": "svc-a"})
    with pytest.raises(ApiError) as caught:
        remember_ic(request, "co:reports/aggregate", bad)
    assert (caught.value.status, caught.value.code, caught.value.field) == (403, "operator_forbidden", "operator")
    assert request.state.outcome == "denied"
    request.state.outcome = None
    with pytest.raises(ApiError):
        fanout(request, bad, "/v1/stock/current", lambda one: {})
    assert fake.calls == []


def test_caller_dataclass_defaults_to_unrestricted() -> None:
    assert Caller("c", "t").operators is None
