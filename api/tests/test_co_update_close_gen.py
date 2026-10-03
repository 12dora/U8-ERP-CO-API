"""/v1/co update, close, and generate. The fake bridge is local."""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line as _audit_line
from tests.support import make_client, write_keys
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired

_HEAD = {"cMemo": "备注", "cWhCode": "01"}
_EDIT = [{"op": "update", "line_id": 7, "iQuantity": 1.5, "bGift": False}]
_GEN = [
    {"source_line_id": 1, "quantity": 0.5, "cWhCode": "02"},
    {"source_line_id": 2147483647, "quantity": 10**12, "cBatch": "B"},
]
_ONE = [{"source_line_id": 1, "quantity": 1}]
_LEAK = "draft-must-not-be-audited"
_LOAD = ("transfer", "sale_invoice", "purchase_invoice", "production_order")
_DELETE = ("purchase_order", "transfer", "dispatch", "arrival", "sale_out", "purchase_in", "sale_invoice")

_FORWARD = (
    (
        "/v1/co/vouchers/update",
        "/v1/vouchers/update",
        _auth(type="sale_order", id=4, head=_HEAD, lines=_EDIT),
        _sealed("type", "id", "head", "lines"),
    ),
    (
        "/v1/co/vouchers/update",
        "/v1/vouchers/update",
        _auth(type="transfer", id=4, head={"cMemo": "仅表头"}),
        _sealed("type", "id", "head"),
    ),
    (
        "/v1/co/vouchers/update",
        "/v1/vouchers/update",
        _auth(type="other_in", id=4, lines=[{"op": "add", "cInvCode": "A"}]),
        _sealed("type", "id", "lines"),
    ),
    (
        "/v1/co/vouchers/update",
        "/v1/vouchers/update",
        _auth(type="other_out", id=4, head={"cMemo": "a"}, lines=[]),
        _sealed("type", "id", "head", "lines"),
    ),
    (
        "/v1/co/vouchers/close",
        "/v1/vouchers/close",
        _auth(type="sale_order", id=4, action="close"),
        _sealed("type", "id", "action"),
    ),
    (
        "/v1/co/vouchers/close",
        "/v1/vouchers/close",
        _auth(type="purchase_order", id=4, action="open", line_ids=[3, 8]),
        _sealed("type", "id", "action", "line_ids"),
    ),
    (
        "/v1/co/vouchers/generate",
        "/v1/vouchers/generate",
        _auth(type="dispatch", id=9, head=_HEAD, lines=_GEN),
        _sealed("type", "id", "head", "lines"),
    ),
    (
        "/v1/co/vouchers/generate",
        "/v1/vouchers/generate",
        _auth(type="sale_out", id=9),
        _sealed("type", "id"),
    ),
    (
        "/v1/co/vouchers/generate",
        "/v1/vouchers/generate",
        _auth(type="purchase_in", id=9, lines=_ONE),
        _sealed("type", "id", "lines"),
    ),
    (
        "/v1/co/vouchers/generate",
        "/v1/vouchers/generate",
        _auth(type="sale_invoice", id=9, lines=_ONE),
        _sealed("type", "id", "lines"),
    ),
)

_GATED = (
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, head={"cMemo": "a"})),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close")),
    ("/v1/co/vouchers/generate", _auth(type="sale_out", id=1)),
)

_AUDIT = (
    (
        "/v1/co/vouchers/update",
        _auth(type="sale_order", id=11, head={"cMemo": _LEAK}, lines=[{"op": "update", "line_id": 3, "cBatch": _LEAK}]),
        "co:vouchers/update:update#11",
    ),
    (
        "/v1/co/vouchers/close",
        _auth(type="sale_order", id=11, action="open", line_ids=[77, 88]),
        "co:vouchers/close:open#11",
    ),
    (
        "/v1/co/vouchers/close",
        _auth(type="purchase_order", id=12, action="close"),
        "co:vouchers/close:close#12",
    ),
    (
        "/v1/co/vouchers/generate",
        _auth(
            type="dispatch",
            id=13,
            head={"cMemo": _LEAK},
            lines=[{"source_line_id": 1, "quantity": 1, "cBatch": _LEAK}],
        ),
        "co:vouchers/generate:generate#13",
    ),
)

_OPENAPI = {
    "/v1/co/vouchers/update": ("修改单据", "coVoucherUpdate", "单据新增删除"),
    "/v1/co/vouchers/close": ("关闭或打开单据", "coVoucherClose", "单据新增删除"),
    "/v1/co/vouchers/generate": ("参照生单", "coVoucherGenerate", "参照生单"),
}


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_update_forwards_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + bridge
    assert set(sent) == write_keys(path, keys)
    assert _SECRET.encode() not in raw
    assert b'"password"' not in raw


