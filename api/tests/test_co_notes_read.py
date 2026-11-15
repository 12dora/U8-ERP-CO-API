"""票据只读：/v1/co/notes/get 的转发字段、提前拒绝、只读权限、响应放行和 OpenAPI；vouchers/list 另收 ar_note / ap_note。"""

from __future__ import annotations

import json

import pytest
from tests.support import base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec
from u8co_api.co_access import ACCESS, READ

_PATH = "/v1/co/notes/get"
_BRIDGE = "/v1/notes/get"

_FORWARD = (
    (_auth(type="ar_note", id=3416), _sealed("type", "id")),
    (_auth(type="ap_note", code="PJ0001"), _sealed("type", "code")),
)

_REJECTS = (
    _auth(),
    _auth(type="ar_note"),
    _auth(type="ar_bill", id=1),
    _auth(type="ar_note", id=1, code="PJ1"),
    _auth(type="ar_note", id=0),
    _auth(type="ar_note", id=2147483648),
    _auth(type="ar_note", id="1"),
    _auth(type="ar_note", code=""),
    _auth(type="ar_note", code="x" * 61),
    _auth(type="ar_note", code="a\nb"),
    _auth(type="ar_note", code="a\x7fb"),
    _auth(type="ar_note", code="a\x85b"),
    _auth(type="ar_note", code="a\x9fb"),
    _auth(type="ar_note", id=1, flag="AR"),
)


@pytest.mark.parametrize(("body", "keys"), _FORWARD)
def test_note_get_forwards_exact_sealed_keys(body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(_PATH, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + _BRIDGE
    assert set(sent) == keys
    assert _SECRET.encode() not in raw
    assert sent["type"] == body["type"]


@pytest.mark.parametrize("body", _REJECTS)
def test_note_get_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _NoteBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        head = {"id": 3416, "code": "PJ0001", "remainder": "1000", "opening": False, "ufts": "123", "extra": 1}
        sub = {"id": 7, "style": "9D", "style_name": "贴现", "cancel_no": "PJTAR000000000001", "gl_ref": None}
        return {"ok": True, "type": "ar_note", "head": head, "subs": [sub], "subs_truncated": False}


def test_note_get_response_keeps_bridge_fields() -> None:
    body = _client(_NoteBridge()).post(_PATH, json=_auth(type="ar_note", id=3416)).json()
    assert body["type"] == "ar_note"
    assert body["head"]["remainder"] == "1000"
    assert body["head"]["extra"] == 1
    assert body["subs"][0]["style_name"] == "贴现"
    assert body["subs_truncated"] is False


def test_note_get_is_read_only_and_gated() -> None:
    body = _auth(type="ap_note", id=5)
    assert ACCESS["co:notes/get"] == READ
    fake = FakeBridge()
    assert _client(fake, co_enabled=False).post(_PATH, json=body).status_code == 404
    assert _client(fake, claims=base_claims()).post(_PATH, json=body).status_code == 403
    outside = _client(fake).post(_PATH, json=dict(body, acc="001"))
    assert outside.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=body)
    assert reader.status_code == 200, reader.text
    assert fake.calls[-1][0] == _BRIDGE
    assert fake.calls[-1][1]["type"] == "ap_note"


@pytest.mark.parametrize("kind", ["ar_note", "ap_note"])
def test_voucher_list_accepts_note_types(kind: str) -> None:
    fake = FakeBridge()
    ok = _client(fake).post("/v1/co/vouchers/list", json=_auth(type=kind, keys_only=True, changed_since="1"))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][0] == "/v1/vouchers/list"
    assert fake.calls[-1][1]["type"] == kind


def test_note_types_stay_out_of_writes() -> None:
    fake = FakeBridge()
    denied = _client(fake).post("/v1/co/vouchers/load", json=_auth(type="ar_note", id=1))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_note_get_openapi() -> None:
    spec = _spec()
    operation = spec["paths"][_PATH]["post"]
    assert operation["summary"] == "票据读取"
    assert operation["operationId"] == "coNoteGet"
    assert operation["tags"] == ["单据读取"]
    assert "权限：只读" in operation["description"]
    assert set(_enum(spec, "CoNoteGetIn", "type")) == {"ar_note", "ap_note"}
    assert {"ar_note", "ap_note"} <= set(_enum(spec, "CoVoucherListIn", "type"))
