"""固定资产报表 /v1/co/reports/fa_changes、fa_depreciation：转发的字段、提前拒绝、只读权限、响应放行和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from tests.support import base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, READ

_CHANGES = "/v1/co/reports/fa_changes"
_DEPR = "/v1/co/reports/fa_depreciation"


@pytest.mark.parametrize(
    ("path", "body", "keys"),
    (
        (_CHANGES, _auth(), _sealed()),
        (
            _CHANGES,
            _auth(fiscal_year=2026, period=4, card="00021", code="00005", change_type=1, after="QUJD", limit=5),
            _sealed("fiscal_year", "period", "card", "code", "change_type", "after", "limit"),
        ),
        (_DEPR, _auth(period=8, card="00021", nonzero=True), _sealed("period", "card", "nonzero")),
    ),
)
def test_fa_reports_forward_exact_sealed_keys(path: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + path.replace("/v1/co/", "/v1/")
    assert set(sent) == keys
    assert _SECRET.encode() not in raw


@pytest.mark.parametrize(
    ("path", "body"),
    (
        (_CHANGES, _auth(period=13)),
        (_CHANGES, _auth(period="4")),
        (_CHANGES, _auth(card="a%")),
        (_CHANGES, _auth(code="12345678901")),
        (_CHANGES, _auth(change_type=0)),
        (_CHANGES, _auth(nonzero=True)),
        (_CHANGES, _auth(limit=1001)),
        (_DEPR, _auth(fiscal_year=1999)),
        (_DEPR, _auth(nonzero=1)),
        (_DEPR, _auth(code="00005")),
        (_DEPR, _auth(after="")),
    ),
)
def test_fa_reports_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _FaBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        item = {"card_code": "00021", "period": 8, "amount": 120.5, "accumulated": 3000.0, "extra_col": "x"}
        return {"ok": True, "fiscal_year": 2026, "posted_periods": [1, 8], "items": [item], "next": None, "extra": 1}


def test_fa_depreciation_response_keeps_bridge_fields() -> None:
    body = _client(_FaBridge()).post(_DEPR, json=_auth()).json()
    assert body["posted_periods"] == [1, 8]
    assert body["items"][0]["extra_col"] == "x"
    assert body["fiscal_year"] == 2026
    assert body["extra"] == 1


@pytest.mark.parametrize("path", (_CHANGES, _DEPR))
def test_fa_reports_are_read_only_and_gated(path: str) -> None:
    assert ACCESS["co:" + path.removeprefix("/v1/co/")] == READ
    fake = FakeBridge()
    body = _auth()
    assert _client(fake, co_enabled=False).post(path, json=body).status_code == 404
    assert _client(fake, claims=base_claims()).post(path, json=body).status_code == 403
    assert fake.calls == []
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(path, json=body)
    assert reader.status_code == 200, reader.text
    assert fake.calls[-1][0] == path.replace("/v1/co/", "/v1/")


@pytest.mark.parametrize(
    ("path", "summary", "operation_id"),
    ((_CHANGES, "固定资产变动单", "coReportFaChanges"), (_DEPR, "固定资产折旧", "coReportFaDepreciation")),
)
def test_fa_reports_openapi(path: str, summary: str, operation_id: str) -> None:
    operation = _spec()["paths"][path]["post"]
    assert operation["summary"] == summary
    assert operation["operationId"] == operation_id
    assert operation["tags"] == ["报表"]
    assert "权限：只读" in operation["description"]
