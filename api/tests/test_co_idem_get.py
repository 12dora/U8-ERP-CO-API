"""幂等结果查询 /v1/co/idempotency/get：与原请求相同的 caller，失败的记录换成本服务的错误格式。"""

from __future__ import annotations

import pytest
from tests.support import base_claims
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, WRITE
from u8co_api.co_routes import POST_ROUTES

_PATH = "/v1/co/idempotency/get"
_KEY = "order-2026-0001"


class IdemBridge(FakeBridge):
    def __init__(self, reply: dict | None = None) -> None:
        super().__init__()
        self.reply = reply

    def call(self, path: str, payload: dict) -> dict:
        if path == "/v1/idempotency/get":
            self.calls.append((path, dict(payload)))
            return self.reply if self.reply is not None else {"ok": True, "found": False}
        return super().call(path, payload)


def _body(path: str = "/v1/co/vouchers/create", key: str = _KEY) -> dict:
    return _auth(path=path, key=key)


@pytest.mark.parametrize(
    ("api_path", "route"),
    [
        ("/v1/co/vouchers/create", "/u8co/v1/vouchers/create"),
        ("/v1/co/vouchers/generate", "/u8co/v1/vouchers/generate"),
        ("/v1/co/gl/vouchers/create", "/u8co/v1/gl/vouchers/create"),
        ("/v1/co/archives/create", "/u8co/v1/archives/create"),
        # 任一写路由都能查。
        ("/v1/co/vouchers/verify", "/u8co/v1/vouchers/verify"),
        ("/v1/co/vouchers/delete", "/u8co/v1/vouchers/delete"),
        ("/v1/co/sale-orders/verify", "/u8co/v1/sale-orders/verify"),
        ("/v1/co/gl/vouchers/void", "/u8co/v1/gl/vouchers/void"),
        ("/v1/co/gl/vouchers/post", "/u8co/v1/gl/vouchers/post"),
        ("/v1/co/openings/post", "/u8co/v1/openings/post"),
        ("/v1/co/openings/arap", "/u8co/v1/openings/arap"),
        ("/v1/co/periods/close", "/u8co/v1/periods/close"),
        ("/v1/co/ia/post", "/u8co/v1/ia/post"),
        ("/v1/co/ia/period_end", "/u8co/v1/ia/period_end"),
        ("/v1/co/workflow/approve", "/u8co/v1/workflow/approve"),
        ("/v1/co/archives/delete", "/u8co/v1/archives/delete"),
        ("/v1/co/arap/writeoff/auto", "/u8co/v1/arap/writeoff/auto"),
        # 公司间生成买方单据的记录在桥的 vouchers/generate 下。
        ("/v1/co/intercompany/generate_buyer", "/u8co/v1/vouchers/generate"),
    ],
)
def test_lookup_forwards_the_bridge_route_key_and_caller(api_path: str, route: str) -> None:
    fake = IdemBridge()
    ok = _client(fake).post(_PATH, json=_body(api_path))
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"ok": True, "found": False}
    path, payload = fake.calls[0]
    assert path == "/v1/idempotency/get"
    assert payload["route"] == route
    assert payload["idempotency_key"] == _KEY
    assert payload["caller"] == "tool:tool-a"
    assert "path" not in payload
    assert "key" not in payload


def test_lookup_uses_the_same_caller_as_the_original_request() -> None:
    fake = IdemBridge()
    client = _client(fake)
    create = _auth(type="sale_order", head={"a": "b"}, lines=[{"a": 1}])
    assert client.post("/v1/co/vouchers/create", json=create, headers={"Idempotency-Key": _KEY}).status_code == 200
    assert client.post(_PATH, json=_body()).status_code == 200
    original, lookup = fake.calls[0][1], fake.calls[1][1]
    assert original["caller"] == lookup["caller"]
    assert original["idempotency_key"] == lookup["idempotency_key"]
    assert original["acc"] == lookup["acc"]


def test_stored_success_is_returned_as_is() -> None:
    stored = {"ok": True, "type": "sale_order", "id": 1000123, "code": "0000000123"}
    reply = {"ok": True, "found": True, "state": "ok", "status": 200, "created_utc": "2026-09-29T08:00:00Z"}
    fake = IdemBridge({**reply, "response": stored})
    ok = _client(fake).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    assert ok.json() == {**reply, "response": stored}


def test_stored_outcome_unknown_is_translated_to_the_api_error_shape() -> None:
    stored = {"ok": False, "code": "outcome_unknown", "message": "已送出但没有结果"}
    reply = {"ok": True, "found": True, "state": "outcome_unknown", "status": 504, "created_utc": "2026-09-29T08:00:00Z"}
    fake = IdemBridge({**reply, "response": stored})
    ok = _client(fake).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["status"] == 504
    error = data["response"]["error"]
    assert error["code"] == "outcome_unknown"
    assert error["message"] == "已送出但没有结果"
    assert error["retryable"] is False
    assert "hint" in error
    assert "ok" not in data["response"]


def test_stored_error_status_follows_the_api_mapping() -> None:
    # 桥的 500 internal 在本服务是 502；retryable 与正常错误响应相同。
    stored = {"ok": False, "code": "internal", "message": "内部错误"}
    reply = {"ok": True, "found": True, "state": "outcome_unknown", "status": 500, "response": stored}
    ok = _client(IdemBridge(reply)).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == 502
    assert ok.json()["response"]["error"]["code"] == "internal"
    busy = {"ok": True, "found": True, "state": "outcome_unknown", "status": 503}
    busy["response"] = {"ok": False, "code": "busy_timeout", "message": "排队超时"}
    ok = _client(IdemBridge(busy)).post(_PATH, json=_body())
    assert ok.json()["response"]["error"]["retryable"] is True


def test_in_flight_has_no_response() -> None:
    reply = {"ok": True, "found": True, "state": "in_flight", "created_utc": "2026-09-29T08:00:00Z"}
    ok = _client(IdemBridge(reply)).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    assert ok.json() == reply


@pytest.mark.parametrize(
    ("body", "field"),
    [
        (_body(path="/v1/co/vouchers/load"), "path"),
        (_body(path="/v1/co/idempotency/get"), "path"),
        (_body(path="/v1/vouchers/create"), "path"),
        (_body(key=""), "key"),
        (_body(key="has space"), "key"),
        (_body(key="k" * 129), "key"),
        (_auth(path="/v1/co/vouchers/create"), "key"),
    ],
)
def test_bad_lookups_are_400_before_the_bridge(body: dict, field: str) -> None:
    fake = IdemBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == field
    assert fake.calls == []


def test_lookup_itself_does_not_take_an_idempotency_key() -> None:
    fake = IdemBridge()
    denied = _client(fake).post(_PATH, json=_body(), headers={"Idempotency-Key": _KEY})
    assert denied.status_code == 400
    assert fake.calls == []


def test_read_only_caller_may_look_up() -> None:
    fake = IdemBridge()
    ok = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text


def test_lookup_path_enum_is_every_write_route() -> None:
    spec = _spec()
    writes = {route.path for route in POST_ROUTES if ACCESS[route.action] == WRITE}
    assert set(spec["components"]["schemas"]["IdemGetIn"]["properties"]["path"]["enum"]) == writes


def test_openapi_describes_the_lookup() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["operationId"] == "coIdempotencyGet"
    assert operation["x-u8co-access"] == "read"
    assert not any(item.get("name") == "Idempotency-Key" for item in operation.get("parameters", []))
