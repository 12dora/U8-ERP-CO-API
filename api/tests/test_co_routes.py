"""Bridge payload and error propagation. Password stays on the bridge call only."""

from tests.test_co_gate import _SECRET, FakeBridge, _client, _login
from u8co_api.errors import ApiError


def _verify(**extra) -> dict:
    body = _login(id=1, action="verify", year="2026", date="2026-09-27")
    body.update(extra)
    return body


def test_login_payload_is_plain_and_pathed():
    fake = FakeBridge()
    client = _client(fake)
    body = _login(year="2026", date="2026-09-27")
    ok = client.post("/v1/co/login-check", json=body)
    assert ok.status_code == 200
    assert ok.json()["operator"] == "op001"
    assert _SECRET not in ok.text
    assert fake.calls == [("/v1/login-check", body)]
    assert "password_enc" not in fake.calls[0][1]


def test_verify_paths_keep_the_plain_password():
    fake = FakeBridge()
    client = _client(fake)
    body = _verify()
    sale = client.post("/v1/co/sale-orders/verify", json=body)
    assert sale.status_code == 200
    assert fake.calls[0][0] == "/v1/sale-orders/verify"
    assert fake.calls[0][1]["password"] == _SECRET
    dispatch = client.post("/v1/co/dispatches/verify", json=body)
    assert dispatch.status_code == 200
    assert fake.calls[1][0] == "/v1/dispatches/verify"
    assert _SECRET not in sale.text
    assert _SECRET not in dispatch.text


def test_unknown_field_is_400_before_the_bridge():
    fake = FakeBridge()
    client = _client(fake)
    body = _login(year="2026", date="2026-09-27")
    body["note"] = "x"
    denied = client.post("/v1/co/login-check", json=body)
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_bridge_error_keeps_status_and_code():
    fake = FakeBridge()
    client = _client(fake)
    body = _verify()
    fake.error = ApiError(409, "state_mismatch", "单据已是目标状态")
    mismatch = client.post("/v1/co/sale-orders/verify", json=body)
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "state_mismatch"
    fake.error = ApiError(504, "outcome_unknown", "结果未知")
    unknown = client.post("/v1/co/dispatches/verify", json=body)
    assert unknown.status_code == 504
    assert unknown.json()["error"]["code"] == "outcome_unknown"
    assert _SECRET not in mismatch.text
    assert _SECRET not in unknown.text


def test_health_returns_the_bridge_body():
    fake = FakeBridge()
    client = _client(fake)
    ok = client.get("/v1/co/health")
    assert ok.status_code == 200
    assert ok.json() == {"ok": True, "version": "test"}
    fake.error = ApiError(503, "unavailable", "CO 桥不可达")
    down = client.get("/v1/co/health")
    assert down.status_code == 503
    assert down.json()["error"]["code"] == "unavailable"
