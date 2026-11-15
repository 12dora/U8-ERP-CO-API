"""Production order close / open: forwarding, the MO-only response fields and bridge errors."""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.errors import ApiError

_KIND = "production_order"
_CLOSE = "/v1/co/vouchers/close"


def _mo_closed(payload: dict) -> dict:
    shut = payload["action"] == "close"
    ids = payload.get("line_ids") or [11, 12]
    return {
        "ok": True,
        "type": payload["type"],
        "id": payload["id"],
        "code": "MO0001",
        "action": payload["action"],
        "closed": shut,
        "closed_by": "张三" if shut else "",
        "closed_at": "2026-09-28" if shut else "",
        "changed": len(ids),
        "lines": [{"line_id": item, "closed": shut} for item in ids],
        "state": {"verified": not shut, "closed": shut, "verifier": "张三", "verified_at": "2026-09-27"},
    }


class _MoBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        return _mo_closed(payload)


@pytest.mark.parametrize(
    ("body", "keys"),
    [
        (_auth(type=_KIND, id=9, action="close"), _sealed("type", "id", "action")),
        (_auth(type=_KIND, id=9, action="open", line_ids=[11, 12]), _sealed("type", "id", "action", "line_ids")),
    ],
)
def test_mo_close_forwards_exact_keys(body: dict, keys: set[str]) -> None:
    _forwards(_CLOSE, "/v1/vouchers/close", body, keys)


@pytest.mark.parametrize("action", ["close", "open"])
def test_mo_close_keeps_code_changed_and_state(action: str) -> None:
    fake = _MoBridge()
    done = _client(fake).post(_CLOSE, json=_auth(type=_KIND, id=9, action=action, line_ids=[11]))
    assert done.status_code == 200, done.text
    body = done.json()
    assert fake.calls[-1][1]["line_ids"] == [11]
    assert (body["code"], body["changed"], body["lines"]) == ("MO0001", 1, [{"line_id": 11, "closed": action == "close"}])
    assert body["state"]["closed"] is (action == "close")
    assert body["state"]["verified"] is (action == "open")


def test_sale_order_close_has_no_mo_fields() -> None:
    done = _client(FakeBridge()).post(_CLOSE, json=_auth(type="sale_order", id=4, action="close"))
    assert done.status_code == 200, done.text
    assert not {"code", "changed", "state"} & set(done.json())


@pytest.mark.parametrize(
    "body",
    [
        _auth(type=_KIND, id=9, action="close", line_ids=[]),
        _auth(type=_KIND, id=9, action="close", line_ids=[1, 1]),
        _auth(type=_KIND, id=9, action="close", line_ids=None),
        _auth(type=_KIND, id=9, action="verify"),
        _auth(type=_KIND, id=0, action="close"),
    ],
)
def test_mo_close_rejects_before_the_bridge(body: dict) -> None:
    fake = _MoBridge()
    denied = _client(fake).post(_CLOSE, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (409, "u8_rejected", "本月成本已计算，不能关闭"),
        (409, "u8_rejected", "生产订单还有在制，不能关闭"),
        (409, "state_mismatch", "单据已关闭"),
        (409, "workflow_enabled", "单据已启用审批流，本期不支持"),
        (403, "no_permission", "没有生产订单关闭权限"),
    ],
)
def test_mo_close_passes_bridge_errors(status: int, code: str, message: str) -> None:
    fake = _MoBridge()
    fake.error = ApiError(status, code, message)
    denied = _client(fake).post(_CLOSE, json=_auth(type=_KIND, id=9, action="close"))
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_openapi_lists_production_order_close() -> None:
    schemas = make_client().get("/v1/openapi.json").json()["components"]["schemas"]
    assert _KIND in schemas["CoCloseIn"]["properties"]["type"]["enum"]
    assert "生产订单" in schemas["CoCloseIn"]["properties"]["type"]["description"]
    assert {"code", "changed", "state"} <= set(schemas["CoCloseOut"]["properties"])