def test_draft_values_are_forwarded_unchanged() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        updated = client.post(
            "/v1/co/vouchers/update",
            json=_auth(type="sale_order", id=4, head=_HEAD, lines=_EDIT),
        )
        assert updated.status_code == 200, updated.text
        sent = json.loads(server.httpd.hits[-1][2])
        assert sent["head"] == _HEAD
        assert sent["lines"] == _EDIT
        generated = client.post(
            "/v1/co/vouchers/generate",
            json=_auth(type="dispatch", id=9, head=_HEAD, lines=_GEN),
        )
        assert generated.status_code == 200, generated.text
        raw = server.httpd.hits[-1][2]
        sent = json.loads(raw)
        assert sent["head"] == _HEAD
        assert sent["lines"] == _GEN
        assert _SECRET.encode() not in raw


@pytest.mark.parametrize(("path", "body"), _GATED)
def test_new_routes_are_404_when_disabled(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake, co_enabled=False)
    denied = client.post(path, json=body)
    assert denied.status_code == 404
    assert fake.calls == []


def test_update_is_503_before_the_bridge_when_unconfigured() -> None:
    fake = FakeBridge()
    client = _client(fake, co_configured=False)
    denied = client.post("/v1/co/vouchers/close", json=_auth(type="sale_order", id=1, action="open"))
    assert denied.status_code == 503
    assert denied.json()["error"]["code"] == "unavailable"
    assert fake.calls == []


