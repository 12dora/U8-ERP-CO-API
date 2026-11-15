"""取消处理（arap/process/cancel）、处理制单（arap/process/voucher）、汇兑损益与取消（arap/exchange_gain、
arap/exchange_gain/cancel，只对测试账套开放）：转给桥的字段、请求校验、响应放行、403 / 409 放行、审计和 OpenAPI。
另有模型层的校验文案（API 的 400 只给字段路径，文案与桥一致是为了文档和日志）。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.support import audit_line, write_keys
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_models_arap_batch import CoArapExGainIn, CoArapProcVoucherIn
from u8co_api.co_models_arap_proc import TRANSFER_NO_RECEIPT, CoArapTransferIn
from u8co_api.errors import ApiError

_CANCEL = "/v1/co/arap/process/cancel"
_VOUCHER = "/v1/co/arap/process/voucher"
_GAIN = "/v1/co/arap/exchange_gain"
_GAIN_CANCEL = "/v1/co/arap/exchange_gain/cancel"
_YCF = "YCFAP000000000001"
_SY = "SYRAR0000000000001"


class _BatchBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "rollback", "action": path.rsplit("/", 1)[-1], "docs": []}
        if path == "/v1/arap/process/cancel":
            item = {"ledger": "AR", "type": "sale_invoice", "id": 9, "code": "0000000001", "debit": 0, "credit": 5}
            back = {"ledger": "AR", "type": "ar_bill", "id": 3, "code": "YS1", "amount": 5, "remaining": 5}
            return {
                "ok": True,
                "cancel_no": payload["cancel_no"],
                "style": "9I",
                "kind": "transfer",
                "ar_rows": 1,
                "ap_rows": 1,
                "items": [item],
                "restored": [back],
            }
        if path == "/v1/arap/process/voucher":
            voucher = {"year": 2026, "period": 10, "sign": "转", "no": 12, "num": "转-0012", "date": "2026-10-02"}
            line = {
                "entry": 1,
                "account": "220201",
                "digest": "应收冲应付",
                "debit": 5,
                "credit": 0,
                "supplier": "V001",
            }
            return {
                "ok": True,
                "flag": payload["flag"],
                "proc_style": "9I",
                "out_sign": "ZZ",
                "cancel_nos": payload["cancel_nos"],
                "rows": 2,
                "pz_id": "AR0000000000012",
                "voucher": voucher,
                "lines": [line, dict(line, entry=2, account="112201", debit=0, credit=5, memo="x")],
            }
        batch = {"cancel_no": _SY, "partner": "C001", "type": "26", "id": "0000000001", "lines": 1, "diff": 1.5}
        return {"ok": True, "flag": payload["flag"], "rate": 7.1, "rows": 1, "batches": [batch], "total": 1.5}


@pytest.mark.parametrize(
    ("path", "body", "keys"),
    [
        (_CANCEL, {"flag": "AR", "cancel_no": _YCF}, ("flag", "cancel_no")),
        (
            _VOUCHER,
            {"flag": "AR", "cancel_nos": [_YCF], "sign": "转", "voucher_date": "2026-10-02", "digest": "冲"},
            ("flag", "cancel_nos", "sign", "voucher_date", "digest"),
        ),
        (_VOUCHER, {"flag": "AR", "cancel_nos": [_SY], "pl_code": "660399"}, ("flag", "cancel_nos", "pl_code")),
        (
            _GAIN,
            {"flag": "AR", "currency": "美元", "rate": 7.1, "partners": ["C001"], "settle_cleared": False},
            ("flag", "currency", "rate", "partners", "settle_cleared"),
        ),
        (_GAIN_CANCEL, {"flag": "AR", "cancel_nos": [_SY]}, ("flag", "cancel_nos")),
        (_GAIN_CANCEL, {"flag": "AP"}, ("flag",)),
    ],
)
def test_routes_send_exact_sealed_keys(path: str, body: dict, keys: tuple[str, ...]) -> None:
    with _Up() as server:
        response = _wired(server.base_url).post(path, json=_auth(**body))
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + path.replace("/v1/co/", "/v1/"))
    assert set(sent) == write_keys(path, _sealed(*keys))
    for name in keys:
        assert sent[name] == body[name], name
    assert _SECRET not in raw.decode("utf-8")


def test_cancel_returns_items_and_restored() -> None:
    ok = _client(_BatchBridge()).post(_CANCEL, json=_auth(flag="AR", cancel_no=_YCF))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert (body["cancel_no"], body["style"], body["ar_rows"], body["ap_rows"]) == (_YCF, "9I", 1, 1)
    assert body["items"][0]["credit"] == 5
    assert body["restored"][0]["remaining"] == 5


def test_voucher_returns_the_voucher_and_keeps_extras() -> None:
    ok = _client(_BatchBridge()).post(_VOUCHER, json=_auth(flag="AR", cancel_nos=[_YCF, "YCFAP000000900002"]))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert (body["proc_style"], body["out_sign"], body["pz_id"]) == ("9I", "ZZ", "AR0000000000012")
    assert body["voucher"]["num"] == "转-0012"
    assert [line["entry"] for line in body["lines"]] == [1, 2]
    assert body["lines"][1]["memo"] == "x"
    projected = _client(_BatchBridge()).post(_VOUCHER + "?fields=account", json=_auth(flag="AR", cancel_nos=[_YCF]))
    assert projected.json()["lines"] == [{"account": "220201"}, {"account": "112201"}]


def test_exchange_gain_returns_batches() -> None:
    ok = _client(_BatchBridge()).post(_GAIN, json=_auth(flag="AR", currency="美元"))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert (body["rate"], body["total"]) == (7.1, 1.5)
    assert body["batches"][0]["cancel_no"] == _SY


_REJECTS = (
    (_CANCEL, {"flag": "AP", "cancel_no": _YCF}),
    (_CANCEL, {"flag": "AR", "cancel_no": "FCYAR000000000001"}),
    (_CANCEL, {"flag": "AP", "cancel_no": "BZAR0000000000001"}),
    (_CANCEL, {"flag": "AR", "cancel_no": "HXAR0000000000001"}),
    (_CANCEL, {"flag": "AR", "cancel_no": _SY}),
    (_CANCEL, {"flag": "AR", "cancel_no": "YCFAP"}),
    (_CANCEL, {"flag": "AR", "cancel_no": "YCFAP" + "1" * 21}),
    (_CANCEL, {"flag": "AR"}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": []}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": [f"YCFAP{n:012d}" for n in range(51)]}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": [_YCF, _YCF]}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": [_YCF, "BZAR0000000000001"]}),
    (_VOUCHER, {"flag": "AP", "cancel_nos": [_YCF]}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": ["HXAR0000000000001"]}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": [_SY]}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": [_YCF], "pl_code": "660399"}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": [_SY], "pl_code": "6604 04"}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": [_YCF], "sign": "转账凭"}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": [_YCF], "voucher_date": "2026/10/02"}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": [_YCF], "digest": "x" * 121}),
    (_VOUCHER, {"flag": "AR", "cancel_nos": [_YCF], "digest": "a\tb"}),
    (_GAIN, {"flag": "AR"}),
    (_GAIN, {"flag": "AR", "currency": " "}),
    (_GAIN, {"flag": "AR", "currency": "美元", "rate": 0}),
    (_GAIN, {"flag": "AR", "currency": "美元", "rate": "7.1"}),
    (_GAIN, {"flag": "AR", "currency": "美元", "rate": 7.10000000001}),
    (_GAIN, {"flag": "AR", "currency": "美元", "settle_cleared": "yes"}),
    (_GAIN, {"flag": "AR", "currency": "美元", "partners": []}),
    (_GAIN, {"flag": "AR", "currency": "美元", "partners": ["C1", "C1 "]}),
    (_GAIN, {"flag": "AR", "currency": "美元", "partners": ["C1", 5]}),
    (_GAIN, {"flag": "AR", "currency": "美元", "cancel_nos": [_SY]}),
    (_GAIN_CANCEL, {"flag": "AR", "cancel_nos": []}),
    (_GAIN_CANCEL, {"flag": "AR", "cancel_nos": ["SYPAP0000000000001"]}),
    (_GAIN_CANCEL, {"flag": "AR", "cancel_nos": ["HXAR0000000000001"]}),
    (_GAIN_CANCEL, {"flag": "AR", "cancel_nos": [_SY, _SY]}),
    (_GAIN_CANCEL, {"flag": "AR", "currency": "美元"}),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_bad_bodies_are_400_before_the_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=_auth(**body))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (_CANCEL, {"flag": "AR", "cancel_no": "HRAR0000000000001"}),
        (_CANCEL, {"flag": "AP", "cancel_no": "BZAP0000000000001"}),
        (_VOUCHER, {"flag": "AP", "cancel_nos": ["HPAP0000000000001", "HPAP0000000000002"]}),
        (_VOUCHER, {"flag": "AP", "cancel_nos": ["FCYAR000000000001"]}),
    ],
)
def test_red_offset_and_other_batches_are_accepted(path: str, body: dict) -> None:
    fake = _BatchBridge()
    assert _client(fake).post(path, json=_auth(**body)).status_code == 200
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (_GAIN, {"flag": "AR", "currency": "美元"}),
        (_GAIN_CANCEL, {"flag": "AR", "cancel_nos": [_SY]}),
        (_VOUCHER, {"flag": "AR", "cancel_nos": [_SY], "pl_code": "660399"}),
    ],
)
def test_non_test_account_403_passes_through(path: str, body: dict) -> None:
    fake = _BatchBridge()
    fake.error = ApiError(403, "test_account_only", "汇兑损益只对配置为测试账套的账套开放")
    denied = _client(fake).post(path, json=_auth(dry_run=True, **body))
    assert denied.status_code == 403
    error = denied.json()["error"]
    assert (error["code"], error["message"]) == ("test_account_only", "汇兑损益只对配置为测试账套的账套开放")
    assert error["retryable"] is False
    assert "汇兑" in error["hint"]


@pytest.mark.parametrize(
    ("code", "message"),
    [("state_mismatch", "已制单，请先取消制单"), ("not_found", "处理号不存在"), ("u8_rejected", "核对不符，已回滚")],
)
def test_cancel_refusals_pass_through(code: str, message: str) -> None:
    fake = _BatchBridge()
    fake.error = ApiError(404 if code == "not_found" else 409, code, message)
    denied = _client(fake).post(_CANCEL, json=_auth(flag="AR", cancel_no=_YCF))
    assert denied.json()["error"]["message"] == message
    assert denied.json()["error"]["code"] == code


def test_dry_run_returns_the_preview() -> None:
    fake = _BatchBridge()
    ok = _client(fake).post(_GAIN_CANCEL, json=_auth(flag="AR", dry_run=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    assert ok.json()["mode"] == "rollback"


def test_audit_names_the_batches(capsys: pytest.CaptureFixture[str]) -> None:
    client = _client(_BatchBridge())
    assert client.post(_CANCEL, json=_auth(flag="AR", cancel_no=_YCF)).status_code == 200
    assert audit_line(capsys.readouterr().out, _CANCEL)["action"] == f"co:arap/process/cancel:cancel#{_YCF}"
    nos = [_YCF, "YCFAP000000900002", "YCFAP000000900003"]
    assert client.post(_VOUCHER, json=_auth(flag="AR", cancel_nos=nos)).status_code == 200
    assert audit_line(capsys.readouterr().out, _VOUCHER)["action"] == f"co:arap/process/voucher:voucher#{_YCF}+2"
    assert client.post(_GAIN, json=_auth(flag="AR", currency="美元")).status_code == 200
    assert audit_line(capsys.readouterr().out, _GAIN)["action"] == "co:arap/exchange_gain:exchange_gain#美元"
    assert client.post(_GAIN_CANCEL, json=_auth(flag="AR")).status_code == 200
    out = capsys.readouterr().out
    assert audit_line(out, _GAIN_CANCEL)["action"] == "co:arap/exchange_gain/cancel:cancel#date:2026-09-27"
    assert _SECRET not in out


def test_model_messages_match_the_bridge() -> None:
    with pytest.raises(ValidationError, match="汇兑损益制单必须给 pl_code"):
        CoArapProcVoucherIn.model_validate(_auth(flag="AR", cancel_nos=[_SY]))
    with pytest.raises(ValidationError, match="一次只能给同一类处理的批次号"):
        CoArapProcVoucherIn.model_validate(_auth(flag="AR", cancel_nos=[_YCF, "BZAR0000000000001"]))
    with pytest.raises(ValidationError, match="partners 里有重复的编码"):
        CoArapExGainIn.model_validate(_auth(flag="AR", currency="美元", partners=["C1", "C1"]))
    line = {"type": "48", "id": "SK1", "amount": 5}
    with pytest.raises(ValidationError, match=TRANSFER_NO_RECEIPT):
        CoArapTransferIn.model_validate(
            _auth(flag="AR", customer="C001", vendor="V001", ar_lines=[line], ap_lines=[dict(line, type="01")])
        )


@pytest.mark.parametrize(
    ("path", "operation_id", "words"),
    [
        (_CANCEL, "coArapProcessCancel", ("YCFAP", "FCYAR", "BZAR", "HRAR", "已制单，请先取消制单", "AR0807")),
        (_VOUCHER, "coArapProcessVoucher", ("U8PzInsert", "pl_code", "test_account_only", "AR0508", "validate")),
        (_GAIN, "coArapExchangeGain", ("9M", "testAccounts", "test_account_only", "本期没有调整汇率", "AR0507")),
        (_GAIN_CANCEL, "coArapExchangeGainCancel", ("testAccounts", "by_date", "AR0807")),
    ],
)
def test_routes_are_in_openapi_as_writes(path: str, operation_id: str, words: tuple[str, ...]) -> None:
    operation = _spec()["paths"][path]["post"]
    assert operation["operationId"] == operation_id
    assert operation["tags"] == ["应收应付处理"]
    assert operation["x-u8co-access"] == "write"
    assert operation["x-u8co-dry-run"] is True
    assert "权限：写" in operation["description"]
    for word in words:
        assert word in operation["description"], word
