"""应收应付期初单据 /v1/co/openings/arap：各 action 转发给桥的字段、请求校验、响应放行、预演、403 / 409 放行、
审计和 OpenAPI。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line, base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, WRITE
from u8co_api.co_models_openings import OpeningsArapIn
from u8co_api.errors import ApiError

_PATH = "/v1/co/openings/arap"
_BRIDGE = "/v1/openings/arap"
_CREATE = {"side": "ar", "action": "create", "partner": "C900001", "amount": 100.0, "account": "112201"}
_FULL = dict(
    _CREATE,
    side="ap",
    partner="S900001",
    amount=-300,
    account="220201",
    department="D901",
    person="op001",
    digest="期初应付",
    currency="美元",
    exch_rate=7.1,
)
_STATE = {"verified": True, "verifier": "张三", "verified_at": "2025-12-31"}


class _ArapBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            action = "opening_arap_" + payload["action"]
            return {"ok": True, "dry_run": True, "mode": "rollback", "action": action, "docs": [], "detail": {}}
        kind = "ar_bill" if payload["side"] == "ar" else "ap_bill"
        reply = {
            "ok": True,
            "type": kind,
            "id": payload.get("id", 41),
            "code": "0000000041",
            "side": payload["side"],
            "opening": True,
            "bridge_note": "kept",
        }
        if payload["action"] == "create":
            reply.update(start_date="2026-01-01", date="2025-12-31", state=dict(_STATE, verified=False))
        elif payload["action"] == "delete":
            reply["deleted"] = True
        else:
            reply["state"] = _STATE
        return reply


_SENT = (
    (_CREATE, _sealed("side", "action", "partner", "amount", "account")),
    (_FULL, _sealed(*_FULL)),
    ({"side": "ar", "action": "verify", "id": 41}, _sealed("side", "action", "id")),
    ({"side": "ap", "action": "unverify", "id": 41}, _sealed("side", "action", "id")),
    ({"side": "ar", "action": "delete", "id": 41}, _sealed("side", "action", "id")),
)


@pytest.mark.parametrize(("body", "keys"), _SENT)
def test_each_action_sends_exact_sealed_keys(body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PATH, json=_auth(**body))
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(sent) == keys | {"caller"}
    for name, value in body.items():
        assert sent[name] == value
    assert _SECRET not in raw.decode("utf-8")


def test_create_forwards_fields_and_keeps_bridge_fields() -> None:
    fake = _ArapBridge()
    ok = _client(fake).post(_PATH, json=_auth(**_CREATE))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert (sent["partner"], sent["amount"], sent["account"]) == ("C900001", 100.0, "112201")
    assert "dry_run" not in sent
    assert "id" not in sent
    body = ok.json()
    assert (body["type"], body["id"], body["code"]) == ("ar_bill", 41, "0000000041")
    assert (body["side"], body["opening"]) == ("ar", True)
    assert (body["start_date"], body["date"]) == ("2026-01-01", "2025-12-31")
    assert body["bridge_note"] == "kept"


@pytest.mark.parametrize("action", ["verify", "unverify"])
def test_verify_and_unverify_return_state(action: str) -> None:
    ok = _client(_ArapBridge()).post(_PATH, json=_auth(side="ap", action=action, id=41))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert (body["type"], body["side"], body["opening"]) == ("ap_bill", "ap", True)
    assert body["state"]["verified"] is True


def test_delete_reports_deleted() -> None:
    ok = _client(_ArapBridge()).post(_PATH, json=_auth(side="ar", action="delete", id=41))
    assert ok.status_code == 200, ok.text
    assert ok.json()["deleted"] is True


def test_dry_run_returns_the_preview() -> None:
    fake = _ArapBridge()
    ok = _client(fake).post(_PATH, json=_auth(dry_run=True, **_CREATE))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    body = ok.json()
    assert (body["dry_run"], body["mode"], body["action"]) == (True, "rollback", "opening_arap_create")


def test_dry_run_rejects_idempotency_key() -> None:
    fake = _ArapBridge()
    denied = _client(fake).post(_PATH, json=_auth(dry_run=True, **_CREATE), headers={"Idempotency-Key": "open-ar-1"})
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "dry_run"
    assert fake.calls == []


def test_idempotency_key_is_forwarded() -> None:
    fake = _ArapBridge()
    ok = _client(fake).post(_PATH, json=_auth(**_CREATE), headers={"Idempotency-Key": "open-ar-1"})
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["idempotency_key"] == "open-ar-1"


def _create(**change) -> dict:
    body = dict(_CREATE)
    body.update(change)
    return _auth(**{key: value for key, value in body.items() if value is not None})


_REJECTS = (
    _create(partner=None),
    _create(amount=None),
    _create(account=None),
    _create(id=41),
    _create(amount=0),
    _create(amount=1.234),
    _create(amount="100"),
    _create(amount=True),
    _create(amount=2000000000000),
    _create(partner=""),
    _create(partner="C" * 21),
    _create(exch_rate=0),
    _create(date_doc="2025-12-31"),
    _create(side="gl"),
    _create(side="AR"),
    _create(action="close"),
    _create(dry_run="yes"),
    _auth(side="ar", action="delete"),
    _auth(side="ar", action="verify", id=0),
    _auth(side="ar", action="verify", id="41"),
    _auth(side="ar", action="delete", id=41, partner="C900001"),
    _auth(side="ar", action="delete", id=41, amount=10),
    _auth(side="ap", action="unverify", id=41, account="220201"),
    _auth(side="ap", action="verify", id=41, exch_rate=1),
    _auth(action="verify", id=41),
    _auth(side="ar", id=41),
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
    [
        "应收款管理启用的第一个月已经结账，不能修改期初单据",
        "应付款管理启用的第一个月已经结账，不能修改期初单据",
        "不是期初单据",
        "单据已审核",
        "单据未审核",
        "单据已生成凭证，不能弃审",
        "单据已核销，不能删除",
    ],
)
def test_bridge_conflict_passes_through(message: str) -> None:
    fake = _ArapBridge()
    fake.error = ApiError(409, "state_mismatch", message)
    denied = _client(fake).post(_PATH, json=_auth(side="ar", action="unverify", id=41))
    assert denied.status_code == 409
    error = denied.json()["error"]
    assert (error["code"], error["message"]) == ("state_mismatch", message)


def test_not_found_passes_through() -> None:
    fake = _ArapBridge()
    fake.error = ApiError(404, "not_found", "单据不存在")
    denied = _client(fake).post(_PATH, json=_auth(side="ar", action="delete", id=41))
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "not_found"


def test_non_test_account_403_passes_through_with_hint() -> None:
    fake = _ArapBridge()
    fake.error = ApiError(403, "test_account_only", "期初单据只对配置为测试账套的账套开放")
    denied = _client(fake).post(_PATH, json=_auth(dry_run=True, **_CREATE))
    assert denied.status_code == 403
    error = denied.json()["error"]
    assert (error["code"], error["message"]) == ("test_account_only", "期初单据只对配置为测试账套的账套开放")
    assert error["retryable"] is False
    assert "testAccounts" in error["hint"]


def test_audit_names_action_and_side_or_id(capsys) -> None:
    client = _client(_ArapBridge())
    assert client.post(_PATH, json=_auth(**_CREATE)).status_code == 200
    assert audit_line(capsys.readouterr().out, _PATH)["action"] == "co:openings/arap:create#ar"
    assert client.post(_PATH, json=_auth(side="ap", action="verify", id=41)).status_code == 200
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert audit_line(out, _PATH)["action"] == "co:openings/arap:verify#41"


def test_model_and_access_table() -> None:
    assert ACCESS["co:openings/arap"] == WRITE
    body = OpeningsArapIn.model_validate(_auth(**_CREATE))
    assert body.audit_ref() == "ar"
    assert body.dry_run is False
    assert OpeningsArapIn.model_validate(_auth(**dict(_CREATE, amount=-5))).amount == -5


def test_read_only_caller_is_refused() -> None:
    fake = _ArapBridge()
    denied = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=_auth(**_CREATE))
    assert denied.status_code == 403
    assert fake.calls == []


def test_is_in_openapi_as_write_with_dry_run_and_idempotency() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "应收应付期初单据"
    assert operation["operationId"] == "coOpeningsArap"
    assert operation["tags"] == ["期初记账"]
    assert operation["x-u8co-access"] == "write"
    assert operation["x-u8co-dry-run"] is True
    assert "权限：写" in operation["description"]
    for word in ("AR0306", "AP0306", "testAccounts", "test_account_only", "第一个月已经结账"):
        assert word in operation["description"]
    names = {item.get("name") for item in operation.get("parameters", [])}
    assert "Idempotency-Key" in names
