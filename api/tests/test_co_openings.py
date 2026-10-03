"""采购期初记账、存货核算期初记账 /v1/co/openings/post：请求校验、转发给桥的字段、响应放行、预演、409 放行、审计和 OpenAPI。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line, base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, WRITE
from u8co_api.co_models_openings import OpeningsPostIn
from u8co_api.errors import ApiError

_PATH = "/v1/co/openings/post"
_BRIDGE = "/v1/openings/post"
_POST = {"module": "pu", "action": "post"}


class _OpeningBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        reply = {
            "ok": True,
            "module": payload["module"],
            "action": payload["action"],
            "posted": payload["action"] == "post",
            "opening_year": 2024,
            "start_date": "2024-01-01",
            "bridge_note": "kept",
        }
        if payload["module"] == "ia":
            reply["counts"] = {"st34_verified": 2, "summary_m0": 2, "gl_mend_p0": 1}
        if payload.get("dry_run") is True:
            # 桥的标准预演体：操作后会是的状态在 detail.opening。
            opening = {key: reply[key] for key in ("module", "posted", "opening_year", "start_date")}
            action = "opening_" + payload["action"]
            return {"ok": True, "dry_run": True, "mode": "rollback", "action": action, "docs": [],
                    "detail": {"opening": opening}}
        return reply


def test_post_forwards_exact_fields_and_keeps_bridge_fields() -> None:
    fake = _OpeningBridge()
    ok = _client(fake).post(_PATH, json=_auth(**_POST))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert (sent["module"], sent["action"]) == ("pu", "post")
    assert "dry_run" not in sent
    body = ok.json()
    assert body["posted"] is True
    assert body["opening_year"] == 2024
    assert body["start_date"] == "2024-01-01"
    assert body["bridge_note"] == "kept"


def test_unpost_reports_posted_false() -> None:
    ok = _client(_OpeningBridge()).post(_PATH, json=_auth(module="pu", action="unpost"))
    assert ok.status_code == 200, ok.text
    assert ok.json()["posted"] is False
    assert ok.json()["action"] == "unpost"


def test_post_sends_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PATH, json=_auth(**_POST))
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(sent) == _sealed("module", "action", "caller")
    assert _SECRET not in raw.decode("utf-8")


def test_dry_run_returns_the_preview_with_bridge_fields() -> None:
    fake = _OpeningBridge()
    ok = _client(fake).post(_PATH, json=_auth(dry_run=True, **_POST))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    body = ok.json()
    assert body["dry_run"] is True
    assert body["mode"] == "rollback"
    assert body["action"] == "opening_post"
    want = {"module": "pu", "posted": True, "opening_year": 2024, "start_date": "2024-01-01"}
    assert body["detail"]["opening"] == want


def test_dry_run_rejects_idempotency_key() -> None:
    fake = _OpeningBridge()
    denied = _client(fake).post(_PATH, json=_auth(dry_run=True, **_POST), headers={"Idempotency-Key": "pu-open-1"})
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "dry_run"
    assert fake.calls == []


def test_idempotency_key_is_forwarded() -> None:
    fake = _OpeningBridge()
    ok = _client(fake).post(_PATH, json=_auth(**_POST), headers={"Idempotency-Key": "pu-open-1"})
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["idempotency_key"] == "pu-open-1"


_REJECTS = (
    _auth(action="post"),
    _auth(module="pu"),
    _auth(module="st", action="post"),
    _auth(module="PU", action="post"),
    _auth(module="IA", action="post"),
    _auth(module="st", action="unpost"),
    _auth(module="pu", action="close"),
    _auth(module="pu", action="post", dry_run="yes"),
    _auth(module="pu", action="post", year_start=2024),
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
    ["采购期初已记账", "采购期初未记账", "已有采购发票，不能取消期初记账", "采购管理未启用"],
)
def test_bridge_conflict_passes_through(message: str) -> None:
    fake = _OpeningBridge()
    fake.error = ApiError(409, "state_mismatch", message)
    denied = _client(fake).post(_PATH, json=_auth(**_POST))
    assert denied.status_code == 409
    error = denied.json()["error"]
    assert error["code"] == "state_mismatch"
    assert error["message"] == message


def test_non_test_account_403_passes_through_with_hint() -> None:
    fake = _OpeningBridge()
    fake.error = ApiError(403, "test_account_only", "期初记账只对配置为测试账套的账套开放")
    denied = _client(fake).post(_PATH, json=_auth(dry_run=True, **_POST))
    assert denied.status_code == 403
    error = denied.json()["error"]
    assert (error["code"], error["message"]) == ("test_account_only", "期初记账只对配置为测试账套的账套开放")
    assert error["retryable"] is False
    assert "testAccounts" in error["hint"]


def test_audit_names_action_and_module(capsys) -> None:
    ok = _client(_OpeningBridge()).post(_PATH, json=_auth(module="pu", action="unpost"))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert audit_line(out, _PATH)["action"] == "co:openings/post:unpost#pu"


def test_model_and_access_table() -> None:
    assert ACCESS["co:openings/post"] == WRITE
    body = OpeningsPostIn.model_validate(_auth(**_POST))
    assert body.audit_ref() == "pu"
    assert body.dry_run is False


def test_read_only_caller_is_refused() -> None:
    fake = _OpeningBridge()
    denied = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=_auth(**_POST))
    assert denied.status_code == 403
    assert fake.calls == []


def test_is_in_openapi_as_write_with_dry_run_and_idempotency() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "采购 / 存货核算期初记账"
    assert operation["operationId"] == "coOpeningsPost"
    assert operation["tags"] == ["期初记账"]
    assert operation["x-u8co-access"] == "write"
    assert operation["x-u8co-dry-run"] is True
    assert "权限：写" in operation["description"]
    for word in ("PU0206", "ASM3102", "testAccounts", "test_account_only"):
        assert word in operation["description"]
    names = {item.get("name") for item in operation.get("parameters", [])}
    assert "Idempotency-Key" in names


def test_ia_post_forwards_module_and_keeps_counts() -> None:
    fake = _OpeningBridge()
    ok = _client(fake).post(_PATH, json=_auth(module="ia", action="post"))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert (sent["module"], sent["action"]) == ("ia", "post")
    body = ok.json()
    assert (body["module"], body["posted"]) == ("ia", True)
    assert body["counts"] == {"st34_verified": 2, "summary_m0": 2, "gl_mend_p0": 1}


def test_ia_unpost_closed_months_409_passes_through() -> None:
    fake = _OpeningBridge()
    fake.error = ApiError(409, "state_mismatch", "存货核算已有月份结账，不能取消期初记账")
    denied = _client(fake).post(_PATH, json=_auth(module="ia", action="unpost"))
    assert denied.status_code == 409
    assert denied.json()["error"]["message"] == "存货核算已有月份结账，不能取消期初记账"
    assert fake.calls[-1][1]["module"] == "ia"


def test_ia_dry_run_forwards_flag() -> None:
    fake = _OpeningBridge()
    ok = _client(fake).post(_PATH, json=_auth(module="ia", action="post", dry_run=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    assert ok.json()["detail"]["opening"]["module"] == "ia"


def test_ia_audit_names_module(capsys) -> None:
    ok = _client(_OpeningBridge()).post(_PATH, json=_auth(module="ia", action="post"))
    assert ok.status_code == 200, ok.text
    assert audit_line(capsys.readouterr().out, _PATH)["action"] == "co:openings/post:post#ia"
    assert OpeningsPostIn.model_validate(_auth(module="ia", action="unpost")).audit_ref() == "ia"
