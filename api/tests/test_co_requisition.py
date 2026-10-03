"""契约 B5：请购单（purchase_requisition）的读取、新增、修改、删除、审核、整单关闭在 API 层放行并原样转给桥。"""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards

_KIND = "purchase_requisition"
_CREATE = "/v1/co/vouchers/create"
_UPDATE = "/v1/co/vouchers/update"
_CLOSE = "/v1/co/vouchers/close"
_HEAD = {"dDate": "2026-09-28", "cDepCode": "D01", "cMemo": "请购", "cDefine1": "x"}
_LINES = [
    {"cInvCode": "A001", "fQuantity": 2, "dRequirDate": "2026-10-08", "cVenCode": "V001", "iOriCost": 10.5},
    {"cInvCode": "A002", "fQuantity": 1, "iOriTaxCost": 11.3, "iPerTaxRate": 13, "cbMemo": "备注", "cFree1": "红"},
]
_EDIT = [
    {"op": "update", "line_id": 7, "fQuantity": 3},
    {"op": "add", "cInvCode": "A003", "fQuantity": 1},
    {"op": "delete", "line_id": 8},
]


def _create(**extra) -> dict:
    body = {"type": _KIND, "head": _HEAD, "lines": _LINES}
    body.update(extra)
    return _auth(**body)


def _update(**extra) -> dict:
    body = {"type": _KIND, "id": 9, "head": {"cMemo": "改"}, "lines": _EDIT}
    body.update(extra)
    return _auth(**body)


_FORWARD = (
    (_CREATE, "/v1/vouchers/create", _create(), _sealed("type", "head", "lines")),
    (_UPDATE, "/v1/vouchers/update", _update(), _sealed("type", "id", "head", "lines")),
    ("/v1/co/vouchers/delete", "/v1/vouchers/delete", _auth(type=_KIND, id=9), _sealed("type", "id")),
    (
        "/v1/co/vouchers/verify",
        "/v1/vouchers/verify",
        _auth(type=_KIND, id=9, action="unverify"),
        _sealed("type", "id", "action"),
    ),
    ("/v1/co/vouchers/load", "/v1/vouchers/load", _auth(type=_KIND, id=9), _sealed("type", "id")),
)

_UPDATE_REJECTS = (
    _update(head={"cCode": "QG1"}),
    _update(head={"cVerifier": "x"}),
    _update(head={"cAuditDate": "2026-09-28"}),
    _update(head={"ID": 1}),
    _update(lines=[{"op": "update", "line_id": 7, "AutoID": 1}]),
    _update(lines=[{"op": "add", "line_id": 7, "cInvCode": "A"}]),
    _update(lines=[{"op": "delete", "line_id": 7, "fQuantity": 1}]),
    _auth(type=_KIND, id=9),
)


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_requisition_forwards_exact_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    _forwards(path, bridge, body, keys)


def test_requisition_create_reaches_the_bridge_unchanged() -> None:
    fake = FakeBridge()
    made = _client(fake).post(_CREATE, json=_create())
    assert made.status_code == 200, made.text
    sent = fake.calls[-1][1]
    assert (sent["type"], sent["head"], sent["lines"]) == (_KIND, _HEAD, _LINES)


def test_requisition_update_reaches_the_bridge_unchanged() -> None:
    fake = FakeBridge()
    done = _client(fake).post(_UPDATE, json=_update())
    assert done.status_code == 200, done.text
    sent = fake.calls[-1][1]
    assert (sent["type"], sent["id"], sent["lines"]) == (_KIND, 9, _EDIT)


@pytest.mark.parametrize("body", _UPDATE_REJECTS)
def test_requisition_update_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_UPDATE, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


@pytest.mark.parametrize("action", ["verify", "unverify"])
def test_requisition_verify_is_allowed(action: str) -> None:
    fake = FakeBridge()
    done = _client(fake).post("/v1/co/vouchers/verify", json=_auth(type=_KIND, id=9, action=action))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["type"] == _KIND


@pytest.mark.parametrize("action", ["close", "open"])
def test_requisition_close_whole_only(action: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    done = client.post(_CLOSE, json=_auth(type=_KIND, id=9, action=action))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["action"] == action
    assert "line_ids" not in fake.calls[-1][1]
    count = len(fake.calls)
    denied = client.post(_CLOSE, json=_auth(type=_KIND, id=9, action=action, line_ids=[1]))
    assert denied.status_code == 400, denied.text
    assert len(fake.calls) == count


def test_requisition_cannot_generate() -> None:
    fake = FakeBridge()
    denied = _client(fake).post(
        "/v1/co/vouchers/generate",
        json=_auth(type=_KIND, id=3, lines=[{"source_line_id": 1, "quantity": 1}]),
    )
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_openapi_lists_requisition() -> None:
    spec = make_client().get("/v1/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for model in ("CoLoadIn", "CoVoucherVerifyIn", "CoCreateIn", "CoDeleteIn", "CoUpdateIn", "CoCloseIn"):
        assert _KIND in schemas[model]["properties"]["type"]["enum"], model
    assert "请购单" in schemas["CoCreateIn"]["properties"]["type"]["description"]
