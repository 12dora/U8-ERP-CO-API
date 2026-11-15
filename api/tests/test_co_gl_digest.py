"""总账凭证摘要 /v1/co/gl/vouchers/digest：请求校验、转发给桥的字段、响应放行、只读权限和 OpenAPI。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.errors import ApiError

_PATH = "/v1/co/gl/vouchers/digest"
_BRIDGE = "/v1/gl/vouchers/digest"
_PRINT = "ccf3a661cdcf7b87ccc6bfe9892067c5266651e790c34ed8886566f88311be93"


class _DigestBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        item = {"period": 9, "sign": "转", "no": 3, "fingerprint": _PRINT}
        if not payload.get("keys_only"):
            item.update(maker="张三", checker="李四", cashier="", bookkeeper="", posted=False, void=False)
            item.update(debit_total=100.00, lines=2, date="2026-09-08")
        return {
            "ok": True,
            "fiscal_year": payload.get("fiscal_year", 2026),
            "periods": payload.get("periods", [8, 9, 10, 11, 12]),
            "items": [item],
            "next": "9.3.3",
            "watermark": "9001",
            "ident": "9001",
            "bridge_note": "kept",
        }


def test_digest_forwards_defaults_and_keeps_bridge_fields() -> None:
    fake = _DigestBridge()
    ok = _client(fake).post(_PATH, json=_auth())
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert "periods" not in sent
    assert "closed_periods" not in sent
    body = ok.json()
    assert body["periods"] == [8, 9, 10, 11, 12]
    assert body["watermark"] == "9001"
    assert body["ident"] == "9001"
    assert body["bridge_note"] == "kept"
    assert body["items"][0]["fingerprint"] == _PRINT
    assert body["items"][0]["bookkeeper"] == ""


def test_digest_sends_exact_sealed_keys() -> None:
    fields = {"fiscal_year": 2026, "periods": [9, 8], "after": "9.3.120", "limit": 500, "keys_only": True}
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PATH, json=_auth(**fields))
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(sent) == _sealed(*fields)
    assert sent["periods"] == [9, 8]
    assert sent["keys_only"] is True
    assert _SECRET not in raw.decode("utf-8")


def test_digest_keys_only_items() -> None:
    fake = _DigestBridge()
    ok = _client(fake).post(_PATH, json=_auth(keys_only=True, closed_periods=0))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["closed_periods"] == 0
    assert set(ok.json()["items"][0]) == {"period", "sign", "no", "fingerprint"}


_REJECTS = (
    _auth(fiscal_year=1899),
    _auth(periods=[]),
    _auth(periods=[0]),
    _auth(periods=[13]),
    _auth(periods=[9, 9]),
    _auth(periods=list(range(1, 13)) + [1]),
    _auth(periods=[9], closed_periods=1),
    _auth(closed_periods=-1),
    _auth(closed_periods=13),
    _auth(after="9.3"),
    _auth(after="a.b.c"),
    _auth(after="9.3.\u0661\u0662"),
    _auth(limit=0),
    _auth(limit=501),
    _auth(keys_only="maybe"),
    _auth(keys_only="yes"),
    _auth(keys_only=1),
    _auth(periods=["9"]),
    _auth(periods=[True]),
    _auth(fiscal_year="2026"),
    _auth(closed_periods="1"),
    _auth(closed_periods=False),
    _auth(limit="200"),
    _auth(period=9),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_digest_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_digest_is_open_to_read_only_callers() -> None:
    fake = _DigestBridge()
    ok = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=_auth())
    assert ok.status_code == 200, ok.text
    assert len(fake.calls) == 1


def test_digest_bridge_error_passes_through() -> None:
    fake = _DigestBridge()
    fake.error = ApiError(403, "no_permission", "没有凭证查询权限")
    denied = _client(fake).post(_PATH, json=_auth())
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "no_permission"


def test_digest_is_in_openapi_as_read() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "总账凭证摘要"
    assert operation["operationId"] == "coGlVoucherDigest"
    assert operation["tags"] == ["总账凭证"]
    assert "权限：只读" in operation["description"]
    assert "fingerprint" in operation["description"]
