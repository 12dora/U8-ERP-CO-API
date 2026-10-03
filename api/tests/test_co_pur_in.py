"""契约 B6：无来源采购入库单（purchase_in）的新增、修改在 API 层放行并原样转给桥。"""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards

_CREATE = "/v1/co/vouchers/create"
_UPDATE = "/v1/co/vouchers/update"
_HEAD = {"cWhCode": "01", "cVenCode": "V001", "dDate": "2026-09-28", "cMemo": "无来源入库", "cDefine1": "x"}
_LINES = [
    {"cInvCode": "A001", "iQuantity": 2, "iUnitCost": 10.5, "iTaxRate": 13, "cPosition": "P0101", "cbMemo": "备注"},
    {"cInvCode": "A002", "iQuantity": 1, "iOriTaxCost": 11.3, "cBatch": "B1", "cFree1": "红"},
]
_EDIT = [
    {"op": "update", "line_id": 7, "iQuantity": 3},
    {"op": "add", "cInvCode": "A003", "iQuantity": 1, "iPrice": 20},
    {"op": "delete", "line_id": 8},
]


def _create(**extra) -> dict:
    body = {"type": "purchase_in", "head": _HEAD, "lines": _LINES}
    body.update(extra)
    return _auth(**body)


def _update(**extra) -> dict:
    body = {"type": "purchase_in", "id": 9, "head": {"cMemo": "改"}, "lines": _EDIT}
    body.update(extra)
    return _auth(**body)


_FORWARD = (
    (_CREATE, "/v1/vouchers/create", _create(), _sealed("type", "head", "lines")),
    (_UPDATE, "/v1/vouchers/update", _update(), _sealed("type", "id", "head", "lines")),
    (_UPDATE, "/v1/vouchers/update", _auth(type="purchase_in", id=9, lines=_EDIT), _sealed("type", "id", "lines")),
)

_UPDATE_REJECTS = (
    _update(head={"cCode": "RK1"}),
    _update(head={"cHandler": "x"}),
    _update(head={"ID": 1}),
    _update(lines=[{"op": "update", "line_id": 7, "AutoID": 1}]),
    _update(lines=[{"op": "add", "line_id": 7, "cInvCode": "A"}]),
    _update(lines=[{"op": "delete", "line_id": 7, "iQuantity": 1}]),
    _auth(type="purchase_in", id=9),
)


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_purchase_in_forwards_exact_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    _forwards(path, bridge, body, keys)


def test_purchase_in_create_reaches_the_bridge_unchanged() -> None:
    fake = FakeBridge()
    made = _client(fake).post(_CREATE, json=_create())
    assert made.status_code == 200, made.text
    sent = fake.calls[-1][1]
    assert sent["type"] == "purchase_in"
    assert sent["head"] == _HEAD
    assert sent["lines"] == _LINES


def test_purchase_in_update_reaches_the_bridge_unchanged() -> None:
    fake = FakeBridge()
    done = _client(fake).post(_UPDATE, json=_update())
    assert done.status_code == 200, done.text
    sent = fake.calls[-1][1]
    assert sent["type"] == "purchase_in"
    assert sent["lines"] == _EDIT


@pytest.mark.parametrize("body", _UPDATE_REJECTS)
def test_purchase_in_update_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_UPDATE, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_purchase_in_create_needs_lines() -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_CREATE, json=_create(lines=[]))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_generate_and_delete_of_purchase_in_still_allowed() -> None:
    fake = FakeBridge()
    client = _client(fake)
    gen = client.post(
        "/v1/co/vouchers/generate",
        json=_auth(type="purchase_in", id=3, head={"cwhcode": "01"}, lines=[{"source_line_id": 1, "quantity": 1}]),
    )
    assert gen.status_code == 200, gen.text
    gone = client.post("/v1/co/vouchers/delete", json=_auth(type="purchase_in", id=3))
    assert gone.status_code == 200, gone.text


def test_openapi_lists_purchase_in_for_create_and_update() -> None:
    spec = make_client().get("/v1/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert "purchase_in" in schemas["CoCreateIn"]["properties"]["type"]["enum"]
    assert "purchase_in" in schemas["CoUpdateIn"]["properties"]["type"]["enum"]
    assert "采购入库单（无来源）" in schemas["CoCreateIn"]["properties"]["type"]["description"]
