"""总账取消记账 /v1/co/gl/vouchers/unpost：请求校验、转发给桥的字段、响应放行、审计和 OpenAPI。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.errors import ApiError
from u8co_api.write_class import RULES

_PATH = "/v1/co/gl/vouchers/unpost"
_BRIDGE = "/v1/gl/vouchers/unpost"
_TWO = [{"sign": "转", "no": 3}, {"sign": "收", "no": 12}]


class _UnpostBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        vouchers = payload.get("vouchers") or [{"sign": "转", "no": 3}]
        return {
            "ok": True,
            "fiscal_year": payload.get("fiscal_year", 2026),
            "period": payload.get("period", 9),
            "count": len(vouchers),
            "vouchers": [{"sign": v["sign"], "no": v["no"], "extra": "kept"} for v in vouchers],
            "bridge_note": "kept",
        }


def test_unpost_without_fields_forwards_only_auth() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PATH, json=_auth())
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(sent) == _sealed("caller")
    assert _SECRET not in raw.decode("utf-8")


def test_unpost_forwards_check_fields_and_keeps_bridge_fields() -> None:
    fake = _UnpostBridge()
    ok = _client(fake).post(_PATH, json=_auth(period=9, vouchers=_TWO, fiscal_year=2025))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert sent["vouchers"] == _TWO
    assert sent["period"] == 9
    assert sent["fiscal_year"] == 2025
    body = ok.json()
    assert body["count"] == 2
    assert body["bridge_note"] == "kept"
    assert body["vouchers"][0]["extra"] == "kept"


_REJECTS = (
    _auth(vouchers=_TWO),
    _auth(period=0),
    _auth(period=13),
    _auth(period=9, vouchers=[]),
    _auth(period=9, vouchers=[{"sign": "转", "no": 3}, {"sign": "转", "no": 3}]),
    _auth(period=9, vouchers=[{"sign": "转", "no": 0}]),
    _auth(fiscal_year=1899),
    _auth(sign="转"),
    _auth(no=3),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_unpost_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_unpost_audit_names_the_checked_batch(capsys) -> None:
    ok = _client(_UnpostBridge()).post(_PATH, json=_auth(period=9, vouchers=_TWO))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert audit_line(out, _PATH)["action"] == "co:gl/vouchers/unpost:unpost#9-转-3+1"


def test_unpost_audit_without_check_fields(capsys) -> None:
    ok = _client(_UnpostBridge()).post(_PATH, json=_auth())
    assert ok.status_code == 200, ok.text
    assert audit_line(capsys.readouterr().out, _PATH)["action"] == "co:gl/vouchers/unpost:unpost#last"


@pytest.mark.parametrize(
    ("status", "code"),
    [(403, "test_account_only"), (409, "state_mismatch"), (504, "outcome_unknown")],
)
def test_unpost_bridge_errors_pass_through(status: int, code: str) -> None:
    fake = _UnpostBridge()
    fake.error = ApiError(status, code, "桥拒绝")
    denied = _client(fake).post(_PATH, json=_auth())
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_unpost_is_a_write_classified_as_other() -> None:
    assert RULES["gl/vouchers/unpost"] == ("gl", "other")


def test_unpost_is_in_openapi_as_write() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "取消记账"
    assert operation["operationId"] == "coGlVoucherUnpost"
    assert operation["tags"] == ["总账凭证"]
    assert "权限：写" in operation["description"]
    assert "测试账套" in operation["description"]
