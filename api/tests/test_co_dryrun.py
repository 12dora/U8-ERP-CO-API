"""写操作预演：dry_run 只在为 true 时转给桥，预演响应按 DryRunOut，不能带 Idempotency-Key。"""

from __future__ import annotations

import pytest
from tests.test_co_access import _READS, _WRITES
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, WRITE
from u8co_api.co_dryrun import PLAN_ONLY, supports
from u8co_api.co_routes import POST_ROUTES

_LEGACY = ("/v1/co/sale-orders/verify", "/v1/co/dispatches/verify")
_AUTO = "/v1/co/arap/writeoff/auto"
_DRY_WRITES = tuple((path, body) for path, body in _WRITES if path not in _LEGACY and path != _AUTO)
_DOC = {
    "type": "sale_order",
    "id": 1000123,
    "code": "0000000123",
    "state": "exists",
    "head": {"ccuscode": "C001", "ddate": "2024-06-01", "cmemo": ""},
    "lines": [{"autoid": 1, "cinvcode": "A01", "iquantity": 5.0}],
    "lines_total": 1,
}


def _preview(path: str) -> dict:
    return {
        "ok": True,
        "dry_run": True,
        "mode": "rollback",
        "route": path.removeprefix("/v1/"),
        "type": "sale_order",
        "action": "create",
        "docs": [dict(_DOC)],
        "detail": {"planned": 1},
        "warnings": ["number_may_skip", "locks_held"],
        "message": "预演完成，已回滚，没有写入",
    }


class DryBridge(FakeBridge):
    """dry_run 为 true 时回预演结果，否则按普通假桥。"""

    def __init__(self, reply: dict | None = None) -> None:
        super().__init__()
        self.reply = reply

    def call(self, path: str, payload: dict) -> dict:
        if payload.get("dry_run") is True and path not in PLAN_ONLY:
            self.calls.append((path, dict(payload)))
            return self.reply if self.reply is not None else _preview(path)
        return super().call(path, payload)


@pytest.mark.parametrize(("path", "body"), _DRY_WRITES)
def test_every_write_route_forwards_dry_run_and_returns_the_preview(path: str, body: dict) -> None:
    fake = DryBridge()
    ok = _client(fake).post(path, json={**body, "dry_run": True})
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["dry_run"] is True
    assert data["mode"] == "rollback"
    assert data["docs"][0]["head"]["ccuscode"] == "C001"
    assert data["warnings"] == ["number_may_skip", "locks_held"]
    assert data["detail"] == {"planned": 1}
    assert len(fake.calls) == 1
    assert fake.calls[0][1]["dry_run"] is True


@pytest.mark.parametrize(("path", "body"), _DRY_WRITES)
def test_normal_writes_keep_the_old_bridge_body(path: str, body: dict) -> None:
    fake = FakeBridge()
    plain = _client(fake).post(path, json=body)
    assert plain.status_code == 200, plain.text
    explicit = _client(fake).post(path, json={**body, "dry_run": False})
    assert explicit.status_code == 200, explicit.text
    assert len(fake.calls) == 2
    assert "dry_run" not in fake.calls[0][1]
    assert fake.calls[0][1] == fake.calls[1][1]


@pytest.mark.parametrize("path", _LEGACY)
def test_legacy_verify_routes_reject_dry_run_before_the_bridge(path: str) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=_auth(id=1, action="verify", dry_run=True))
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "bad_request"
    assert denied.json()["error"]["field"] == "dry_run"
    assert fake.calls == []


@pytest.mark.parametrize(("path", "body"), _READS)
def test_read_routes_do_not_take_dry_run(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json={**body, "dry_run": True})
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["field"] == "dry_run"
    assert fake.calls == []


def test_auto_writeoff_keeps_its_plan_only_dry_run() -> None:
    fake = FakeBridge()
    body = _auth(flag="AR", partner="C001", dry_run=True)
    ok = _client(fake).post(_AUTO, json=body)
    assert ok.status_code == 200, ok.text
    assert ok.json()["dry_run"] is True
    assert "plan" in ok.json()
    assert fake.calls[0][1]["dry_run"] is True
    _client(fake).post(_AUTO, json={**body, "dry_run": False})
    assert fake.calls[1][1]["dry_run"] is False


def test_dry_run_with_idempotency_key_is_400_before_the_bridge() -> None:
    fake = DryBridge()
    body = _auth(type="sale_order", head={"a": "b"}, lines=[{"a": 1}], dry_run=True)
    denied = _client(fake).post("/v1/co/vouchers/create", json=body, headers={"Idempotency-Key": "order-1"})
    assert denied.status_code == 400
    error = denied.json()["error"]
    assert error["message"] == "预演不能带 Idempotency-Key"
    assert error["field"] == "dry_run"
    assert error["retryable"] is False
    assert fake.calls == []


