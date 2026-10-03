"""幂等键：Idempotency-Key 头转成桥请求体的 idempotency_key 和 caller。桥是本地假桥，不访问网络。"""

from __future__ import annotations

import json

import pytest
from tests.test_co_access import _READS, _WRITES
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from tests.test_co_gl_arc import _FIELDS
from tests.test_co_gl_arc import _HEAD as _GL_HEAD
from tests.test_co_gl_arc import _LINES as _GL_LINES
from u8co_api.auth import Caller
from u8co_api.co_bridge import to_api_error
from u8co_api.co_access import ACCESS, READ, WRITE
from u8co_api.co_ic_core import FANOUT_ACTIONS
from u8co_api.co_idem import IDEMPOTENT_PATHS, caller_tag
from u8co_api.co_routes import POST_ROUTES

_KEY = "order-2026-0001"

_CREATES = (
    (
        "/v1/co/vouchers/create",
        "/v1/vouchers/create",
        _auth(type="other_in", head={"a": "b"}, lines=[{"a": 1}]),
    ),
    ("/v1/co/vouchers/generate", "/v1/vouchers/generate", _auth(type="sale_out", id=9)),
    (
        "/v1/co/gl/vouchers/create",
        "/v1/gl/vouchers/create",
        _auth(head=_GL_HEAD, lines=_GL_LINES),
    ),
    (
        "/v1/co/archives/create",
        "/v1/archives/create",
        _auth(archive="customer", code="C900001", fields=_FIELDS),
    ),
)

# 全部写路由都收 Idempotency-Key（审核、删除、修改、总账各操作、记账、审批、档案、核销、制单……）；只读路由仍 400。
_WRITE_PATHS = {route.path for route in POST_ROUTES if ACCESS[route.action] == WRITE}
# 原样转给同名桥路由的写路由；intercompany/generate_buyer 是多账套路由，内部改调买方账套的 vouchers/generate
# （键随那次调用转给桥），不在 _WRITES 里逐条转发，另见 test_co_ic_generate。
_FORWARDED_WRITE_PATHS = {route.path for route in POST_ROUTES if ACCESS[route.action] == WRITE} - {
    route.path for route in POST_ROUTES if route.action in FANOUT_ACTIONS
}


def test_bridge_path_table_is_every_write_route() -> None:
    writes = {route.bridge_path for route in POST_ROUTES if ACCESS[route.action] == WRITE}
    assert IDEMPOTENT_PATHS == writes
    assert {bridge for _path, bridge, _body in _CREATES} <= IDEMPOTENT_PATHS
    assert {path for path, _body in _WRITES} == _FORWARDED_WRITE_PATHS
    assert not IDEMPOTENT_PATHS & {route.bridge_path for route in POST_ROUTES if ACCESS[route.action] == READ}


