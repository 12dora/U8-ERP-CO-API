"""公司间公共部分（co_ic_core）：账套授权全有或全无、并行调桥与部分失败、翻页上限、并发名额、审计。"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers
from tests.ic_fakes import IcBridge, ic_client, login, sample_map
from tests.support import audit_line, base_claims, entry
from u8co_api.auth import Caller
from u8co_api.co_client import BridgeRoutes
from u8co_api.co_ic_core import FanResult, allow_accs, fanout, ic_map_of, paged, remember_ic
from u8co_api.co_models_ic import IcLogin
from u8co_api.config import Settings
from u8co_api.errors import ApiError, unavailable
from u8co_api.limit import Limit

_MATCH = "/v1/co/reports/intercompany_match"
_WHO = Caller("tool-a", "tool", write=True)


def _request(fake, accounts=("801", "802", "803"), caller=_WHO, routes=None, concurrency=8):
    state = SimpleNamespace(
        settings=Settings(accounts=accounts, ic_map=sample_map()),
        co_bridge=fake,
        co_routes=routes or BridgeRoutes(),
        co_global_limit=Limit(rpm=1_000_000, concurrency=concurrency),
        co_limit=Limit(rpm=1000, concurrency=concurrency),
    )
    request = SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace(), headers=Headers({}))
    request.state.ic_caller = caller
    request.state.trust_name, request.state.caller_id = caller.name, caller.client_id
    return request


def _logins(*accs: str) -> list[IcLogin]:
    return [IcLogin(**login(acc)) for acc in accs]


def _match_body(**extra) -> dict:
    body = {
        "logins": [login("802"), login("801")],
        "seller": {"acc": "802", "type": "sale_out"},
        "buyer": {"acc": "801", "type": "purchase_in"},
        "date_from": "2026-08-01",
        "date_to": "2026-08-31",
    }
    body.update(extra)
    return body


def test_fanout_calls_each_account_once_with_its_own_login() -> None:
    fake = IcBridge()
    request = _request(fake)
    remember_ic(request, "co:reports/aggregate", _logins("801", "802", "803"))
    results = fanout(request, _logins("801", "802", "803"), "/v1/reports/arap_balance", lambda one: {"side": "ar"})
    assert list(results) == ["801", "802", "803"]
    assert all(result.ok and result.status == 200 for result in results.values())
    sent = sorted((payload["acc"], payload["side"], payload["password"]) for _path, payload in fake.calls)
    assert [item[:2] for item in sent] == [("801", "ar"), ("802", "ar"), ("803", "ar")]
    assert all(payload.get("date") and payload.get("year") for _path, payload in fake.calls)
    assert request.state.audit_action == "co:reports/aggregate#801=ok,802=ok,803=ok"
    assert request.state.accs == ["801", "802", "803"]
    assert request.state.operators == ["801=op001", "802=op001", "803=op001"]


def test_fanout_keeps_going_when_one_account_fails() -> None:
    fake = IcBridge()
    fake.errors["803"] = unavailable("CO 桥不可达")
    request = _request(fake)
    results = fanout(request, _logins("801", "803"), "/v1/stock/current", lambda one: {})
    assert results["801"] == FanResult(True, 200, {"ok": True}, None)
    assert results["803"].ok is False
    assert results["803"].status == 503
    assert results["803"].error == {"code": "unavailable", "message": "CO 桥不可达"}
    assert request.state.audit_action.endswith("801=ok,803=unavailable")


def test_unexpected_job_error_is_an_internal_error_not_an_unreachable_bridge(caplog) -> None:
    fake = IcBridge()
    fake.errors["803"] = RuntimeError("boom")  # type: ignore[assignment]
    request = _request(fake)
    results = fanout(request, _logins("801", "803"), "/v1/stock/current", lambda one: {})
    assert results["801"].ok is True
    assert (results["803"].ok, results["803"].status) == (False, 500)
    assert results["803"].error == {"code": "internal", "message": "账套 803 的处理出现内部错误"}
    assert any("803" in record.getMessage() for record in caplog.records)


def test_fanout_uses_the_routed_bridge_per_account() -> None:
    default, routed = IcBridge(), IcBridge()
    request = _request(default, routes=BridgeRoutes((("routes[0]", ("803",), routed),)))
    fanout(request, _logins("801", "803"), "/v1/stock/current", lambda one: {})
    assert default.paths() == ["/v1/stock/current"] and default.calls[0][1]["acc"] == "801"
    assert routed.calls[0][1]["acc"] == "803"
    assert request.state.bridge == "801=default,803=routes[0]"


def test_fanout_refuses_all_accounts_when_one_is_not_allowed() -> None:
    fake = IcBridge()
    request = _request(fake, caller=Caller("tool-a", "tool", write=True, accounts=frozenset({"801", "802"})))
    with pytest.raises(ApiError) as caught:
        fanout(request, _logins("801", "803"), "/v1/stock/current", lambda one: {})
    assert (caught.value.status, caught.value.code) == (403, "account_not_allowed")
    assert fake.calls == []
    with pytest.raises(ApiError):
        allow_accs(_request(fake, accounts=("801",)), ["801", "802"])


def test_fanout_runs_accounts_in_parallel_up_to_three() -> None:
    gate = threading.Barrier(3, timeout=5)

    class Slow(IcBridge):
        def call(self, path: str, payload: dict) -> dict:
            gate.wait()
            return super().call(path, payload)

    results = fanout(_request(Slow()), _logins("801", "802", "803"), "/v1/stock/current", lambda one: {})
    assert all(result.ok for result in results.values())


def test_fanout_falls_back_to_one_thread_without_spare_slots() -> None:
    request = _request(IcBridge(), concurrency=1)
    request.app.state.co_global_limit.acquire("co")  # 路由依赖已占的那个名额
    results = fanout(request, _logins("801", "802"), "/v1/stock/current", lambda one: {})
    assert all(result.ok for result in results.values())
    request.app.state.co_global_limit.release("co")
    assert request.app.state.co_global_limit.acquire("co") is True


def test_fanout_reports_a_timeout_per_account() -> None:
    release = threading.Event()

    class Stuck(IcBridge):
        def call(self, path: str, payload: dict) -> dict:
            if payload["acc"] == "802":
                release.wait(5)
            return super().call(path, payload)

    try:
        results = fanout(_request(Stuck()), _logins("801", "802"), "/v1/stock/current", lambda one: {}, timeout=0.2)
    finally:
        release.set()
    assert results["801"].ok is True
    assert (results["802"].status, results["802"].error["code"]) == (504, "timeout")


def test_paged_follows_next_and_stops_at_the_cap() -> None:
    fake = IcBridge()
    fake.heads[("801", "purchase_in")] = [{"id": n} for n in range(1, 8)]
    request = _request(fake)
    body = {"type": "purchase_in"}
    rows = paged(request, IcLogin(**login("801")), "/v1/vouchers/search", body, page_size=3, max_pages=3)
    assert [row["id"] for row in rows] == list(range(1, 8))
    assert [payload.get("after") for _path, payload in fake.calls] == [None, 3, 6]
    with pytest.raises(ApiError) as caught:
        paged(request, IcLogin(**login("801")), "/v1/vouchers/search", body, page_size=3, max_pages=2)
    assert (caught.value.status, caught.value.code) == (422, "ic_too_many_rows")


def test_unconfigured_map_is_404() -> None:
    request = _request(IcBridge())
    request.app.state.settings = Settings(accounts=("801",))
    with pytest.raises(ApiError) as caught:
        ic_map_of(request)
    assert (caught.value.status, caught.value.code, caught.value.message) == (404, "ic_not_configured", "未配置公司间对照")


def test_route_is_404_without_a_map_and_never_calls_the_bridge() -> None:
    fake = IcBridge()
    denied = ic_client(fake, ic_map=None).post(_MATCH, json=_match_body())
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "ic_not_configured"
    assert fake.calls == []


def test_route_needs_every_account_in_the_token() -> None:
    fake = IcBridge()
    trust = (entry(accounts_claim="u8co_accounts", mgmt_claim="u8co_mgmt"),)
    claims = base_claims(u8co_read=True, u8co_mgmt=True, u8co_accounts=["801"])
    denied = ic_client(fake, claims=claims, trust=trust).post(_MATCH, json=_match_body())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []
    outside = ic_client(fake, co_accounts=("801", "803")).post(_MATCH, json=_match_body())
    assert outside.status_code == 403
    assert fake.calls == []


def test_route_rejects_accounts_from_different_groups() -> None:
    fake = IcBridge()
    body = _match_body(logins=[login("802"), login("801"), login("998")])
    denied = ic_client(fake, co_accounts=("801", "802", "998")).post(_MATCH, json=body)
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "ic_group_mismatch"
    assert fake.calls == []


def test_route_writes_one_audit_row_with_every_account(capsys) -> None:
    fake = IcBridge()
    ok = ic_client(fake, claims=base_claims(u8co_read=True, u8co_mgmt=True)).post(_MATCH, json=_match_body())
    assert ok.status_code == 200, ok.text
    row = audit_line(capsys.readouterr().out, _MATCH)
    assert row["accs"] == ["802", "801"]
    assert row["operators"] == ["802=op001", "801=op001"]
    assert row["action"] == "co:reports/intercompany_match#802:sale_out>801:purchase_in:801=ok,802=ok"
    assert row["bridge"] == "801=default,802=default"
    assert "sentinel" not in str(row)
