"""月末结账 /v1/co/periods/close：请求校验、转发给桥的字段、响应放行、预演、through、测试账套 403 放行、审计和 OpenAPI。
桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line, base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, WRITE
from u8co_api.co_models_periods import PeriodsCloseIn
from u8co_api.errors import ApiError

_PATH = "/v1/co/periods/close"
_BRIDGE = "/v1/periods/close"
_CLOSE = {"module": "gl", "fiscal_year": 2026, "period": 9, "action": "close"}
_THROUGH = {"fiscal_year": 2026, "period": 8, "action": "close", "through": True}


class _PeriodBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        steps = [{"module": "gl", "fiscal_year": payload["fiscal_year"], "period": payload["period"]}]
        if payload.get("dry_run") is True:
            # 桥的标准预演体：会结账的期间在 detail.periods。
            return {"ok": True, "dry_run": True, "mode": "rollback", "action": "period_close", "docs": [],
                    "detail": {"periods": steps}}
        if payload.get("through") is True:
            return {"ok": True, "action": "close", "through": steps, "count": 1, "bridge_note": "kept"}
        return {
            "ok": True,
            "module": payload["module"],
            "action": payload["action"],
            "fiscal_year": payload["fiscal_year"],
            "period": payload["period"],
            "closed": payload["action"] == "close",
            "bridge_note": "kept",
        }


def test_close_forwards_exact_fields_and_keeps_bridge_fields() -> None:
    fake = _PeriodBridge()
    ok = _client(fake).post(_PATH, json=_auth(**_CLOSE))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert (sent["module"], sent["fiscal_year"], sent["period"], sent["action"]) == ("gl", 2026, 9, "close")
    assert "dry_run" not in sent
    assert "through" not in sent
    body = ok.json()
    assert body["closed"] is True
    assert body["bridge_note"] == "kept"


def test_reopen_reports_closed_false() -> None:
    ok = _client(_PeriodBridge()).post(_PATH, json=_auth(**dict(_CLOSE, action="reopen")))
    assert ok.status_code == 200, ok.text
    assert ok.json()["closed"] is False
    assert ok.json()["action"] == "reopen"


def test_through_without_module_lists_the_periods() -> None:
    fake = _PeriodBridge()
    ok = _client(fake).post(_PATH, json=_auth(**_THROUGH))
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert sent["through"] is True
    assert "module" not in sent
    body = ok.json()
    assert body["count"] == 1
    assert body["through"] == [{"module": "gl", "fiscal_year": 2026, "period": 8}]


def test_close_sends_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PATH, json=_auth(**_CLOSE))
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(sent) == _sealed("module", "fiscal_year", "period", "action", "caller")
    assert _SECRET not in raw.decode("utf-8")


def test_dry_run_returns_the_preview_with_periods() -> None:
    fake = _PeriodBridge()
    ok = _client(fake).post(_PATH, json=_auth(dry_run=True, **_THROUGH))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    body = ok.json()
    assert (body["dry_run"], body["mode"], body["action"]) == (True, "rollback", "period_close")
    assert body["detail"]["periods"] == [{"module": "gl", "fiscal_year": 2026, "period": 8}]


def test_dry_run_rejects_idempotency_key() -> None:
    fake = _PeriodBridge()
    denied = _client(fake).post(_PATH, json=_auth(dry_run=True, **_CLOSE), headers={"Idempotency-Key": "close-1"})
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "dry_run"
    assert fake.calls == []


def test_idempotency_key_is_forwarded() -> None:
    fake = _PeriodBridge()
    ok = _client(fake).post(_PATH, json=_auth(**_CLOSE), headers={"Idempotency-Key": "close-1"})
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["idempotency_key"] == "close-1"


_REJECTS = (
    _auth(fiscal_year=2026, period=9, action="close"),
    _auth(module="xx", fiscal_year=2026, period=9, action="close"),
    _auth(module="GL", fiscal_year=2026, period=9, action="close"),
    _auth(module="gl", fiscal_year=2026, period=0, action="close"),
    _auth(module="gl", fiscal_year=2026, period=13, action="close"),
    _auth(module="gl", fiscal_year=26, period=9, action="close"),
    _auth(module="gl", fiscal_year="2026", period=9, action="close"),
    _auth(module="gl", fiscal_year=2026, period="9", action="close"),
    _auth(module="gl", fiscal_year=2026, period=9, action="post"),
    _auth(module="gl", fiscal_year=2026, period=9, action="close", through="yes"),
    _auth(fiscal_year=2026, period=9, action="reopen", through=True),
    _auth(module="gl", fiscal_year=2026, period=9, action="close", dry_run="yes"),
    _auth(module="gl", fiscal_year=2026, period=9, action="close", year_end=True),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize(
    "message",
    ["总账 2026 年 10 期：上一期间还没结账（2026 年第一个未结账期间是 9 期）", "总账 2026 年 9 期：请先做期间损益结转"],
)
def test_bridge_conflict_passes_through(message: str) -> None:
    fake = _PeriodBridge()
    fake.error = ApiError(409, "state_mismatch", message)
    denied = _client(fake).post(_PATH, json=_auth(**_CLOSE))
    assert denied.status_code == 409
    error = denied.json()["error"]
    assert error["code"] == "state_mismatch"
    assert error["message"] == message


def test_non_test_account_403_passes_through_with_hint() -> None:
    fake = _PeriodBridge()
    fake.error = ApiError(403, "test_account_only", "月末结账只对配置为测试账套的账套开放")
    denied = _client(fake).post(_PATH, json=_auth(**_CLOSE))
    assert denied.status_code == 403
    error = denied.json()["error"]
    assert error["code"] == "test_account_only"
    assert error["retryable"] is False
    assert "testAccounts" in error["hint"]


def test_audit_names_action_and_module(capsys) -> None:
    ok = _client(_PeriodBridge()).post(_PATH, json=_auth(**dict(_CLOSE, action="reopen")))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert audit_line(out, _PATH)["action"] == "co:periods/close:reopen#gl"
    ok = _client(_PeriodBridge()).post(_PATH, json=_auth(**_THROUGH))
    assert ok.status_code == 200, ok.text
    assert audit_line(capsys.readouterr().out, _PATH)["action"] == "co:periods/close:close#through"


def test_model_and_access_table() -> None:
    assert ACCESS["co:periods/close"] == WRITE
    body = PeriodsCloseIn.model_validate(_auth(**_CLOSE))
    assert body.audit_ref() == "gl"
    assert body.dry_run is False
    assert body.through is None


def test_read_only_caller_is_refused() -> None:
    fake = _PeriodBridge()
    denied = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=_auth(**_CLOSE))
    assert denied.status_code == 403
    assert fake.calls == []


def test_is_in_openapi_as_write_with_dry_run_and_idempotency() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "月末结账"
    assert operation["operationId"] == "coPeriodsClose"
    assert operation["tags"] == ["月末结账"]
    assert operation["x-u8co-access"] == "write"
    assert operation["x-u8co-dry-run"] is True
    assert "权限：写" in operation["description"]
    for auth_id in ("GL1512", "GL1520", "PU0207", "testAccounts"):
        assert auth_id in operation["description"]
    names = {item.get("name") for item in operation.get("parameters", [])}
    assert "Idempotency-Key" in names