@pytest.mark.parametrize(("path", "body"), _WRITES)
def test_every_write_route_forwards_the_key(path: str, body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(path, json=body, headers={"Idempotency-Key": _KEY})
    assert ok.status_code == 200, ok.text
    seen, payload = fake.calls[-1]
    assert "/v1/co" + seen.removeprefix("/v1") == path
    assert payload["idempotency_key"] == _KEY
    assert payload["caller"] == "tool:tool-a"


@pytest.mark.parametrize(("path", "bridge", "body"), _CREATES)
def test_header_is_forwarded_with_the_caller(path: str, bridge: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post(path, json=body, headers={"Idempotency-Key": _KEY})
    assert ok.status_code == 200, ok.text
    seen, payload = fake.calls[-1]
    assert seen == bridge
    assert payload["idempotency_key"] == _KEY
    assert payload["caller"] == "tool:tool-a"


@pytest.mark.parametrize(("path", "bridge", "body"), _CREATES)
def test_without_header_only_the_caller_is_added(path: str, bridge: str, body: dict) -> None:
    # 写入一律带 caller（桥只记进审计，不做幂等）；没带头时不加 idempotency_key。
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post(path, json=body)
    assert ok.status_code == 200, ok.text
    payload = fake.calls[-1][1]
    assert "idempotency_key" not in payload
    assert payload["caller"] == "tool:tool-a"


@pytest.mark.parametrize(("path", "body"), _READS)
def test_header_on_read_routes_is_400(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body, headers={"Idempotency-Key": _KEY})
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize("key", ("a b", "k" * 129, "键"))
def test_bad_keys_are_400_before_the_bridge(key: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    path, _bridge, body = _CREATES[0]
    denied = client.post(path, json=body, headers={"Idempotency-Key": key.encode("utf-8")})
    assert denied.status_code == 400
    assert fake.calls == []


def test_longest_visible_key_is_accepted() -> None:
    fake = FakeBridge()
    client = _client(fake)
    key = "~!" * 64
    path, _bridge, body = _CREATES[0]
    ok = client.post(path, json=body, headers={"Idempotency-Key": key})
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["idempotency_key"] == key


def test_repeated_header_is_400() -> None:
    fake = FakeBridge()
    client = _client(fake)
    path, _bridge, body = _CREATES[0]
    headers = [("Idempotency-Key", "a"), ("Idempotency-Key", "b")]
    denied = client.post(path, json=body, headers=headers)
    assert denied.status_code == 400
    assert fake.calls == []


def test_same_key_from_two_clients_gets_two_callers() -> None:
    first = caller_tag(Caller(client_id="tool-a", name="tool"))
    second = caller_tag(Caller(client_id="tool-b", name="tool"))
    assert first == "tool:tool-a"
    assert first != second


def test_long_or_odd_callers_are_hashed() -> None:
    long_id = caller_tag(Caller(client_id="c" * 300, name="tool"))
    assert long_id.startswith("sha256:")
    assert len(long_id) <= 200
    odd = caller_tag(Caller(client_id="a\nb", name="tool"))
    assert odd.startswith("sha256:")


def test_sealed_body_carries_the_key_and_no_password() -> None:
    path, bridge, body = _CREATES[0]
    with _Up() as server:
        client = _wired(server.base_url)
        ok = client.post(path, json=body, headers={"Idempotency-Key": _KEY})
        assert ok.status_code == 200, ok.text
        _method, seen, raw = server.httpd.hits[-1]
    sent = json.loads(raw)
    assert seen == "/u8co" + bridge
    assert set(sent) == _sealed("type", "head", "lines", "idempotency_key", "caller")
    assert sent["idempotency_key"] == _KEY
    assert b'"password"' not in raw


def test_mismatch_maps_to_409() -> None:
    error = to_api_error(
        409,
        {
            "ok": False,
            "code": "idempotency_mismatch",
            "message": "该幂等键已用于内容不同的请求",
        },
    )
    assert error.status == 409
    assert error.code == "idempotency_mismatch"


def test_replayed_outcome_unknown_stays_504() -> None:
    error = to_api_error(504, {"ok": False, "code": "outcome_unknown", "message": "结果未知"})
    assert error.status == 504
    assert error.code == "outcome_unknown"


def test_store_unavailable_maps_to_503() -> None:
    error = to_api_error(503, {"ok": False, "code": "store_unavailable", "message": "幂等记录无法读写"})
    assert error.status == 503
    assert error.code == "store_unavailable"


def test_bridge_mismatch_reaches_the_caller() -> None:
    fake = FakeBridge()
    fake.error = to_api_error(409, {"ok": False, "code": "idempotency_mismatch", "message": "内容不同"})
    client = _client(fake)
    path, _bridge, body = _CREATES[0]
    denied = client.post(path, json=body, headers={"Idempotency-Key": _KEY})
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "idempotency_mismatch"


def test_openapi_documents_the_header_only_on_write_routes() -> None:
    spec = _spec()
    for route in POST_ROUTES:
        params = spec["paths"][route.path]["post"].get("parameters", [])
        has = any(item.get("name") == "Idempotency-Key" and item.get("in") == "header" for item in params)
        assert has == (route.path in _WRITE_PATHS), route.path
    assert "idempotency_mismatch" in spec["info"]["description"]
