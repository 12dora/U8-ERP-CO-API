"""形态转换单、调拨申请单、盘点单的读取、新增、修改、删除、审核在 API 层放行并原样转给桥；盘点单不开修改，三类都不能关闭、生单。"""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards

_KINDS = ("shape_change", "transfer_request", "stock_check")
_CREATE = "/v1/co/vouchers/create"
_UPDATE = "/v1/co/vouchers/update"
_SHAPE_HEAD = {"dAVDate": "2026-09-28", "cDepCode": "D01", "cAVMemo": "形态转换"}
_SHAPE_LINES = [
    {"cInvCode": "A001", "cWhCode": "01", "bAVType": "转换前", "iGroupNO": 1, "iAVQuantity": 2},
    {"cInvCode": "A002", "cWhCode": "01", "bAVType": "转换后", "iGroupNO": 1, "iAVQuantity": 2},
]
_REQUEST_HEAD = {"dTVDate": "2026-09-28", "cOWhCode": "01", "cIWhCode": "02", "cTVMemo": "申请"}
_REQUEST_LINES = [{"cInvCode": "A001", "iTVQuantity": 3, "cFree1": "红"}]
_CHECK_HEAD = {"cWhCode": "01", "dCVDate": "2026-09-28"}
_CHECK_LINES = [{"cInvCode": "A001", "iCVCQuantity": 0}, {"cInvCode": "A002", "iCVCQuantity": 5, "iCVQuantity": 4}]
_BODIES = {
    "shape_change": (_SHAPE_HEAD, _SHAPE_LINES),
    "transfer_request": (_REQUEST_HEAD, _REQUEST_LINES),
    "stock_check": (_CHECK_HEAD, _CHECK_LINES),
}
_EDIT = [
    {"op": "update", "line_id": 7, "iAVQuantity": 3},
    {"op": "add", "cInvCode": "A003", "cWhCode": "01", "bAVType": "转换后", "iAVQuantity": 1},
    {"op": "delete", "line_id": 8},
]


def _create(kind: str) -> dict:
    head, lines = _BODIES[kind]
    return _auth(type=kind, head=head, lines=lines)


def _update(kind: str = "shape_change", **extra) -> dict:
    body = {"type": kind, "id": 9, "head": {"cAVMemo": "改"}, "lines": _EDIT}
    body.update(extra)
    return _auth(**body)


def _forward_cases() -> list[tuple[str, str, dict, set[str]]]:
    cases = []
    for kind in _KINDS:
        cases.append((_CREATE, "/v1/vouchers/create", _create(kind), _sealed("type", "head", "lines")))
        cases.append(("/v1/co/vouchers/delete", "/v1/vouchers/delete", _auth(type=kind, id=9), _sealed("type", "id")))
        cases.append(("/v1/co/vouchers/load", "/v1/vouchers/load", _auth(type=kind, id=9), _sealed("type", "id")))
        if kind != "stock_check":
            verify = _auth(type=kind, id=9, action="verify")
            cases.append(("/v1/co/vouchers/verify", "/v1/vouchers/verify", verify, _sealed("type", "id", "action")))
    cases.append((_UPDATE, "/v1/vouchers/update", _update(), _sealed("type", "id", "head", "lines")))
    return cases


_UPDATE_REJECTS = (
    _update(head={"cAVCode": "X1"}),
    _update(head={"cVerifyPerson": "x"}),
    _update(head={"dVerifyDate": "2026-09-28"}),
    _update(head={"ID": 1}),
    _update("transfer_request", head={"cTVCode": "X1"}),
    _update(lines=[{"op": "update", "line_id": 7, "autoID": 1}]),
    _update(lines=[{"op": "add", "line_id": 7, "cInvCode": "A"}]),
    _update(lines=[{"op": "delete", "line_id": 7, "iAVQuantity": 1}]),
    _update("stock_check", head={"cCVMemo": "改"}, lines=None),
    _auth(type="shape_change", id=9),
)


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _forward_cases())
def test_stmisc_forwards_exact_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    _forwards(path, bridge, body, keys)


@pytest.mark.parametrize("kind", _KINDS)
def test_stmisc_create_reaches_the_bridge_unchanged(kind: str) -> None:
    fake = FakeBridge()
    made = _client(fake).post(_CREATE, json=_create(kind))
    assert made.status_code == 200, made.text
    sent = fake.calls[-1][1]
    assert (sent["type"], sent["head"], sent["lines"]) == (kind, *_BODIES[kind])


