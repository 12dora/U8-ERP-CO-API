"""总账红字冲销 /v1/co/gl/vouchers/reverse：请求校验、转发给桥的字段、响应放行、审计和 OpenAPI。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.errors import ApiError

_PATH = "/v1/co/gl/vouchers/reverse"
_BRIDGE = "/v1/gl/vouchers/reverse"
_KEY = {"period": 12, "sign": "转", "no": 9}


class _ReverseBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "validate", "action": "gl_reverse", "docs": []}
        source = {
            "fiscal_year": payload.get("fiscal_year", 2026),
            "period": payload["period"],
            "sign": payload["sign"],
            "no": payload["no"],
            "out_no": "GL0000000900001",
            "out_no_assigned": False,
        }
        return {
            "ok": True,
            "fiscal_year": 2026,
            "period": 1,
            "sign": payload["sign"],
            "no": 2,
            "voucher_date": payload.get("voucher_date", "2026-01-31"),
            "lines": 2,
            "out_no": "GL0000000900002",
            "reversal_of": source,
            "bridge_note": "kept",
        }


def test_reverse_forwards_exact_fields_and_keeps_bridge_fields() -> None:
    fake = _ReverseBridge()
    ok = _client(fake).post(_PATH, json=_auth(**_KEY))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert (sent["period"], sent["sign"], sent["no"]) == (12, "转", 9)
    assert "fiscal_year" not in sent
    assert "voucher_date" not in sent
    body = ok.json()
    assert body["no"] == 2
    assert body["out_no"] == "GL0000000900002"
    assert body["reversal_of"]["out_no"] == "GL0000000900001"
    assert body["bridge_note"] == "kept"


def test_reverse_forwards_year_and_voucher_date_when_given() -> None:
    fake = _ReverseBridge()
    ok = _client(fake).post(_PATH, json=_auth(fiscal_year=2025, voucher_date="2026-01-31", **_KEY))
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert sent["fiscal_year"] == 2025
    assert sent["voucher_date"] == "2026-01-31"
    assert ok.json()["reversal_of"]["fiscal_year"] == 2025


def test_reverse_sends_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PATH, json=_auth(**_KEY))
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(sent) == _sealed("period", "sign", "no", "caller")
    assert _SECRET not in raw.decode("utf-8")


_REJECTS = (
    _auth(period=12, sign="转"),
    _auth(sign="转", no=9),
    _auth(period=13, sign="转", no=9),
    _auth(period=12, sign="转账凭", no=9),
    _auth(period=12, sign="转", no=0),
    _auth(period=12, sign="转", no=32768),
    _auth(fiscal_year=1899, **_KEY),
    _auth(voucher_date="2026/01/31", **_KEY),
    _auth(lines=[], **_KEY),
    _auth(head={"sign": "转"}, **_KEY),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_reverse_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_reverse_audit_names_the_source_voucher(capsys) -> None:
    ok = _client(_ReverseBridge()).post(_PATH, json=_auth(fiscal_year=2025, **_KEY))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert audit_line(out, _PATH)["action"].endswith("#2025:12-转-9")


def test_reverse_bridge_conflict_passes_through() -> None:
    fake = _ReverseBridge()
    fake.error = ApiError(409, "state_mismatch", "凭证已被红字冲销")
    denied = _client(fake).post(_PATH, json=_auth(**_KEY))
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "state_mismatch"


def test_reverse_outcome_unknown_passes_through() -> None:
    fake = _ReverseBridge()
    fake.error = ApiError(504, "outcome_unknown", "红字凭证已保存（2026年1期 转-2，冲销 2025年12期 转-9），回读失败")
    denied = _client(fake).post(_PATH, json=_auth(**_KEY))
    assert denied.status_code == 504
    assert denied.json()["error"]["code"] == "outcome_unknown"


def test_reverse_dry_run_is_forwarded() -> None:
    fake = _ReverseBridge()
    ok = _client(fake).post(_PATH, json=_auth(dry_run=True, **_KEY))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    assert ok.json()["mode"] == "validate"


def test_reverse_is_in_openapi_as_write() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "红字冲销总账凭证"
    assert operation["operationId"] == "coGlVoucherReverse"
    assert operation["tags"] == ["总账凭证"]
    assert "权限：写" in operation["description"]
    assert "已记账" in operation["description"]
    assert "voucher_date" in operation["description"]