def test_sale_out_generate_rejects_a_head() -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(
        "/v1/co/vouchers/generate",
        json=_auth(type="sale_out", id=9, head={"ddate": "2026-09-27"}),
    )
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_generate_rejects_an_account_outside_the_allowlist() -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post("/v1/co/vouchers/generate", json=_auth(type="sale_out", id=1, acc="001"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []


@pytest.mark.parametrize(("path", "body", "view"), _AUDIT)
def test_audit_names_the_verb_and_hides_the_draft(path: str, body: dict, view: str, capsys) -> None:
    client = _client()
    ok = client.post(path, json=body)
    assert ok.status_code == 200, ok.text
    err = capsys.readouterr().out
    assert _SECRET not in err
    assert _LEAK not in err
    assert _audit_line(err, path)["action"] == view


class _OrderBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        body = super().call(path, payload)
        if payload.get("type") != "production_order":
            return body
        state = dict(body["state"])
        state["closed"] = False
        state["note"] = "x"
        body["state"] = state
        body["allocations"] = [{"MoDId": "9"}]
        body["allocations_truncated"] = False
        body["mystery"] = "x"
        return body


def test_production_order_load_keeps_allocations_and_closed() -> None:
    client = _client(_OrderBridge())
    ok = client.post("/v1/co/vouchers/load", json=_auth(type="production_order", id=1))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["allocations"] == [{"MoDId": "9"}]
    assert body["allocations_truncated"] is False
    assert body["state"]["closed"] is False
    assert body["state"]["verified"] is False
    assert "note" not in body["state"]
    assert "mystery" not in body
    plain = client.post("/v1/co/vouchers/load", json=_auth(type="sale_order", id=1))
    assert plain.status_code == 200, plain.text
    assert "allocations" not in plain.json()
    assert "closed" not in plain.json()["state"]


@pytest.mark.parametrize("kind", _LOAD)
def test_load_accepts_update_kinds(kind: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post("/v1/co/vouchers/load", json=_auth(type=kind, id=1))
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][0] == "/v1/vouchers/load"
    assert fake.calls[0][1]["type"] == kind


@pytest.mark.parametrize("kind", ("transfer", "sale_invoice"))
def test_verify_accepts_transfer_and_sale_invoice(kind: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post("/v1/co/vouchers/verify", json=_auth(type=kind, id=1, action="verify"))
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][0] == "/v1/vouchers/verify"


@pytest.mark.parametrize("kind", ("purchase_order", "transfer"))
def test_create_accepts_purchase_order_and_transfer(kind: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post("/v1/co/vouchers/create", json=_auth(type=kind, head={"a": "b"}, lines=[{"a": 1}]))
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][0] == "/v1/vouchers/create"
    assert fake.calls[0][1]["type"] == kind


@pytest.mark.parametrize("kind", _DELETE)
def test_delete_accepts_update_kinds(kind: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post("/v1/co/vouchers/delete", json=_auth(type=kind, id=1))
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][0] == "/v1/vouchers/delete"


class _OptionalBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        body = super().call(path, payload)
        if path == "/v1/vouchers/create" and payload.get("type") == "purchase_order":
            body["lines"] = 3
        if path == "/v1/vouchers/verify" and payload.get("type") == "sale_invoice":
            body["ar_verifier"] = "应收员"
        return body


def test_create_lines_and_sale_invoice_ar_verifier_are_optional() -> None:
    client = _client(_OptionalBridge())
    created = client.post(
        "/v1/co/vouchers/create",
        json=_auth(type="purchase_order", head={"a": "b"}, lines=[{"a": 1}]),
    )
    assert created.status_code == 200, created.text
    assert created.json()["lines"] == 3
    plain = client.post(
        "/v1/co/vouchers/create",
        json=_auth(type="sale_order", head={"a": "b"}, lines=[{"a": 1}]),
    )
    assert plain.status_code == 200, plain.text
    assert "lines" not in plain.json()
    checked = client.post("/v1/co/vouchers/verify", json=_auth(type="sale_invoice", id=1, action="verify"))
    assert checked.status_code == 200, checked.text
    assert checked.json()["ar_verifier"] == "应收员"
    other = client.post("/v1/co/vouchers/verify", json=_auth(type="sale_order", id=1, action="verify"))
    assert other.status_code == 200, other.text
    assert "ar_verifier" not in other.json()


def test_transfer_verify_returns_generated_documents() -> None:
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post("/v1/co/vouchers/verify", json=_auth(type="transfer", id=5, action="verify"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["generated"] == [{"type": "other_out", "id": 8}, {"type": "other_in", "id": 9}]
    plain = client.post("/v1/co/vouchers/verify", json=_auth(type="transfer", id=5, action="unverify"))
    assert plain.status_code == 200, plain.text
    assert "generated" not in plain.json()


def test_close_and_generate_responses_keep_their_fields() -> None:
    fake = FakeBridge()
    client = _client(fake)
    closed = client.post("/v1/co/vouchers/close", json=_auth(type="sale_order", id=4, action="close", line_ids=[3, 4]))
    assert closed.status_code == 200, closed.text
    assert closed.json()["lines"] == [{"line_id": 3, "closed": True}, {"line_id": 4, "closed": True}]
    assert _SECRET not in closed.text
    made = client.post("/v1/co/vouchers/generate", json=_auth(type="purchase_in", id=6, lines=_ONE))
    assert made.status_code == 200, made.text
    body = made.json()
    assert body["source_type"] == "purchase_order"
    assert body["source_id"] == 6
    assert body["id"] == 42
    assert body["lines"] == 2


def test_update_openapi_paths_summaries_and_tags() -> None:
    spec = _spec()
    for path, (summary, operation_id, tag) in _OPENAPI.items():
        operation = spec["paths"][path]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert operation["tags"] == [tag]
    create = spec["paths"]["/v1/co/vouchers/create"]["post"]["description"]
    delete = spec["paths"]["/v1/co/vouchers/delete"]["post"]["description"]
    assert "采购订单" in create
    assert "调拨单" in create
    assert "发货单" in delete
    assert "采购入库单" in delete
    assert "销售发票" in delete
    gen = spec["paths"]["/v1/co/vouchers/generate"]["post"]["description"]
    assert "不能带表头" in gen
    gen_id = spec["components"]["schemas"]["CoGenerateIn"]["properties"]["id"]["description"]
    assert "销售发票来自发货单" in gen_id
    head = spec["components"]["schemas"]["CoGenerateIn"]["properties"]["head"]["description"]
    assert "销售出库不能带表头" in head
    assert "lines" in spec["components"]["schemas"]["CoCreateOut"]["properties"]
    verify = _enum(spec, "CoVoucherVerifyIn", "type")
    load = _enum(spec, "CoLoadIn", "type")
    assert "purchase_invoice" in verify  # 契约 B1：采购复核
    assert "production_order" in verify
    assert "transfer" in verify
    assert "sale_invoice" in verify
    assert "production_order" in load
    assert "purchase_invoice" in load
    assert "transfer" in _enum(spec, "CoCreateIn", "type")
    assert "dispatch" in _enum(spec, "CoDeleteIn", "type")
    props = spec["components"]["schemas"]["CoVoucherVerifyOut"]["properties"]
    assert "generated" in props
    assert "ar_verifier" in props


def _spec() -> dict:
    client = make_client(auth=False)
    ok = client.get("/v1/openapi.json", headers={"Authorization": f"Bearer {client.token}"})
    assert ok.status_code == 200
    return ok.json()


def _enum(spec: dict, schema: str, field: str) -> list[str]:
    prop = spec["components"]["schemas"][schema]["properties"][field]
    return _enums(spec, prop)


def _enums(spec: dict, node: object) -> list[str]:
    if not isinstance(node, dict):
        return []
    if "enum" in node:
        return [item for item in node["enum"] if isinstance(item, str)]
    ref = node.get("$ref")
    if isinstance(ref, str):
        name = ref.rsplit("/", 1)[-1]
        return _enums(spec, spec["components"]["schemas"].get(name, {}))
    return _joined(spec, node)


def _joined(spec: dict, node: dict) -> list[str]:
    found: list[str] = []
    for key in ("anyOf", "allOf", "oneOf"):
        for choice in node.get(key) or []:
            found.extend(_enums(spec, choice))
    return found
