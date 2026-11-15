"""应收冲应付 / 应付冲应收（arap/transfer）、并账（arap/merge）、红票对冲（arap/red_offset）：
转给桥的字段、请求校验、响应放行、预演、409 放行、审计和 OpenAPI。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line, base_claims, write_keys
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, WRITE
from u8co_api.co_models_arap_proc import CoArapMergeIn
from u8co_api.errors import ApiError

_TRANSFER = "/v1/co/arap/transfer"
_MERGE = "/v1/co/arap/merge"
_RED = "/v1/co/arap/red_offset"
_AR = {"type": "26", "id": "0000000001", "line_id": 11, "amount": 5}
_AP = {"type": "01", "id": "0000000002", "amount": 5}
_TRANSFER_BODY = {"flag": "AR", "customer": "C001", "vendor": "V001", "ar_lines": [_AR], "ap_lines": [_AP]}
_MERGE_BODY = {"flag": "AR", "from": "C001", "to": "C002", "lines": [{"type": "26", "id": "0000000001"}]}
_RED_BODY = {
    "flag": "AR",
    "partner": "C001",
    "red": [{"type": "26", "id": "0000000009", "amount": 10}],
    "blue": [{"type": "26", "id": "0000000001", "line_id": 11, "amount": 10}],
}


class _ProcBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "rollback", "action": path.rsplit("/", 1)[-1], "docs": []}
        row = {"type": "26", "id": "0000000001", "doc_id": 9, "line_id": 11, "amount": 5, "advance": False}
        if path == "/v1/arap/transfer":
            return {
                "ok": True,
                "flag": "AR",
                "style": "9I",
                "cancel_no": "YCFAP000000000001",
                "amount": 5,
                "ar_rows": [row],
                "ap_rows": [dict(row, type="01")],
                "bridge_note": "kept",
            }
        if path == "/v1/arap/merge":
            merged = dict(row, from_remaining=0, to_remaining=5)
            return {"ok": True, "cancel_no": "BZAR0000000000001", "from": "C001", "to": "C002", "rows": [merged]}
        return {"ok": True, "cancel_no": "HRAR0000000000001", "amount": 10, "red_rows": [row], "blue_rows": [row]}


@pytest.mark.parametrize(
    ("path", "body", "keys"),
    [
        (_TRANSFER, _TRANSFER_BODY, ("flag", "customer", "vendor", "ar_lines", "ap_lines")),
        (_MERGE, _MERGE_BODY, ("flag", "from", "to", "lines")),
        (_RED, _RED_BODY, ("flag", "partner", "red", "blue")),
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


def test_transfer_keeps_bridge_fields() -> None:
    fake = _ProcBridge()
    ok = _client(fake).post(_TRANSFER, json=_auth(digest="对冲", currency="人民币", **_TRANSFER_BODY))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == "/v1/arap/transfer"
    assert (sent["digest"], sent["currency"]) == ("对冲", "人民币")
    assert "line_id" not in sent["ap_lines"][0]
    body = ok.json()
    assert (body["cancel_no"], body["style"], body["bridge_note"]) == ("YCFAP000000000001", "9I", "kept")
    assert body["ar_rows"][0]["advance"] is False
    assert body["ap_rows"][0]["type"] == "01"


def test_merge_forwards_from_and_returns_it() -> None:
    fake = _ProcBridge()
    ok = _client(fake).post(_MERGE, json=_auth(**_MERGE_BODY))
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert (sent["from"], sent["to"]) == ("C001", "C002")
    assert "from_" not in sent
    assert "amount" not in sent["lines"][0]
    body = ok.json()
    assert (body["from"], body["to"]) == ("C001", "C002")
    assert body["rows"][0]["to_remaining"] == 5
    model = CoArapMergeIn.model_validate(_auth(**_MERGE_BODY))
    assert model.audit_ref() == "C001>C002"


def test_red_offset_returns_rows() -> None:
    ok = _client(_ProcBridge()).post(_RED, json=_auth(**_RED_BODY))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert (body["cancel_no"], body["amount"]) == ("HRAR0000000000001", 10)
    assert len(body["red_rows"]) == len(body["blue_rows"]) == 1


def _with(base: dict, **change) -> dict:
    body = dict(base)
    body.update(change)
    return _auth(**{key: value for key, value in body.items() if value is not None})


def _ar(**change) -> dict:
    return dict(_AR, **change)


_TRANSFER_REJECTS = (
    _with(_TRANSFER_BODY, flag="ar"),
    _with(_TRANSFER_BODY, customer=None),
    _with(_TRANSFER_BODY, customer=""),
    _with(_TRANSFER_BODY, customer=" "),
    _with(_TRANSFER_BODY, vendor="V" * 21),
    _with(_TRANSFER_BODY, currency="\x01"),
    _with(_TRANSFER_BODY, digest="x" * 121),
    _with(_TRANSFER_BODY, digest="a\nb"),
    _with(_TRANSFER_BODY, ar_lines=[]),
    _with(_TRANSFER_BODY, ar_lines=[_ar()] * 51),
    _with(_TRANSFER_BODY, ar_lines=[_ar(type="01")]),
    _with(_TRANSFER_BODY, ap_lines=[dict(_AP, type="26")]),
    _with(_TRANSFER_BODY, ar_lines=[_ar(amount=0)]),
    _with(_TRANSFER_BODY, ar_lines=[_ar(amount=1.234)], ap_lines=[dict(_AP, amount=1.234)]),
    _with(_TRANSFER_BODY, ar_lines=[_ar(amount="5")]),
    _with(_TRANSFER_BODY, ar_lines=[_ar(id=1)]),
    _with(_TRANSFER_BODY, ar_lines=[_ar(line_id=0)]),
    _with(_TRANSFER_BODY, ar_lines=[_ar(type="R0")]),
    _with(_TRANSFER_BODY, ar_lines=[_ar(memo="x")]),
    _with(_TRANSFER_BODY, ap_lines=[dict(_AP, amount=4)]),
    _with(_TRANSFER_BODY, ar_lines=[_ar(amount=2), _ar(amount=3)]),
    _with(_TRANSFER_BODY, ar_lines=[_ar(amount=2), _ar(line_id=None, amount=3)]),
    _with(_TRANSFER_BODY, date="2026/10/02"),
    _with(_TRANSFER_BODY, dry_run="yes"),
)


@pytest.mark.parametrize("body", _TRANSFER_REJECTS)
def test_transfer_rejects_bad_bodies_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_TRANSFER, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_transfer_accepts_split_lines_and_bills() -> None:
    fake = _ProcBridge()
    lines = [_ar(amount=2), _ar(line_id=12, amount=3), {"type": "R0", "id": "YS1", "amount": 1}]
    body = _with(_TRANSFER_BODY, ar_lines=lines, ap_lines=[dict(_AP, amount=6)])
    ok = _client(fake).post(_TRANSFER, json=body)
    assert ok.status_code == 200, ok.text
    assert len(fake.calls[-1][1]["ar_lines"]) == 3


@pytest.mark.parametrize("side", ["ar_lines", "ap_lines"])
def test_transfer_refuses_receipts_and_payments(side: str) -> None:
    fake = FakeBridge()
    line = {"type": "48" if side == "ar_lines" else "49", "id": "SK1", "line_id": 7, "amount": 5}
    denied = _client(fake).post(_TRANSFER, json=_with(_TRANSFER_BODY, **{side: [line]}))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


_MERGE_LINE = {"type": "26", "id": "0000000001"}
_MERGE_REJECTS = (
    _with(_MERGE_BODY, to="c001"),
    _with(_MERGE_BODY, to=" "),
    _with(_MERGE_BODY, **{"from": None}),
    _with(_MERGE_BODY, from_="C003"),
    _with(_MERGE_BODY, flag="AP"),
    _with(_MERGE_BODY, lines=[dict(_MERGE_LINE, type="48")]),
    _with(_MERGE_BODY, lines=[dict(_MERGE_LINE, type="R0", line_id=3)]),
    _with(_MERGE_BODY, lines=[_MERGE_LINE, dict(_MERGE_LINE, id="0000000001 ")]),
    _with(_MERGE_BODY, lines=[_MERGE_LINE, dict(_MERGE_LINE, line_id=3)]),
    _with(_MERGE_BODY, lines=[dict(_MERGE_LINE, amount=-1)]),
    _with(_MERGE_BODY, lines=[dict(_MERGE_LINE, id="x" * 31)]),
    _with(_MERGE_BODY, digest=" "),
    _with(_MERGE_BODY, lines=[]),
)


@pytest.mark.parametrize("body", _MERGE_REJECTS)
def test_merge_rejects_bad_bodies_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_MERGE, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def _red(**change) -> dict:
    return _with(_RED_BODY, **change)


_RED_REJECTS = (
    _red(partner=None),
    _red(red=[{"type": "48", "id": "SK1", "amount": 10}]),
    _red(flag="AP"),
    _red(blue=[{"type": "26", "id": "0000000001", "amount": 9}]),
    _red(blue=[{"type": "26", "id": "0000000009", "amount": 10}]),
    _red(red=[{"type": "R0", "id": "1", "line_id": 2, "amount": 10}]),
    _red(red=[]),
)


@pytest.mark.parametrize("body", _RED_REJECTS)
def test_red_offset_rejects_bad_bodies_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_RED, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


@pytest.mark.parametrize(("path", "body"), [(_TRANSFER, _TRANSFER_BODY), (_MERGE, _MERGE_BODY), (_RED, _RED_BODY)])
def test_dry_run_returns_the_preview_and_refuses_idempotency_key(path: str, body: dict) -> None:
    fake = _ProcBridge()
    ok = _client(fake).post(path, json=_auth(dry_run=True, **body))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    assert (ok.json()["dry_run"], ok.json()["mode"]) == (True, "rollback")
    denied = _client(fake).post(path, json=_auth(dry_run=True, **body), headers={"Idempotency-Key": "proc-1"})
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "dry_run"


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("state_mismatch", "转账日期所在期间应收已结账"),
        ("workflow_enabled", "销售发票 0000000001 受审批流控制，不能通过接口转账"),
        ("u8_rejected", "应收冲应付后核对不符，已回滚：余额不符"),
    ],
)
def test_bridge_conflict_passes_through(code: str, message: str) -> None:
    fake = _ProcBridge()
    fake.error = ApiError(409, code, message)
    denied = _client(fake).post(_TRANSFER, json=_auth(**_TRANSFER_BODY))
    assert denied.status_code == 409
    error = denied.json()["error"]
    assert (error["code"], error["message"]) == (code, message)


def test_audit_names_route_and_partners(capsys: pytest.CaptureFixture[str]) -> None:
    client = _client(_ProcBridge())
    assert client.post(_TRANSFER, json=_auth(**_TRANSFER_BODY)).status_code == 200
    out = capsys.readouterr().out
    assert audit_line(out, _TRANSFER)["action"] == "co:arap/transfer:transfer#C001/V001"
    assert _SECRET not in out
    assert client.post(_MERGE, json=_auth(**_MERGE_BODY)).status_code == 200
    assert audit_line(capsys.readouterr().out, _MERGE)["action"] == "co:arap/merge:merge#C001>C002"
    assert client.post(_RED, json=_auth(**_RED_BODY)).status_code == 200
    assert audit_line(capsys.readouterr().out, _RED)["action"] == "co:arap/red_offset:red_offset#C001"


@pytest.mark.parametrize(("path", "body"), [(_TRANSFER, _TRANSFER_BODY), (_MERGE, _MERGE_BODY), (_RED, _RED_BODY)])
def test_read_only_caller_is_refused(path: str, body: dict) -> None:
    fake = _ProcBridge()
    denied = _client(fake, claims=base_claims(u8co_read=True)).post(path, json=_auth(**body))
    assert denied.status_code == 403
    assert fake.calls == []


@pytest.mark.parametrize(
    ("path", "operation_id", "words"),
    [
        (_TRANSFER, "coArapTransfer", ("9I", "9J", "YCFAP", "FCYAR", "AR050502", "arap/process/cancel")),
        (_MERGE, "coArapMerge", ("BZ", "from", "AR050504", "arap/process/voucher")),
        (_RED, "coArapRedOffset", ("9N", "HRAR", "AP_JZ_Red", "AR050503")),
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
    assert "Idempotency-Key" in {item.get("name") for item in operation.get("parameters", [])}
    assert ACCESS["co:" + path.removeprefix("/v1/co/")] == WRITE


def test_merge_schema_names_the_from_field() -> None:
    schemas = _spec()["components"]["schemas"]
    assert "from" in schemas["CoArapMergeIn"]["properties"]
    assert "from_" not in schemas["CoArapMergeIn"]["properties"]
