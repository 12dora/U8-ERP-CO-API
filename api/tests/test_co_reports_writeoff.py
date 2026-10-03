"""/v1/co/reports/arap_writeoffs：核销记录查询的转发字段、提前拒绝、只读权限、响应放行和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from tests.support import base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec
from u8co_api.co_access import ACCESS, READ

_PATH = "/v1/co/reports/arap_writeoffs"
_BRIDGE = "/v1/reports/arap_writeoffs"

_FORWARD = (
    (_auth(flag="AR"), _sealed("flag")),
    (
        _auth(
            flag="AR",
            partner="C001",
            receipt={"type": "ar_receipt", "id": 5},
            target={"type": "sale_invoice", "id": 9},
            date_from="2026-09-01",
            date_to="2026-09-30",
            cancel_no="HXAR0000000000001",
            after="eA",
            limit=200,
        ),
        _sealed("flag", "partner", "receipt", "target", "date_from", "date_to", "cancel_no", "after", "limit"),
    ),
    (
        _auth(flag="AP", receipt_code="FK0001", target_code="PI0001", limit=1),
        _sealed("flag", "receipt_code", "target_code", "limit"),
    ),
    (_auth(flag="AP", target={"type": "ap_bill", "id": 3}), _sealed("flag", "target")),
)

_REJECTS = (
    _auth(),
    _auth(flag="ar"),
    _auth(flag="XX"),
    _auth(flag="AR", receipt={"type": "ap_payment", "id": 1}),
    _auth(flag="AP", receipt={"type": "ar_receipt", "id": 1}),
    _auth(flag="AR", receipt={"type": "ar_receipt", "id": 0}),
    _auth(flag="AR", receipt={"type": "ar_receipt", "id": 1, "line_id": 2}),
    _auth(flag="AR", receipt={"type": "ar_receipt", "id": 1}, receipt_code="SK1"),
    _auth(flag="AR", target={"type": "purchase_invoice", "id": 1}),
    _auth(flag="AP", target={"type": "sale_invoice", "id": 1}),
    _auth(flag="AR", target={"type": "sale_invoice", "id": "1"}),
    _auth(flag="AR", target={"type": "sale_invoice", "id": 1}, target_code="SO1"),
    _auth(flag="AR", cancel_no="HXAP0000000000001"),
    _auth(flag="AR", cancel_no="HXAR"),
    _auth(flag="AR", cancel_no="XX0001"),
    _auth(flag="AR", date_from="2026-09-31"),
    _auth(flag="AR", date_from="2026-09-02", date_to="2026-09-01"),
    _auth(flag="AR", limit=0),
    _auth(flag="AR", limit=201),
    _auth(flag="AR", partner=" "),
    _auth(flag="AR", side="ar"),
)


@pytest.mark.parametrize(("body", "keys"), _FORWARD)
def test_writeoffs_forward_exact_sealed_keys(body: dict, keys: set[str]) -> None:
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
    for name in ("receipt", "target"):
        if name in body:
            assert sent[name] == body[name]


@pytest.mark.parametrize("body", _REJECTS)
def test_writeoffs_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _ListBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        target = {"type": "sale_invoice", "vouch_type": "26", "id": 9, "line_id": 11, "code": "SO1", "amount": 1.0}
        receipt = {"type": "ar_receipt", "vouch_type": "48", "id": 5, "line_id": 7, "line_ids": [7], "code": "SK1"}
        item = {
            "cancel_no": "HXAR0000000000001",
            "date": "2026-09-28",
            "year": 2026,
            "period": 9,
            "receipt": receipt,
            "targets": [target],
            "amount": 1.0,
            "gl_voucher": False,
            "cancellable": False,
            "reason": "核销已制单，请先删除凭证",
            "extra": 1,
        }
        return {"ok": True, "flag": "AR", "items": [item], "next": "eA", "more": True}


def test_writeoffs_response_keeps_bridge_fields() -> None:
    body = _client(_ListBridge()).post(_PATH, json=_auth(flag="AR")).json()
    assert body["flag"] == "AR"
    assert body["next"] == "eA"
    assert body["more"] is True
    item = body["items"][0]
    assert item["cancel_no"] == "HXAR0000000000001"
    assert item["targets"][0]["line_id"] == 11
    assert item["receipt"]["line_ids"] == [7]
    assert item["cancellable"] is False
    assert item["reason"] == "核销已制单，请先删除凭证"
    assert item["extra"] == 1


def test_writeoffs_are_read_only_and_gated() -> None:
    body = _auth(flag="AP")
    assert ACCESS["co:reports/arap_writeoffs"] == READ
    fake = FakeBridge()
    assert _client(fake, co_enabled=False).post(_PATH, json=body).status_code == 404
    assert _client(fake, claims=base_claims()).post(_PATH, json=body).status_code == 403
    outside = _client(fake).post(_PATH, json=dict(body, acc="001"))
    assert outside.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=body)
    assert reader.status_code == 200, reader.text
    assert fake.calls[-1][0] == _BRIDGE
    assert fake.calls[-1][1]["flag"] == "AP"


def test_writeoffs_openapi() -> None:
    spec = _spec()
    operation = spec["paths"][_PATH]["post"]
    assert operation["summary"] == "核销记录"
    assert operation["operationId"] == "coReportArapWriteoffs"
    assert operation["tags"] == ["报表"]
    assert "权限：只读" in operation["description"]
    assert "cancellable" in operation["description"]
    assert set(_enum(spec, "ReportWriteoffsIn", "flag")) == {"AR", "AP"}