def test_auto_writeoff_plan_with_idempotency_key_is_400_before_the_bridge() -> None:
    # arap/writeoff/auto 的实际核销可带键；只算计划（dry_run=true）带键拒绝，免得把计划存成结果。
    fake = FakeBridge()
    body = _auth(flag="AR", partner="C001", dry_run=True)
    denied = _client(fake).post(_AUTO, json=body, headers={"Idempotency-Key": "auto-1"})
    assert denied.status_code == 400
    assert denied.json()["error"]["message"] == "预演不能带 Idempotency-Key"
    assert denied.json()["error"]["field"] == "dry_run"
    assert fake.calls == []
    ok = _client(fake).post(_AUTO, json={**body, "dry_run": False}, headers={"Idempotency-Key": "auto-1"})
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["idempotency_key"] == "auto-1"
    assert fake.calls[0][1]["dry_run"] is False


def test_dry_run_must_be_a_boolean() -> None:
    fake = DryBridge()
    body = _auth(type="sale_order", id=1, dry_run="true")
    denied = _client(fake).post("/v1/co/vouchers/delete", json=body)
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "dry_run"
    assert fake.calls == []


def test_a_bridge_that_did_not_preview_is_outcome_unknown() -> None:
    # 桥没有按预演执行（回了正式写入的结果）：写入可能已生效，不能当成预演。
    fake = DryBridge(reply={"ok": True, "type": "sale_order", "id": 1, "code": "0000000001"})
    body = _auth(type="sale_order", head={"a": "b"}, lines=[{"a": 1}], dry_run=True)
    failed = _client(fake).post("/v1/co/vouchers/create", json=body)
    assert failed.status_code == 504
    assert failed.json()["error"]["code"] == "outcome_unknown"


def test_a_malformed_preview_is_bad_response() -> None:
    fake = DryBridge(reply={"ok": True, "dry_run": True, "docs": "not-a-list"})
    body = _auth(type="sale_order", id=1, dry_run=True)
    failed = _client(fake).post("/v1/co/vouchers/delete", json=body)
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "bad_response"


def test_validate_mode_preview_without_docs() -> None:
    reply = {"ok": True, "dry_run": True, "mode": "validate", "route": "gl/vouchers/create", "warnings": ["validate_only"]}
    fake = DryBridge(reply={**reply, "detail": {"stopped_before": "U8PzInsert"}})
    path, body = next(item for item in _DRY_WRITES if item[0] == "/v1/co/gl/vouchers/create")
    ok = _client(fake).post(path, json={**body, "dry_run": True})
    assert ok.status_code == 200, ok.text
    assert ok.json() == {**reply, "detail": {"stopped_before": "U8PzInsert"}}


def test_fields_and_compact_apply_to_preview_docs() -> None:
    fake = DryBridge()
    body = _auth(type="sale_order", id=1, action="close", dry_run=True)
    ok = _client(fake).post("/v1/co/vouchers/close?fields=docs.head.ccuscode,docs.lines.cinvcode", json=body)
    assert ok.status_code == 200, ok.text
    doc = ok.json()["docs"][0]
    assert doc["head"] == {"ccuscode": "C001"}
    assert doc["lines"] == [{"cinvcode": "A01"}]
    assert doc["code"] == "0000000123"
    compact = _client(fake).post("/v1/co/vouchers/close?compact=true", json=body)
    assert "cmemo" not in compact.json()["docs"][0]["head"]


def test_openapi_documents_the_preview_alternative() -> None:
    spec = _spec()
    schemas = spec["components"]["schemas"]
    assert "DryRunOut" in schemas
    assert "DryRunDoc" in schemas
    for route in POST_ROUTES:
        operation = spec["paths"][route.path]["post"]
        flagged = operation.get("x-u8co-dry-run") is True
        assert flagged == supports(route), route.path
        body = schemas[route.body_model.__name__]["properties"]
        if ACCESS[route.action] != WRITE:
            assert "dry_run" not in body, route.path
        if not flagged:
            continue
        assert "dry_run" in body, route.path
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        refs = [item.get("$ref") for item in schema["anyOf"]]
        assert "#/components/schemas/DryRunOut" in refs, route.path
    for path in (*_LEGACY, _AUTO):
        assert "x-u8co-dry-run" not in spec["paths"][path]["post"]
