"""期间损益结转 /v1/co/gl/transfer/pnl、自定义转账 /v1/co/gl/transfer/custom：请求校验、转发的字段、响应放行、
审计、写入分类和 OpenAPI。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.errors import ApiError
from u8co_api.write_class import RULES

_PNL = "/v1/co/gl/transfer/pnl"
_CUSTOM = "/v1/co/gl/transfer/custom"
_MONTH = {"fiscal_year": 2026, "period": 9}


class _TransferBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            detail = {"transfer": {"kind": "pnl", "vouchers": [{"sign": "转", "lines": [{"account": "6001", "debit": 800}]}]}}
            return {"ok": True, "dry_run": True, "mode": "validate", "action": "gl_transfer_pnl", "docs": [], "detail": detail}
        voucher = {"sign": "转", "period": 9, "no": 31, "lines": 2, "out_no": "GL0000000000031", "pack": "income"}
        return {
            "ok": True,
            "kind": "pnl" if path.endswith("pnl") else "custom",
            "fiscal_year": 2026,
            "period": 9,
            "voucher_date": "2026-09-30",
            "out_sign": "期间损益",
            "count": 1,
            "vouchers": [dict(voucher, bridge_extra="kept")],
            "skipped": [{"tran_id": "148", "account": "64019901", "reason": "损益科目不存在或不是末级科目"}],
        }


def test_pnl_forwards_exact_fields_and_keeps_bridge_fields() -> None:
    fake = _TransferBridge()
    ok = _client(fake).post(_PNL, json=_auth(**_MONTH))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == "/v1/gl/transfer/pnl"
    assert (sent["fiscal_year"], sent["period"]) == (2026, 9)
    assert "voucher_date" not in sent and "exclude_existing" not in sent and "tran_id" not in sent
    body = ok.json()
    assert body["vouchers"][0]["out_no"] == "GL0000000000031"
    assert body["vouchers"][0]["bridge_extra"] == "kept"
    assert body["skipped"][0]["account"] == "64019901"


def test_custom_forwards_tran_id_and_date() -> None:
    fake = _TransferBridge()
    ok = _client(fake).post(_CUSTOM, json=_auth(tran_id="0001", voucher_date="2026-09-30", **_MONTH))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == "/v1/gl/transfer/custom"
    assert sent["tran_id"] == "0001" and sent["voucher_date"] == "2026-09-30"


def test_sends_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PNL, json=_auth(**_MONTH))
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co/v1/gl/transfer/pnl")
    assert set(sent) == _sealed("fiscal_year", "period", "caller")
    assert _SECRET not in raw.decode("utf-8")


_REJECTS = (
    (_PNL, _auth(period=9)),
    (_PNL, _auth(fiscal_year=2026)),
    (_PNL, _auth(fiscal_year="2026", period=9)),
    (_PNL, _auth(fiscal_year=2026, period=13)),
    (_PNL, _auth(fiscal_year=2026, period=9, voucher_date="2026-10-01")),
    (_PNL, _auth(fiscal_year=2026, period=9, voucher_date="2026/09/30")),
    (_PNL, _auth(fiscal_year=2026, period=9, exclude_existing=True)),
    (_PNL, _auth(fiscal_year=2026, period=9, tran_id="0001")),
    (_CUSTOM, _auth(fiscal_year=2026, period=9, tran_id="00 1")),
    (_CUSTOM, _auth(fiscal_year=2026, period=9, tran_id="")),
    (_CUSTOM, _auth(fiscal_year=2026, period=9, lines=[])),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_exclude_existing_goes_with_dry_run() -> None:
    fake = _TransferBridge()
    ok = _client(fake).post(_PNL, json=_auth(dry_run=True, exclude_existing=True, **_MONTH))
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert sent["dry_run"] is True and sent["exclude_existing"] is True
    body = ok.json()
    assert body["mode"] == "validate"
    assert body["detail"]["transfer"]["vouchers"][0]["lines"][0]["account"] == "6001"


def test_audit_names_the_month(capsys) -> None:
    ok = _client(_TransferBridge()).post(_CUSTOM, json=_auth(tran_id="0002", **_MONTH))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert audit_line(out, _CUSTOM)["action"].endswith("#2026-9-0002")


@pytest.mark.parametrize(
    ("status", "code"),
    ((403, "test_account_only"), (403, "no_permission"), (409, "state_mismatch"), (504, "outcome_unknown")),
)
def test_bridge_errors_pass_through(status: int, code: str) -> None:
    fake = _TransferBridge()
    fake.error = ApiError(status, code, "期间损益结转只对配置为测试账套的账套开放")
    denied = _client(fake).post(_PNL, json=_auth(**_MONTH))
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_write_class_is_gl_voucher() -> None:
    assert RULES["gl/transfer/pnl"] == ("gl", "voucher")
    assert RULES["gl/transfer/custom"] == ("gl", "voucher")


@pytest.mark.parametrize(
    ("path", "summary", "operation"), ((_PNL, "期间损益结转", "coGlTransferPnl"), (_CUSTOM, "自定义转账", "coGlTransferCustom"))
)
def test_in_openapi_as_write(path: str, summary: str, operation: str) -> None:
    op = _spec()["paths"][path]["post"]
    assert op["summary"] == summary
    assert op["operationId"] == operation
    assert op["tags"] == ["总账凭证"]
    assert "权限：写" in op["description"]
    assert "test_account_only" in op["description"]
    assert "exclude_existing" in op["description"]


def test_describes_data_permission_and_completion() -> None:
    pnl = _spec()["paths"][_PNL]["post"]["description"]
    custom = _spec()["paths"][_CUSTOM]["post"]["description"]
    assert "no_permission" in pnl and "no_permission" in custom
    assert "existing" in pnl
    assert "逐个生成" in custom


def test_existing_vouchers_pass_through() -> None:
    fake = _TransferBridge()
    original = fake.call

    def call(path: str, payload: dict) -> dict:
        out = original(path, payload)
        out["existing"] = [{"sign": "转", "no": 30, "pack": "income"}]
        return out

    fake.call = call
    ok = _client(fake).post(_PNL, json=_auth(**_MONTH))
    assert ok.status_code == 200, ok.text
    assert ok.json()["existing"] == [{"sign": "转", "no": 30, "pack": "income"}]
