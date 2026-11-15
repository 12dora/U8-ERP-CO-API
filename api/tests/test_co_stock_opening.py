"""期初结存单（stock_opening，U8 单据类型 34）的读取、列表、新增、删除、审核在 API 层放行并原样转给桥；
不能修改（API 层 400，与桥同文）、关闭、生单。测试账套闸门、日期规则和存货核算期初记账的拦截都在桥里，这里只看转发；
新增响应里每行一张单据（docs）原样透出。"""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.co_models_edit import CoUpdateIn

_KIND = "stock_opening"
_CREATE = "/v1/co/vouchers/create"
_UPDATE = "/v1/co/vouchers/update"
_HEAD = {"cWhCode": "01", "cRdCode": "101", "cMemo": "期初"}
_LINES = [{"cInvCode": "A001", "iQuantity": 5, "iUnitCost": 2.5, "cBatch": "B1", "cPosition": "P01"}]
_EDIT = [
    {"op": "update", "line_id": 7, "iQuantity": 3},
    {"op": "add", "cInvCode": "A002", "iQuantity": 1, "iUnitCost": 4},
    {"op": "delete", "line_id": 8},
]


def _update(**extra) -> dict:
    body = {"type": _KIND, "id": 9, "head": {"cMemo": "改"}, "lines": _EDIT}
    body.update(extra)
    return _auth(**body)


_FORWARD = (
    (_CREATE, "/v1/vouchers/create", _auth(type=_KIND, head=_HEAD, lines=_LINES), _sealed("type", "head", "lines")),
    ("/v1/co/vouchers/delete", "/v1/vouchers/delete", _auth(type=_KIND, id=9), _sealed("type", "id")),
    ("/v1/co/vouchers/load", "/v1/vouchers/load", _auth(type=_KIND, id=9), _sealed("type", "id")),
    ("/v1/co/vouchers/list", "/v1/vouchers/list", _auth(type=_KIND), _sealed("type")),
    (
        "/v1/co/vouchers/verify",
        "/v1/vouchers/verify",
        _auth(type=_KIND, id=9, action="unverify"),
        _sealed("type", "id", "action"),
    ),
)
_UPDATE_REJECTS = (
    _update(),
    _update(head={"cCode": "X1"}),
    _update(lines=[{"op": "add", "line_id": 7, "cInvCode": "A"}]),
    _auth(type=_KIND, id=9),
    _update(dry_run=True),
)
_DOCS = [
    {"id": 41, "code": "0000000041", "line": 0, "wh": "01", "inv": "A001", "qty": 5},
    {"id": 42, "code": "0000000042", "line": 1, "wh": "02", "inv": "A002", "qty": 1.5},
]


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_stock_opening_forwards_exact_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    _forwards(path, bridge, body, keys)


def test_stock_opening_create_reaches_the_bridge_unchanged() -> None:
    fake = FakeBridge()
    made = _client(fake).post(_CREATE, json=_auth(type=_KIND, head=_HEAD, lines=_LINES))
    assert made.status_code == 200, made.text
    sent = fake.calls[-1][1]
    assert (sent["type"], sent["head"], sent["lines"]) == (_KIND, _HEAD, _LINES)


class _DocsBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        body = {"ok": True, "type": _KIND, "id": 41, "code": "0000000041", "docs": _DOCS, "count": 2}
        body.update({"date": "2025-12-31", "warnings": ["期初结存单日期固定为库存启用日前一天（2025-12-31）"]})
        return body


def test_stock_opening_create_passes_docs_through() -> None:
    lines = _LINES + [{"cWhCode": "02", "cInvCode": "A002", "iQuantity": 1.5}]
    made = _client(_DocsBridge()).post(_CREATE, json=_auth(type=_KIND, head=_HEAD, lines=lines))
    assert made.status_code == 200, made.text
    body = made.json()
    assert (body["id"], body["code"], body["count"], body["date"]) == (41, "0000000041", 2, "2025-12-31")
    assert body["docs"] == _DOCS
    assert body["warnings"] == ["期初结存单日期固定为库存启用日前一天（2025-12-31）"]


@pytest.mark.parametrize("body", _UPDATE_REJECTS)
def test_stock_opening_update_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_UPDATE, json=body)
    assert denied.status_code == 400, denied.text
    error = denied.json()["error"]
    assert (error["code"], error["message"]) == ("bad_request", "期初结存单不能修改，请删除后重新录入"), error
    assert fake.calls == []


def test_update_type_no_longer_lists_stock_opening() -> None:
    assert _KIND not in CoUpdateIn.model_json_schema()["properties"]["type"]["enum"]
    assert "期初结存单" not in CoUpdateIn.model_json_schema()["properties"]["type"]["description"]


def test_stock_opening_cannot_close_or_generate() -> None:
    fake = FakeBridge()
    client = _client(fake)
    closed = client.post("/v1/co/vouchers/close", json=_auth(type=_KIND, id=9, action="close"))
    assert closed.status_code == 400, closed.text
    made = client.post(
        "/v1/co/vouchers/generate",
        json=_auth(type=_KIND, id=3, lines=[{"source_line_id": 1, "quantity": 1}]),
    )
    assert made.status_code == 400, made.text
    assert fake.calls == []


def test_openapi_lists_stock_opening() -> None:
    schemas = make_client().get("/v1/openapi.json").json()["components"]["schemas"]
    for model in ("CoLoadIn", "CoCreateIn", "CoDeleteIn", "CoVoucherVerifyIn", "CoVoucherListIn"):
        assert _KIND in schemas[model]["properties"]["type"]["enum"], model
    assert _KIND not in schemas["CoUpdateIn"]["properties"]["type"]["enum"]
    assert "期初结存单" in schemas["CoCreateIn"]["properties"]["type"]["description"]
    assert {"docs", "count", "date"} <= set(schemas["CoCreateOut"]["properties"])
