"""存货核算 /v1/co/ia/post、/v1/co/ia/period_end：请求校验、转发给桥的字段、响应放行、预演、
测试账套 403 放行、拒绝原样放行、审计和 OpenAPI。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line, base_claims, write_keys
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, WRITE
from u8co_api.co_models_ia import IaPeriodEndIn, IaPostIn
from u8co_api.co_bridge import to_api_error
from u8co_api.errors import ApiError

_POST = "/v1/co/ia/post"
_END = "/v1/co/ia/period_end"
_BRIDGE_POST = "/v1/ia/post"
_BRIDGE_END = "/v1/ia/period_end"
_POST_BODY = {"fiscal_year": 2026, "period": 9, "action": "post"}
_END_BODY = {"fiscal_year": 2026, "period": 9, "action": "run"}
_COUNTS = {"subsidiary_month": 120, "summary_month": 40}
_DRY_ACTION = {_BRIDGE_POST: "ia_post", _BRIDGE_END: "ia_period_end"}


class _IaBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            # 桥的标准预演体：脚本的诊断计数在 detail.counts。
            return {"ok": True, "dry_run": True, "mode": "rollback", "action": _DRY_ACTION[path], "docs": [],
                    "detail": {"counts": dict(_COUNTS)}}
        return {
            "ok": True,
            "action": payload["action"],
            "fiscal_year": payload["fiscal_year"],
            "period": payload["period"],
            "counts": dict(_COUNTS),
            "bridge_note": "kept",
        }


def test_post_forwards_exact_fields_and_keeps_bridge_fields() -> None:
    fake = _IaBridge()
    ok = _client(fake).post(_POST, json=_auth(**_POST_BODY))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE_POST
    assert (sent["fiscal_year"], sent["period"], sent["action"]) == (2026, 9, "post")
    assert "dry_run" not in sent
    assert "on_uncosted" not in sent
    body = ok.json()
    assert body["counts"] == _COUNTS
    assert body["bridge_note"] == "kept"


@pytest.mark.parametrize("mode", ["refuse", "skip"])
def test_post_forwards_on_uncosted(mode: str) -> None:
    fake = _IaBridge()
    ok = _client(fake).post(_POST, json=_auth(on_uncosted=mode, **_POST_BODY))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["on_uncosted"] == mode


@pytest.mark.parametrize("action", ["run", "cancel"])
def test_period_end_forwards_exact_fields(action: str) -> None:
    fake = _IaBridge()
    ok = _client(fake).post(_END, json=_auth(**dict(_END_BODY, action=action)))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE_END
    assert (sent["fiscal_year"], sent["period"], sent["action"]) == (2026, 9, action)
    assert ok.json()["action"] == action


@pytest.mark.parametrize(
    ("path", "body", "keys"),
    [
        (_POST, dict(_POST_BODY, on_uncosted="skip"), ("fiscal_year", "period", "action", "on_uncosted")),
        (_END, _END_BODY, ("fiscal_year", "period", "action")),
    ],
)
def test_sends_exact_sealed_keys(path: str, body: dict, keys: tuple[str, ...]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(path, json=_auth(**body))
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + path.replace("/v1/co/", "/v1/"))
    assert set(sent) == write_keys(path, _sealed(*keys))
    assert _SECRET not in raw.decode("utf-8")


@pytest.mark.parametrize(("path", "body"), [(_POST, _POST_BODY), (_END, _END_BODY)])
def test_dry_run_returns_the_preview_with_counts(path: str, body: dict) -> None:
    fake = _IaBridge()
    ok = _client(fake).post(path, json=_auth(dry_run=True, **body))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    got = ok.json()
    assert (got["dry_run"], got["mode"]) == (True, "rollback")
    assert got["detail"]["counts"] == _COUNTS


def test_dry_run_rejects_idempotency_key() -> None:
    fake = _IaBridge()
    denied = _client(fake).post(_POST, json=_auth(dry_run=True, **_POST_BODY), headers={"Idempotency-Key": "ia-1"})
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "dry_run"
    assert fake.calls == []


def test_idempotency_key_is_forwarded() -> None:
    fake = _IaBridge()
    ok = _client(fake).post(_END, json=_auth(**_END_BODY), headers={"Idempotency-Key": "ia-1"})
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["idempotency_key"] == "ia-1"


_REJECTS = (
    (_POST, _auth(fiscal_year=2026, period=9)),
    (_POST, _auth(fiscal_year=2026, period=9, action="run")),
    (_POST, _auth(fiscal_year=2026, period=9, action="POST")),
    (_POST, _auth(fiscal_year=2026, period=0, action="post")),
    (_POST, _auth(fiscal_year=2026, period=13, action="post")),
    (_POST, _auth(fiscal_year=26, period=9, action="post")),
    (_POST, _auth(fiscal_year="2026", period=9, action="post")),
    (_POST, _auth(fiscal_year=2026, period="9", action="post")),
    (_POST, _auth(fiscal_year=2026, period=9, action="post", on_uncosted="manual")),
    (_POST, _auth(fiscal_year=2026, period=9, action="unpost", on_uncosted="skip")),
    (_POST, _auth(fiscal_year=2026, period=9, action="post", dry_run="yes")),
    (_POST, _auth(fiscal_year=2026, period=9, action="post", module="ia")),
    (_END, _auth(fiscal_year=2026, period=9, action="post")),
    (_END, _auth(fiscal_year=2026, period=9, action="run", on_uncosted="skip")),
    (_END, _auth(period=9, action="run")),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (409, "state_mismatch", "存货核算 2026 年 9 期：该月已结账"),
        (409, "u8_rejected", "U8 拒绝：存货档案不存在"),
    ],
)
def test_bridge_refusal_passes_through(status: int, code: str, message: str) -> None:
    fake = _IaBridge()
    fake.error = ApiError(status, code, message)
    denied = _client(fake).post(_POST, json=_auth(**_POST_BODY))
    assert denied.status_code == status
    error = denied.json()["error"]
    assert (error["code"], error["message"]) == (code, message)


def test_non_test_account_403_passes_through_with_hint() -> None:
    fake = _IaBridge()
    fake.error = ApiError(403, "test_account_only", "存货核算记账只对配置为测试账套的账套开放")
    denied = _client(fake).post(_END, json=_auth(**_END_BODY))
    assert denied.status_code == 403
    error = denied.json()["error"]
    assert error["code"] == "test_account_only"
    assert error["retryable"] is False
    assert "testAccounts" in error["hint"]


def test_audit_names_action_and_month(capsys) -> None:
    ok = _client(_IaBridge()).post(_POST, json=_auth(**dict(_POST_BODY, action="unpost")))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert audit_line(out, _POST)["action"] == "co:ia/post:unpost#2026-9"
    ok = _client(_IaBridge()).post(_END, json=_auth(**_END_BODY))
    assert ok.status_code == 200, ok.text
    assert audit_line(capsys.readouterr().out, _END)["action"] == "co:ia/period_end:run#2026-9"


def test_models_and_access_table() -> None:
    assert ACCESS["co:ia/post"] == WRITE
    assert ACCESS["co:ia/period_end"] == WRITE
    body = IaPostIn.model_validate(_auth(**_POST_BODY))
    assert body.audit_ref() == "2026-9"
    assert (body.dry_run, body.on_uncosted) == (False, None)
    assert IaPeriodEndIn.model_validate(_auth(**_END_BODY)).action == "run"


@pytest.mark.parametrize(("path", "body"), [(_POST, _POST_BODY), (_END, _END_BODY)])
def test_read_only_caller_is_refused(path: str, body: dict) -> None:
    fake = _IaBridge()
    denied = _client(fake, claims=base_claims(u8co_read=True)).post(path, json=_auth(**body))
    assert denied.status_code == 403
    assert fake.calls == []


@pytest.mark.parametrize(
    ("path", "summary", "operation_id", "auth_ids"),
    [
        (_POST, "存货核算记账", "coIaPost", ("IA2004", "IA2005")),
        (_END, "存货核算期末处理", "coIaPeriodEnd", ("IA2006",)),
    ],
)
def test_is_in_openapi_as_write_with_dry_run_and_idempotency(
    path: str, summary: str, operation_id: str, auth_ids: tuple[str, ...]
) -> None:
    operation = _spec()["paths"][path]["post"]
    assert operation["summary"] == summary
    assert operation["operationId"] == operation_id
    assert operation["tags"] == ["存货核算"]
    assert operation["x-u8co-access"] == "write"
    assert operation["x-u8co-dry-run"] is True
    assert "权限：写" in operation["description"]
    for word in ("testAccounts", "全月平均", "接口暂不支持", "detail.counts", *auth_ids):
        assert word in operation["description"]
    names = {item.get("name") for item in operation.get("parameters", [])}
    assert "Idempotency-Key" in names


def test_ia_timeout_is_a_retryable_503_with_retry_after() -> None:
    body = {"ok": False, "code": "ia_timeout", "message": "存货核算处理超时，已回滚；可稍后重试，或调大 iaCommandSeconds"}
    error = to_api_error(503, body)
    assert (error.status, error.code, error.retry_after) == (503, "ia_timeout", 60)
    fake = _IaBridge()
    fake.error = error
    denied = _client(fake).post(_POST, json=_auth(**_POST_BODY))
    assert denied.status_code == 503
    assert denied.headers["Retry-After"] == "60"
    wire = denied.json()["error"]
    assert (wire["code"], wire["retryable"]) == ("ia_timeout", True)
    assert "iaCommandSeconds" in wire["hint"]
