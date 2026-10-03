"""账套体检 /v1/co/reports/account_readiness：转发的字段、提前拒绝、权限、响应放行和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from tests.support import base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, READ
from u8co_api.co_models_reports_ready import READINESS_CHECKS

_PATH = "/v1/co/reports/account_readiness"
_BRIDGE = "/v1/reports/account_readiness"


@pytest.mark.parametrize(
    ("body", "keys"),
    (
        (_auth(), _sealed()),
        (_auth(as_of="2026-03-31"), _sealed("as_of")),
    ),
)
def test_readiness_forwards_exact_sealed_keys(body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(_PATH, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + _BRIDGE
    assert set(sent) == keys
    assert _SECRET.encode() not in raw


@pytest.mark.parametrize(
    "body",
    (
        _auth(module="gl"),
        _auth(as_of="2026-02-30"),
        _auth(as_of="20260331"),
        _auth(as_of=20260331),
        _auth(fiscal_year=2026),
    ),
)
def test_readiness_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _ReadyBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        checks = [
            {
                "id": name,
                "title": name,
                "status": "ok",
                "detail": "",
                "fix_hint": "",
                "docs_anchor": f"getting-started.md#{name}",
                "safe": True,
            }
            for name in READINESS_CHECKS
        ]
        checks[2]["status"] = "warn"
        return {
            "ok": True,
            "overall": "warn",
            "as_of": "2026-03-31",
            "server_today": "2026-03-31",
            "acc": "999",
            "checks": checks,
            "extra": 1,
        }


def test_readiness_response_keeps_bridge_fields() -> None:
    body = _client(_ReadyBridge()).post(_PATH, json=_auth()).json()
    assert body["overall"] == "warn"
    assert body["server_today"] == "2026-03-31"
    assert [item["id"] for item in body["checks"]] == list(READINESS_CHECKS)
    assert body["checks"][2]["docs_anchor"] == "getting-started.md#calendar"
    assert body["extra"] == 1


def test_readiness_is_read_only_and_gated() -> None:
    assert ACCESS["co:reports/account_readiness"] == READ
    fake = FakeBridge()
    body = _auth()
    assert _client(fake, co_enabled=False).post(_PATH, json=body).status_code == 404
    assert _client(fake, claims=base_claims()).post(_PATH, json=body).status_code == 403
    outside = _client(fake).post(_PATH, json=dict(body, acc="001"))
    assert outside.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=body)
    assert reader.status_code == 200, reader.text
    assert fake.calls[-1][0] == _BRIDGE


def test_readiness_openapi() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "账套体检"
    assert operation["operationId"] == "coReportAccountReadiness"
    assert operation["tags"] == ["报表"]
    assert "权限：只读" in operation["description"]
