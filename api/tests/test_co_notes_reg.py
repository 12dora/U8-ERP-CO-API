"""应收票据登记、删除（含应付票据 flag AP）：/v1/co/notes/create、/v1/co/notes/delete 的转发字段、提前拒绝、
写权限、响应放行和 OpenAPI。"""

from __future__ import annotations

import json

import pytest
from tests.support import base_claims, write_keys
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, WRITE

_CREATE = "/v1/co/notes/create"
_DELETE = "/v1/co/notes/delete"
_NOTE = {
    "flag": "AR",
    "note_no": "N2026001",
    "settle_code": "301",
    "amount": 1000.5,
    "sign_date": "2026-08-01",
    "receipt_date": "2026-08-10",
    "expire_date": "2027-02-01",
    "customer": "C001",
    "dept": "D01",
    "receiver": "示例科技有限公司",
}
_REQUIRED = tuple(_NOTE)


def _note(**extra) -> dict:
    body = dict(_NOTE)
    body.update(extra)
    return _auth(**body)


_FORWARD = (
    (_CREATE, _note(), _sealed(*_REQUIRED)),
    (_CREATE, _note(person="P01", km="112201", digest="收到承兑"), _sealed(*_REQUIRED, "person", "km", "digest")),
    (_CREATE, _note(amount=100, sub_start=1, sub_end=10000), _sealed(*_REQUIRED, "sub_start", "sub_end")),
    (_CREATE, _note(sub_start=999999999899950, sub_end=999999999999999), _sealed(*_REQUIRED, "sub_start", "sub_end")),
    (_DELETE, _auth(flag="AR", note_no="N1"), _sealed("flag", "note_no")),
    (_DELETE, _auth(flag="AR", id=3416), _sealed("flag", "id")),
)