def test_stmisc_update_reaches_the_bridge_unchanged() -> None:
    fake = FakeBridge()
    done = _client(fake).post(_UPDATE, json=_update("transfer_request", head={"cTVMemo": "改"}))
    assert done.status_code == 200, done.text
    sent = fake.calls[-1][1]
    assert (sent["type"], sent["id"], sent["lines"]) == ("transfer_request", 9, _EDIT)


@pytest.mark.parametrize("body", _UPDATE_REJECTS)
def test_stmisc_update_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_UPDATE, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


@pytest.mark.parametrize("kind", ("shape_change", "transfer_request"))
@pytest.mark.parametrize("action", ["verify", "unverify"])
def test_stmisc_verify_is_allowed(kind: str, action: str) -> None:
    fake = FakeBridge()
    done = _client(fake).post("/v1/co/vouchers/verify", json=_auth(type=kind, id=9, action=action))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["type"] == kind


@pytest.mark.parametrize("action", ["verify", "unverify"])
def test_stock_check_verify_refused_before_the_bridge(action: str) -> None:
    # 实测 U8 生成盘盈盘亏单时报「类型不匹配」，盘点单审核、弃审一律不开。
    fake = FakeBridge()
    denied = _client(fake).post("/v1/co/vouchers/verify", json=_auth(type="stock_check", id=9, action=action))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


@pytest.mark.parametrize("kind", _KINDS)
def test_stmisc_cannot_close_or_generate(kind: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    closed = client.post("/v1/co/vouchers/close", json=_auth(type=kind, id=9, action="close"))
    assert closed.status_code == 400, closed.text
    made = client.post(
        "/v1/co/vouchers/generate",
        json=_auth(type=kind, id=3, lines=[{"source_line_id": 1, "quantity": 1}]),
    )
    assert made.status_code == 400, made.text
    assert fake.calls == []


def test_openapi_lists_stmisc() -> None:
    spec = make_client().get("/v1/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for kind in _KINDS:
        for model in ("CoLoadIn", "CoCreateIn", "CoDeleteIn"):
            assert kind in schemas[model]["properties"]["type"]["enum"], (kind, model)
    verify = schemas["CoVoucherVerifyIn"]["properties"]["type"]["enum"]
    assert "shape_change" in verify and "transfer_request" in verify and "stock_check" not in verify
    update = schemas["CoUpdateIn"]["properties"]["type"]["enum"]
    assert "shape_change" in update and "transfer_request" in update and "stock_check" not in update
    assert "形态转换单" in schemas["CoCreateIn"]["properties"]["type"]["description"]


_QTY_REJECTS = (
    _auth(type="shape_change", head=_SHAPE_HEAD, lines=[dict(_SHAPE_LINES[0], iAVQuantity=0.1234567), _SHAPE_LINES[1]]),
    _auth(type="shape_change", head=_SHAPE_HEAD, lines=[dict(_SHAPE_LINES[0], iAVQuantity=0), _SHAPE_LINES[1]]),
    _auth(type="transfer_request", head=_REQUEST_HEAD, lines=[{"cInvCode": "A001", "iTVQuantity": "abc"}]),
    _auth(type="stock_check", head=_CHECK_HEAD, lines=[{"cInvCode": "A001", "iCVCQuantity": -1}]),
    _auth(type="stock_check", head=_CHECK_HEAD, lines=[{"cInvCode": "A001", "iCVCQuantity": 1, "iCVQuantity": 1e13}]),
    _auth(type="stock_check", head=_CHECK_HEAD, lines=[{"cInvCode": "A001", "icvcquantity": True}]),
)


@pytest.mark.parametrize("body", _QTY_REJECTS)
def test_stmisc_create_quantity_rules_match_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_CREATE, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_stmisc_update_quantity_rules_match_the_bridge() -> None:
    fake = FakeBridge()
    client = _client(fake)
    lines = [{"op": "update", "line_id": 7, "iTVQuantity": 1.0000001}]
    denied = client.post(_UPDATE, json=_update("transfer_request", head=None, lines=lines))
    assert denied.status_code == 400, denied.text
    ok = client.post(_UPDATE, json=_update("transfer_request", head=None, lines=[{"op": "update", "line_id": 7, "iTVQuantity": 1.123456}]))
    assert ok.status_code == 200, ok.text
