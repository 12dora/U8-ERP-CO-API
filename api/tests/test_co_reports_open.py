"""期初余额 /v1/co/reports/opening_balance，凭证附件 /v1/co/gl/vouchers/attachments/list，
单据附件 /v1/co/vouchers/attachments/list：转发的字段、提前拒绝、权限和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from tests.support import base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec
from tests.test_co_gl_arc import _KEY
from u8co_api.co_access import ACCESS, READ

_OPEN = "/v1/co/reports/opening_balance"
_GL_ATT = "/v1/co/gl/vouchers/attachments/list"
_ATT = "/v1/co/vouchers/attachments/list"

_FORWARD = (
    (_OPEN, "/v1/reports/opening_balance", _auth(module="stock"), _sealed("module")),
    (
        _OPEN,
        "/v1/reports/opening_balance",
        _auth(module="stock", wh="07", inv="INV0008", batch="B1", nonzero=False, after="eA", limit=1000),
        _sealed("module", "wh", "inv", "batch", "nonzero", "after", "limit"),
    ),
    (
        _OPEN,
        "/v1/reports/opening_balance",
        _auth(module="arap", side="ap", partner="V001", code_prefix="2202"),
        _sealed("module", "side", "partner", "code_prefix"),
    ),
    (
        _OPEN,
        "/v1/reports/opening_balance",
        _auth(module="gl", fiscal_year=2024, code_prefix="1122", leaf_only=True, dim="customer"),
        _sealed("module", "fiscal_year", "code_prefix", "leaf_only", "dim"),
    ),
    (_GL_ATT, "/v1/gl/vouchers/attachments/list", _auth(**_KEY), _sealed("period", "sign", "no")),
    (_ATT, "/v1/vouchers/attachments/list", _auth(type="purchase_order", id=7), _sealed("type", "id")),
)

_REJECTS = (
    (_OPEN, _auth()),
    (_OPEN, _auth(module="ia")),
    (_OPEN, _auth(module="arap")),
    (_OPEN, _auth(module="arap", side="xx")),
    (_OPEN, _auth(module="stock", side="ar")),
    (_OPEN, _auth(module="stock", fiscal_year=2024)),
    (_OPEN, _auth(module="stock", code_prefix="1122")),
    (_OPEN, _auth(module="arap", side="ar", wh="01")),
    (_OPEN, _auth(module="arap", side="ar", dim="customer")),
    (_OPEN, _auth(module="gl", partner="C001")),
    (_OPEN, _auth(module="gl", dim="item")),
    (_OPEN, _auth(module="gl", code_prefix="11%")),
    (_OPEN, _auth(module="gl", fiscal_year=1999)),
    (_OPEN, _auth(module="stock", limit=0)),
    (_OPEN, _auth(module="stock", nonzero="yes")),
    (_OPEN, _auth(module="stock", wh=" ")),
    (_GL_ATT, _auth(period=13, sign="转", no=1)),
    (_GL_ATT, _auth(period=9, sign="转")),
    (_ATT, _auth(type="sale_order")),
    (_ATT, _auth(type="nope", id=1)),
    (_ATT, _auth(type="sale_order", id=0)),
)


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_opening_and_attachments_forward_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + bridge
    assert set(sent) == keys
    assert _SECRET.encode() not in raw


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_opening_and_attachments_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _OpenBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        if path == "/v1/reports/opening_balance":
            trial = {"debit": 1.0, "credit": 1.0, "difference": 0.0, "balanced": True}
            row = {"code": "1001", "open_dir": "借", "open_debit": 1.0, "x": 1}
            return {"ok": True, "module": "gl", "posted": True, "start_date": "2024-12-01", "trial": trial, "items": [row]}
        if path == "/v1/gl/vouchers/attachments/list":
            voucher = {"period": 9, "sign": "转", "no": 3, "attachments": 2}
            return {"ok": True, "voucher": voucher, "items": [], "truncated": False}
        row = {"card": "88", "file_id": "F1", "name": "a.pdf", "size": None, "stored": "file_server"}
        return {"ok": True, "type": "purchase_order", "id": 7, "items": [row], "truncated": False}


def test_opening_and_attachment_responses_keep_bridge_fields() -> None:
    client = _client(_OpenBridge())
    opening = client.post(_OPEN, json=_auth(module="gl")).json()
    assert opening["trial"]["balanced"] is True
    assert opening["posted"] is True
    assert opening["items"][0]["x"] == 1
    gl = client.post(_GL_ATT, json=_auth(**_KEY)).json()
    assert gl["voucher"]["attachments"] == 2
    assert gl["items"] == []
    files = client.post(_ATT, json=_auth(type="purchase_order", id=7)).json()
    assert files["items"][0]["stored"] == "file_server"


@pytest.mark.parametrize(
    ("path", "body", "bridge"),
    (
        (_OPEN, _auth(module="arap", side="ar"), "/v1/reports/opening_balance"),
        (_GL_ATT, _auth(**_KEY), "/v1/gl/vouchers/attachments/list"),
        (_ATT, _auth(type="sale_order", id=1), "/v1/vouchers/attachments/list"),
    ),
)
def test_opening_and_attachments_are_read_only_and_gated(path: str, body: dict, bridge: str) -> None:
    assert ACCESS["co:" + bridge.removeprefix("/v1/")] == READ
    fake = FakeBridge()
    assert _client(fake, co_enabled=False).post(path, json=body).status_code == 404
    assert _client(fake, claims=base_claims()).post(path, json=body).status_code == 403
    outside = _client(fake).post(path, json=dict(body, acc="001"))
    assert outside.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(path, json=body)
    assert reader.status_code == 200, reader.text
    assert fake.calls[-1][0] == bridge


def test_opening_and_attachments_openapi() -> None:
    spec = _spec()
    expected = {
        _OPEN: ("期初余额", "coReportOpeningBalance", "报表"),
        _GL_ATT: ("总账凭证附件列表", "coGlVoucherAttachmentsList", "总账凭证"),
        _ATT: ("单据附件列表", "coVoucherAttachmentsList", None),
    }
    for path, (summary, operation_id, tag) in expected.items():
        operation = spec["paths"][path]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert "权限：只读" in operation["description"]
        if tag is not None:
            assert operation["tags"] == [tag]
    assert set(_enum(spec, "ReportOpeningIn", "module")) == {"stock", "arap", "gl"}
