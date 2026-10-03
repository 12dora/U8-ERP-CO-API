"""票据处理（/v1/co/notes/process）与取消处理、处理制单的票据批次：
转发字段、提前拒绝、写权限、响应放行、审计和 OpenAPI。
应付票据（flag AP）只收结算、退回（PJJAP / CLAP），贴现、背书和 PJTAP / PJBAP 在 API 层 400。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.support import audit_line, base_claims, write_keys
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, WRITE
from u8co_api.co_models_arap_batch import CoArapProcVoucherIn
from u8co_api.co_models_notes_proc import CoNoteProcIn

_PROC = "/v1/co/notes/process"
_CANCEL = "/v1/co/arap/process/cancel"
_VOUCHER = "/v1/co/arap/process/voucher"
_LINE = {"type": "P0", "id": "YF1", "amount": 800}
_SETTLE = {"flag": "AR", "op": "settle", "note": "N2026001", "bank_code": "100201"}
_DISCOUNT = {
    "flag": "AR",
    "op": "discount",
    "note": 3416,
    "amount": 1000,
    "bank_code": "100201",
    "bank_name": "示例银行",
    "expense": 12.5,
    "interest": 0,
    "rate": 2.35,
}
_ENDORSE = {"flag": "AR", "op": "endorse", "note": "N2026001", "amount": 800, "vendor": "V001", "ap_lines": [_LINE]}
_RETURN = {"flag": "AR", "op": "return", "note": "N2026001"}


def _with(base: dict, **change) -> dict:
    body = dict(base)
    body.update(change)
    return {k: v for k, v in body.items() if v is not None}


_FORWARD = (
    (_PROC, _SETTLE),
    (_PROC, _with(_SETTLE, sub_start=1, sub_end=100, amount=1, digest="托收")),
    (_PROC, _DISCOUNT),
    (_PROC, _ENDORSE),
    (_PROC, _RETURN),
    (_PROC, _with(_RETURN, sub_start=1, sub_end=10000000, amount=100000, digest="退回")),
    (_CANCEL, {"flag": "AR", "cancel_no": "PJTAR000000000001"}),
    (_CANCEL, {"flag": "AR", "cancel_no": "CLAR0000000000001"}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": ["PJTAR000000000001"], "expense_code": "660301"}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": ["PJBAR000000000001", "PJBAR000000000002"]}),
    (_PROC, _with(_SETTLE, flag="AP")),
    (_PROC, _with(_RETURN, flag="AP", sub_start=1, sub_end=100)),
    (_CANCEL, {"flag": "AP", "cancel_no": "CLAP0000000000001"}),
    (_CANCEL, {"flag": "AP", "cancel_no": "PJJAP000000000001"}),
    (_VOUCHER, {"flag": "AP", "cancel_nos": ["PJJAP000000000001", "PJJAP000000000002"]}),
    (_VOUCHER, {"flag": "AP", "cancel_nos": ["CLAP0000000000001"]}),
)

_REJECTS = (
    (_PROC, _with(_DISCOUNT, flag="AP")),
    (_PROC, _with(_ENDORSE, flag="AP")),
    (_PROC, _with(_SETTLE, op="transfer")),
    (_PROC, _with(_SETTLE, op="return")),
    (_PROC, _with(_RETURN, vendor="V001")),
    (_PROC, _with(_RETURN, expense=1)),
    (_PROC, _with(_RETURN, ap_lines=[_LINE])),
    (_PROC, _with(_SETTLE, note=0)),
    (_PROC, _with(_SETTLE, note=2147483648)),
    (_PROC, _with(_SETTLE, note=1.0)),
    (_PROC, _with(_SETTLE, note=True)),
    (_PROC, _with(_SETTLE, note=" ")),
    (_PROC, _with(_SETTLE, note="a\nb")),
    (_PROC, _with(_SETTLE, bank_code=None)),
    (_PROC, _with(_SETTLE, bank_name="")),
    (_PROC, _with(_SETTLE, amount=0)),
    (_PROC, _with(_SETTLE, amount=1.005)),
    (_PROC, _with(_SETTLE, amount="1")),
    (_PROC, _with(_SETTLE, expense=1)),
    (_PROC, _with(_SETTLE, vendor="V001")),
    (_PROC, _with(_SETTLE, sub_start=1)),
    (_PROC, _with(_SETTLE, sub_start=5, sub_end=4)),
    (_PROC, _with(_SETTLE, sub_start=1, sub_end=100, amount=2)),
    (_PROC, _with(_SETTLE, sub_start=0, sub_end=4)),
    (_PROC, _with(_SETTLE, digest="x" * 121)),
    (_PROC, _with(_SETTLE, extra=1)),
    (_PROC, _with(_DISCOUNT, rate=101)),
    (_PROC, _with(_DISCOUNT, rate=1.0000001)),
    (_PROC, _with(_DISCOUNT, expense=-1)),
    (_PROC, _with(_DISCOUNT, interest=0.001)),
    (_PROC, _with(_ENDORSE, bank_code="100201")),
    (_PROC, _with(_ENDORSE, vendor=None)),
    (_PROC, _with(_ENDORSE, ap_lines=None)),
    (_PROC, _with(_ENDORSE, ap_lines=[])),
    (_PROC, _with(_ENDORSE, ap_lines=[dict(_LINE, type="49")])),
    (_PROC, _with(_ENDORSE, ap_lines=[dict(_LINE, type="26")])),
    (_PROC, _with(_ENDORSE, ap_lines=[_LINE, _LINE])),
    (_PROC, _with(_ENDORSE, amount=700)),
    (_PROC, _with(_ENDORSE, amount=None, sub_start=1, sub_end=100)),
    (_CANCEL, {"flag": "AR", "cancel_no": "PJJAP000000000001"}),
    (_CANCEL, {"flag": "AP", "cancel_no": "PJJAR000000000001"}),
    (_CANCEL, {"flag": "AR", "cancel_no": "CLAP0000000000001"}),
    (_CANCEL, {"flag": "AP", "cancel_no": "PJTAP000000000001"}),
    (_CANCEL, {"flag": "AP", "cancel_no": "PJBAP000000000001"}),
    (_VOUCHER, {"flag": "AP", "cancel_nos": ["PJTAP000000000001"]}),
    (_VOUCHER, {"flag": "AP", "cancel_nos": ["PJBAP000000000001"]}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": ["CLAP0000000000001"]}),
    (_VOUCHER, {"flag": "AP", "cancel_nos": ["PJJAR000000000001"]}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": ["PJJAR000000000001", "PJTAR000000000001"]}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": ["PJJAR000000000001"], "expense_code": "660301"}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": ["PJTAR000000000001"], "expense_code": "6603 01"}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": ["PJTAR000000000001"], "expense_code": "x" * 41}),
)


@pytest.mark.parametrize(("path", "body"), _FORWARD)
def test_notes_proc_forwards_exact_sealed_keys(path: str, body: dict) -> None:
    with _Up() as server:
        response = _wired(server.base_url).post(path, json=_auth(**body))
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + path.replace("/v1/co/", "/v1/"))
    assert set(sent) == write_keys(path, _sealed(*body))
    for name, value in body.items():
        assert sent[name] == value, name
    assert _SECRET.encode() not in raw


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_notes_proc_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=_auth(**body))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _ProcBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        row = {"type": "P0", "id": "YF1", "doc_id": 77, "line_id": None, "amount": 800, "remaining": 0}
        return {
            "ok": True,
            "acc": "803",
            "flag": "AR",
            "op": payload["op"],
            "style": "9E",
            "cancel_no": "PJBAR000000000001",
            "date": "2026-09-27",
            "note": {"id": 3416, "code": "N2026001", "partner": "C001", "partner_name": "示例客户", "x": 1},
            "amount": 800,
            "remaining": 200,
            "sub_start": None,
            "sub_end": None,
            "vendor": "V001",
            "ap_rows": [row],
            "extra": 1,
        }


def test_notes_proc_response_keeps_bridge_fields() -> None:
    body = _client(_ProcBridge()).post(_PROC, json=_auth(**_ENDORSE)).json()
    assert (body["cancel_no"], body["style"], body["remaining"]) == ("PJBAR000000000001", "9E", 200)
    assert body["note"]["partner_name"] == "示例客户"
    assert body["note"]["x"] == 1
    assert body["ap_rows"][0]["doc_id"] == 77
    assert body["extra"] == 1


class _ReturnBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        note = {"id": 3416, "code": "N2026001", "partner": "C001", "partner_name": "示例客户"}
        return {
            "ok": True,
            "flag": "AR",
            "op": "return",
            "style": "9C",
            "cancel_no": "CLAR0000000000001",
            "note": note,
            "amount": 100000,
            "remaining": 0,
            "digest": "退回示例客户电子承兑",
            "r0_id": 1000000021,
            "r0_code": "YS202609160001",
        }


def test_notes_proc_return_keeps_bill_fields() -> None:
    body = _client(_ReturnBridge()).post(_PROC, json=_auth(**_RETURN)).json()
    assert (body["cancel_no"], body["style"], body["remaining"]) == ("CLAR0000000000001", "9C", 0)
    assert (body["r0_id"], body["r0_code"]) == (1000000021, "YS202609160001")


def test_notes_proc_needs_write() -> None:
    assert ACCESS["co:notes/process"] == WRITE
    fake = FakeBridge()
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(_PROC, json=_auth(**_SETTLE))
    assert reader.status_code == 403, reader.text
    assert fake.calls == []


def test_notes_proc_audit_names_op_and_note(capsys: pytest.CaptureFixture[str]) -> None:
    assert _client(_ProcBridge()).post(_PROC, json=_auth(**_ENDORSE)).status_code == 200
    out = capsys.readouterr().out
    assert audit_line(out, _PROC)["action"].endswith("#endorse:N2026001")
    assert _SECRET not in out


def test_notes_proc_dry_run_is_forwarded() -> None:
    fake = FakeBridge()
    _client(fake).post(_PROC, json=_auth(dry_run=True, **_SETTLE))
    assert fake.calls[-1][1]["dry_run"] is True


def test_model_messages_match_the_bridge() -> None:
    with pytest.raises(ValidationError, match="背书不支持冲付款单（49）"):
        CoNoteProcIn.model_validate(_auth(**_with(_ENDORSE, ap_lines=[dict(_LINE, type="49")])))
    with pytest.raises(ValidationError, match="ap_lines 的金额合计必须等于背书金额 amount"):
        CoNoteProcIn.model_validate(_auth(**_with(_ENDORSE, amount=700)))
    with pytest.raises(ValidationError, match="expense 只用于贴现"):
        CoNoteProcIn.model_validate(_auth(**_with(_SETTLE, expense=1)))
    with pytest.raises(ValidationError, match="bank_code 只用于结算、贴现"):
        CoNoteProcIn.model_validate(_auth(**_with(_RETURN, bank_code="100201")))
    with pytest.raises(ValidationError, match="应付票据只支持结算、退回"):
        CoArapProcVoucherIn.model_validate(_auth(flag="AP", cancel_nos=["PJTAP000000000001"]))
    with pytest.raises(ValidationError, match="应付票据只支持结算（settle）、退回（return）"):
        CoNoteProcIn.model_validate(_auth(**_with(_DISCOUNT, flag="AP")))
    with pytest.raises(ValidationError, match="expense_code 只用于票据贴现"):
        CoArapProcVoucherIn.model_validate(_auth(flag="AR", cancel_nos=["PJJAR000000000001"], expense_code="660301"))


def test_notes_proc_openapi() -> None:
    spec = _spec()
    operation = spec["paths"][_PROC]["post"]
    assert operation["operationId"] == "coNoteProcess"
    assert operation["summary"] == "票据处理"
    assert operation["tags"] == ["应收应付处理"]
    assert operation["x-u8co-access"] == "write"
    for word in ("PJJAR", "PJTAR", "PJBAR", "CLAR", "r0_code", "AR0504", "rollback", "expense_code", "PJJAP", "p0_code"):
        assert word in operation["description"], word
    voucher = spec["paths"][_VOUCHER]["post"]
    assert "expense_code" in CoArapProcVoucherIn.model_json_schema()["properties"]
    assert "PJTAR" in voucher["description"]
    assert "PJJAR" in spec["paths"][_CANCEL]["post"]["description"]
    assert "CLAP" in spec["paths"][_CANCEL]["post"]["description"]
    assert "test_account_only" in voucher["description"]
