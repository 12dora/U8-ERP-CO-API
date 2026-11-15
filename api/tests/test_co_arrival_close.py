"""到货单关闭 / 打开（vouchers/close，type arrival）在 API 层放行并原样转给桥，响应同采购订单关闭。"""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.errors import ApiError

_KIND = "arrival"
_CLOSE = "/v1/co/vouchers/close"


@pytest.mark.parametrize(
    ("body", "keys"),
    [
        (_auth(type=_KIND, id=9, action="close"), _sealed("type", "id", "action")),
        (_auth(type=_KIND, id=9, action="open", line_ids=[31, 32]), _sealed("type", "id", "action", "line_ids")),
    ],
)
def test_arrival_close_forwards_exact_keys(body: dict, keys: set[str]) -> None:
    _forwards(_CLOSE, "/v1/vouchers/close", body, keys)


@pytest.mark.parametrize("action", ["close", "open"])
def test_arrival_close_returns_the_po_shape(action: str) -> None:
    fake = FakeBridge()
    done = _client(fake).post(_CLOSE, json=_auth(type=_KIND, id=9, action=action))
    assert done.status_code == 200, done.text
    body = done.json()
    assert fake.calls[-1][1]["type"] == _KIND
    assert "line_ids" not in fake.calls[-1][1]
    assert (body["type"], body["id"], body["action"], body["closed"]) == (_KIND, 9, action, action == "close")
    assert body["lines"] == [{"line_id": 1, "closed": action == "close"}]
    assert not {"code", "changed", "state"} & set(body)


@pytest.mark.parametrize(
    "body",
    [
        _auth(type=_KIND, id=9, action="close", line_ids=[]),
        _auth(type=_KIND, id=9, action="close", line_ids=[1, 1]),
        _auth(type=_KIND, id=9, action="close", line_ids=None),
        _auth(type=_KIND, id=9, action="close", line_ids=[True]),
        _auth(type=_KIND, id=9, action="verify"),
        _auth(type=_KIND, id=0, action="close"),
    ],
)
def test_arrival_close_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_CLOSE, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (409, "state_mismatch", "单据已关闭"),
        (409, "state_mismatch", "单据未审核"),
        (400, "bad_request", "仅支持蓝字到货单"),
        (409, "u8_rejected", "到货单已全部入库"),
        (504, "outcome_unknown", "到货单已关闭，但回读失败，标识 9"),
    ],
)
def test_arrival_close_passes_bridge_errors(status: int, code: str, message: str) -> None:
    fake = FakeBridge()
    fake.error = ApiError(status, code, message)
    denied = _client(fake).post(_CLOSE, json=_auth(type=_KIND, id=9, action="close"))
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_purchase_return_close_is_still_refused() -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_CLOSE, json=_auth(type="purchase_return", id=9, action="close"))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_openapi_lists_arrival_close() -> None:
    schemas = make_client().get("/v1/openapi.json").json()["components"]["schemas"]
    assert _KIND in schemas["CoCloseIn"]["properties"]["type"]["enum"]
    assert "到货单" in schemas["CoCloseIn"]["properties"]["type"]["description"]
