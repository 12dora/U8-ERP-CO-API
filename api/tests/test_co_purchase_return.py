"""契约 B3：采购退货单（purchase_return，红字到货单）的读取、审核、删除、列表与参照生单。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_update_close_gen import _enum, _spec
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards

_GEN = "/v1/co/vouchers/generate"
_LINES = [{"source_line_id": 11, "quantity": 2}, {"source_line_id": 12, "quantity": 0.5}]
_HEAD = {"cWhCode": "01", "dDate": "2026-09-28", "cMemo": "退货", "cDepCode": "D01"}


def _ret(head: dict | None = None, lines: list | None = None, **extra) -> dict:
    body = {"type": "purchase_return", "id": 7, "lines": _LINES if lines is None else lines}
    if head is not None:
        body["head"] = head
    body.update(extra)
    return _auth(**body)


_FORWARD = (
    (_GEN, "/v1/vouchers/generate", _ret(), _sealed("type", "id", "lines")),
    (
        _GEN,
        "/v1/vouchers/generate",
        _ret(_HEAD, source_type="arrival"),
        _sealed("type", "id", "head", "lines", "source_type"),
    ),
    (_GEN, "/v1/vouchers/generate", _ret(source_type="purchase_order"), _sealed("type", "id", "lines", "source_type")),
    ("/v1/co/vouchers/load", "/v1/vouchers/load", _auth(type="purchase_return", id=7), _sealed("type", "id")),
    ("/v1/co/vouchers/delete", "/v1/vouchers/delete", _auth(type="purchase_return", id=7), _sealed("type", "id")),
    (
        "/v1/co/vouchers/verify",
        "/v1/vouchers/verify",
        _auth(type="purchase_return", id=7, action="verify"),
        _sealed("type", "id", "action"),
    ),
    (
        "/v1/co/vouchers/verify",
        "/v1/vouchers/verify",
        _auth(type="purchase_return", id=7, action="unverify"),
        _sealed("type", "id", "action"),
    ),
)

_ACCEPTS = (
    _ret(),
    _ret(_HEAD),
    _ret({"cwhcode": "01"}, lines=[{"source_line_id": 3, "quantity": 1}]),
    _ret({}, source_type="arrival"),
    _ret(source_type="purchase_order"),
)

_REJECTS = (
    _ret(lines=[]),
    _auth(type="purchase_return", id=7),
    _ret(source_type="purchase_in"),
    _ret(source_type="sale_order"),
    _ret({"cCode": "DH1"}),
    _ret({"cVenCode": "V1"}),
    _ret({"cWhCode": 1}),
    _ret({"dDate": "2026/09/28"}),
    _ret({"cWhCode": "01", "cwhcode": "02"}),
    _ret(lines=[{"source_line_id": 1, "quantity": 1, "cBatch": "B"}]),
    _ret(lines=[{"source_line_id": 1, "quantity": -1}]),
    _ret(lines=[{"source_line_id": 1, "quantity": 0}]),
    _ret(lines=[{"source_line_id": i, "quantity": 1} for i in range(1, 202)]),
)


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_purchase_return_forwards_exact_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    _forwards(path, bridge, body, keys)


@pytest.mark.parametrize("body", _ACCEPTS)
def test_purchase_return_generate_accepts(body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_GEN, json=body)
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["type"] == "purchase_return"


@pytest.mark.parametrize("body", _REJECTS)
def test_purchase_return_generate_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_purchase_return_generate_defaults_to_the_arrival() -> None:
    fake = FakeBridge()
    made = _client(fake).post(_GEN, json=_ret(_HEAD))
    assert made.status_code == 200, made.text
    sent = fake.calls[-1][1]
    assert sent["head"] == _HEAD
    assert sent["lines"] == _LINES
    assert "source_type" not in sent
    assert made.json()["source_type"] == "arrival"
    assert made.json()["source_id"] == 7


@pytest.mark.parametrize("action", ["verify", "unverify"])
def test_purchase_return_verify_is_allowed(action: str) -> None:
    fake = FakeBridge()
    ok = _client(fake).post("/v1/co/vouchers/verify", json=_auth(type="purchase_return", id=7, action=action))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["type"] == "purchase_return"


def test_purchase_return_delete_and_load_are_allowed() -> None:
    fake = FakeBridge()
    client = _client(fake)
    gone = client.post("/v1/co/vouchers/delete", json=_auth(type="purchase_return", id=7))
    assert gone.status_code == 200, gone.text
    assert gone.json()["deleted"] is True
    loaded = client.post("/v1/co/vouchers/load", json=_auth(type="purchase_return", id=7))
    assert loaded.status_code == 200, loaded.text


def test_purchase_return_cannot_be_created_or_closed() -> None:
    fake = FakeBridge()
    client = _client(fake)
    for path, extra in (
        ("/v1/co/vouchers/create", {"head": {"cMemo": "x"}, "lines": [{"cInvCode": "A", "iQuantity": 1}]}),
        ("/v1/co/vouchers/close", {"id": 7, "action": "close"}),
    ):
        denied = client.post(path, json=_auth(type="purchase_return", **extra))
        assert denied.status_code == 400, (path, denied.text)
    assert fake.calls == []


def test_purchase_return_is_in_the_openapi_enums() -> None:
    spec = _spec()
    for schema in ("CoLoadIn", "CoVoucherVerifyIn", "CoDeleteIn", "CoGenerateIn", "CoVoucherListIn"):
        assert "purchase_return" in _enum(spec, schema, "type"), schema
    assert "purchase_return" not in _enum(spec, "CoCreateIn", "type")
    assert "arrival" in _enum(spec, "CoGenerateIn", "source_type")
    source = spec["components"]["schemas"]["CoGenerateIn"]["properties"]["source_type"]["description"]
    assert "采购退货单" in source