_REJECTS = (
    (_CREATE, _note(flag="AP")),
    (_CREATE, _note(flag="AP", vendor="V001")),
    (_CREATE, _note(vendor="V001")),
    (_CREATE, {k: v for k, v in _note().items() if k != "customer"}),
    (_CREATE, _note(note_km=" ")),
    (_CREATE, _note(flag="ap")),
    (_CREATE, _note(amount=0)),
    (_CREATE, _note(amount=1.005)),
    (_CREATE, _note(amount="1")),
    (_CREATE, _note(sign_date="2026/08/01")),
    (_CREATE, _note(sign_date="2026-02-30")),
    (_CREATE, _note(expire_date="2026-07-01")),
    (_CREATE, _note(receipt_date="2026-07-30")),
    (_CREATE, _note(receiver=" ")),
    (_CREATE, _note(digest="a\nb")),
    (_CREATE, _note(note_no="x" * 61)),
    (_CREATE, _note(extra=1)),
    (_CREATE, {k: v for k, v in _note().items() if k != "dept"}),
    (_CREATE, _note(amount=100, sub_start=1)),
    (_CREATE, _note(amount=100, sub_end=10000)),
    (_CREATE, _note(amount=100, sub_start=1, sub_end=9999)),
    (_CREATE, _note(amount=100, sub_start=2, sub_end=10000)),
    (_CREATE, _note(amount=0.01, sub_start=2, sub_end=1)),
    (_CREATE, _note(amount=0.01, sub_start=0, sub_end=0)),
    (_CREATE, _note(amount=0.01, sub_start="1", sub_end="1")),
    (_CREATE, _note(amount=0.01, sub_start=1.0, sub_end=1)),
    (_CREATE, _note(amount=0.01, sub_start=1000000000000000, sub_end=1000000000000000)),
    (_DELETE, _auth(flag="AR")),
    (_DELETE, _auth(flag="AR", note_no="N1", id=1)),
    (_DELETE, _auth(flag="AR", id=0)),
    (_DELETE, _auth(flag="AR", id="1")),
    (_DELETE, _auth(flag="XX", id=1)),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_notes_reg_forwards_exact_sealed_keys(path: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + path.replace("/v1/co/", "/v1/")
    assert set(sent) == write_keys(path, keys)
    assert _SECRET.encode() not in raw
    assert sent["flag"] == "AR"


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_notes_reg_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _RegBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        return {
            "ok": True,
            "acc": "803",
            "flag": "AR",
            "note_id": 3500,
            "note_no": "N2026001",
            "receipt_id": 1000000022,
            "receipt_code": "SK0001",
            "receipt_verified": False,
            "extra": 1,
        }


def test_notes_create_response_keeps_bridge_fields() -> None:
    body = _client(_RegBridge()).post(_CREATE, json=_note()).json()
    assert (body["note_id"], body["receipt_code"], body["receipt_verified"]) == (3500, "SK0001", False)
    assert body["extra"] == 1


class _SplitBridge(_RegBridge):
    def call(self, path: str, payload: dict) -> dict:
        body = super().call(path, payload)
        body.update(sub_start=payload["sub_start"], sub_end=payload["sub_end"])
        return body


def test_notes_create_split_round_trip() -> None:
    fake = _SplitBridge()
    body = _client(fake).post(_CREATE, json=_note(amount=100, sub_start=1, sub_end=10000)).json()
    assert (body["sub_start"], body["sub_end"]) == (1, 10000)
    assert (fake.calls[-1][1]["sub_start"], fake.calls[-1][1]["sub_end"]) == (1, 10000)


def test_notes_create_split_mismatch_message() -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_CREATE, json=_note(amount=100, sub_start=1, sub_end=9999))
    assert denied.status_code == 400
    error = denied.json()["error"]
    assert error["code"] == "bad_request"
    assert error["field"] == "sub_end"
    assert error["message"] == "子票区间与金额不符：区间 9999 张 = 99.99 元，票面 100.00（每张子票 0.01 元）"
    assert fake.calls == []


@pytest.mark.parametrize("path", [_CREATE, _DELETE])
def test_notes_reg_needs_write(path: str) -> None:
    body = _note() if path == _CREATE else _auth(flag="AR", id=1)
    assert ACCESS["co:" + path.removeprefix("/v1/co/")] == WRITE
    fake = FakeBridge()
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(path, json=body)
    assert reader.status_code == 403, reader.text
    assert fake.calls == []


def test_notes_reg_openapi() -> None:
    spec = _spec()
    create = spec["paths"][_CREATE]["post"]
    assert create["operationId"] == "coNoteCreate"
    assert create["summary"] == "票据登记"
    for word in ("vendor", "note_km", "test_account_only", "付款单"):
        assert word in create["description"], word
    assert spec["paths"][_DELETE]["post"]["operationId"] == "coNoteDelete"
    assert "分包" in create["description"]


def _ap_note(**extra) -> dict:
    body = {k: v for k, v in _NOTE.items() if k != "customer"}
    body.update(flag="AP", vendor="V001")
    body.update(extra)
    return _auth(**body)


@pytest.mark.parametrize(
    ("path", "body", "keys"),
    (
        (_CREATE, _ap_note(), _sealed(*(set(_REQUIRED) - {"customer"}), "vendor")),
        (_CREATE, _ap_note(note_km="2201"), _sealed(*(set(_REQUIRED) - {"customer"}), "vendor", "note_km")),
        (_DELETE, _auth(flag="AP", id=3416), _sealed("flag", "id")),
    ),
)
def test_ap_notes_reg_forwards_vendor(path: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        response = _wired(server.base_url).post(path, json=body)
        assert response.status_code == 200, response.text
        sent = json.loads(server.httpd.hits[-1][2])
    assert set(sent) == write_keys(path, keys)
    assert sent["flag"] == "AP"


def test_ap_notes_reg_partner_messages_match_the_bridge() -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_CREATE, json=_ap_note(customer="C001"))
    assert denied.status_code == 400
    assert (denied.json()["error"]["field"], denied.json()["error"]["message"]) == ("customer", "customer 只用于应收票据（flag AR）")
    denied = _client(fake).post(_CREATE, json={k: v for k, v in _ap_note().items() if k != "vendor"})
    assert (denied.json()["error"]["field"], denied.json()["error"]["message"]) == ("vendor", "缺少 vendor（供应商编码）")
    assert fake.calls == []
