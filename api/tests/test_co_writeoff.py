"""/v1/co/arap/writeoff 与 /v1/co/arap/writeoff/cancel：应收 / 应付核销和取消核销。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line as _audit_line
from tests.test_co_gate import _SECRET, FakeBridge, _client, bridge_body
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec

_PATH = "/v1/co/arap/writeoff"
_AR = {"type": "ar_receipt", "id": 5, "line_id": 7}
_INV = {"type": "sale_invoice", "id": 9, "line_id": 11, "amount": 1.25}
_BILL = {"type": "ar_bill", "id": 12, "amount": 3}


def _ask(receipt: dict | None = None, items: list | None = None, **extra) -> dict:
    return _auth(receipt=receipt if receipt is not None else _AR, items=items if items is not None else [_INV], **extra)


def test_writeoff_forwards_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(_PATH, json=_ask(items=[_INV, _BILL]))
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co/v1/arap/writeoff"
    assert set(sent) == _sealed("receipt", "items", "caller")
    assert sent["receipt"] == _AR
    assert sent["items"][0] == _INV
    # 省略的 line_id 不送（exclude_none）。
    assert sent["items"][1] == _BILL
    assert _SECRET.encode() not in raw
    body = response.json()
    assert body["cancel_no"].startswith("HXAR")
    assert body["receipt"]["line_id"] == 7
    assert [item["type"] for item in body["items"]] == ["sale_invoice", "ar_bill"]
    assert "line_id" not in body["items"][1]


def test_writeoff_accepts_ap_side_and_omitted_lines() -> None:
    fake = FakeBridge()
    receipt = {"type": "ap_payment", "id": 3}
    items = [{"type": "purchase_invoice", "id": 4, "amount": 10}, {"type": "ap_bill", "id": 6, "amount": 0.5}]
    ok = _client(fake).post(_PATH, json=_ask(receipt, items))
    assert ok.status_code == 200, ok.text
    path, payload = fake.calls[-1]
    assert path == "/v1/arap/writeoff"
    assert payload["receipt"] == receipt
    assert payload["date"] == "2026-09-27"


@pytest.mark.parametrize(
    "body",
    (
        _ask({"type": "sale_order", "id": 1}),
        _ask({"type": "ar_receipt", "id": 0}),
        _ask({"type": "ar_receipt", "id": 2147483648}),
        _ask({"type": "ar_receipt", "id": 1, "code": "SK1"}),
        _ask(items=[]),
        _ask(items=[dict(_INV, id=i) for i in range(1, 52)]),
        _ask(items=[{"type": "purchase_invoice", "id": 1, "amount": 1}]),
        _ask({"type": "ap_payment", "id": 1}, [_INV]),
        _ask(items=[dict(_INV, amount=0)]),
        _ask(items=[dict(_INV, amount=-1)]),
        _ask(items=[dict(_INV, amount=1.234)]),
        _ask(items=[dict(_INV, amount="1")]),
        _ask(items=[dict(_INV, amount=10**12 + 1)]),
        _ask(items=[dict(_BILL, line_id=3)]),
        _ask(items=[_INV, dict(_INV)]),
        _ask(items=[dict(_INV, memo="x")]),
        _auth(items=[_INV]),
        _auth(receipt=_AR),
        _ask(type="sale_invoice"),
    ),
)
def test_writeoff_rejects_bad_bodies_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


def test_writeoff_is_404_when_disabled() -> None:
    fake = FakeBridge()
    denied = _client(fake, co_enabled=False).post(_PATH, json=_ask())
    assert denied.status_code == 404
    assert fake.calls == []


def test_writeoff_rejects_an_account_outside_the_allowlist() -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=_ask(acc="001"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []


def test_writeoff_is_audited_with_the_receipt(capsys: pytest.CaptureFixture[str]) -> None:
    ok = _client().post(_PATH, json=_ask())
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert _audit_line(out, _PATH)["action"] == "co:arap/writeoff:writeoff#ar_receipt:5"


def test_writeoff_is_in_openapi_as_a_write_route() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "应收 / 应付核销"
    assert operation["operationId"] == "coArapWriteoff"
    assert "权限：写" in operation["description"]
    assert "汇兑差额留给期末汇兑损益" in operation["description"]


def test_writeoff_help_lists_the_bridge_400_cases() -> None:
    text = _spec()["paths"][_PATH]["post"]["description"]
    for phrase in ("明细行不存在", "请指定 line_id", "币种与收付款单不一致", "workflow_enabled", "UA_Period", "arap/writeoff/cancel"):
        assert phrase in text, phrase


class _Chatty(FakeBridge):
    # 桥在响应里多给一个字段。
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        return {**bridge_body(path, payload), "note": "x"}


def test_writeoff_keeps_extra_bridge_fields() -> None:
    fake = _Chatty()
    ok = _client(fake).post(_PATH, json=_ask())
    assert ok.status_code == 200, ok.text
    assert ok.json()["note"] == "x"


_CANCEL = "/v1/co/arap/writeoff/cancel"
_NO = "HXAR0000000000001"


def test_cancel_forwards_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(_CANCEL, json=_auth(flag="AR", cancel_no=_NO))
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co/v1/arap/writeoff/cancel"
    assert set(sent) == _sealed("flag", "cancel_no", "caller")
    assert (sent["flag"], sent["cancel_no"]) == ("AR", _NO)
    assert _SECRET.encode() not in raw
    body = response.json()
    assert (body["cancel_no"], body["flag"]) == (_NO, "AR")
    assert body["receipt"]["lines"] == [{"line_id": 7, "amount": 1, "remaining": 2.5}]
    # 桥多给的字段不丢。
    assert body["items"][0]["memo"] == "x"


def test_cancel_accepts_the_ap_side() -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_CANCEL, json=_auth(flag="AP", cancel_no="HXAP1"))
    assert ok.status_code == 200, ok.text
    path, payload = fake.calls[-1]
    assert path == "/v1/arap/writeoff/cancel"
    assert (payload["flag"], payload["cancel_no"]) == ("AP", "HXAP1")


@pytest.mark.parametrize(
    "body",
    (
        _auth(flag="AR", cancel_no="HXAP1"),
        _auth(flag="AP", cancel_no=_NO),
        _auth(flag="ar", cancel_no=_NO),
        _auth(flag="AR", cancel_no="HXAR"),
        _auth(flag="AR", cancel_no="HXAB1"),
        _auth(flag="AR", cancel_no="HXAR1 "),
        _auth(flag="AR", cancel_no="HXAR" + "1" * 21),
        _auth(flag="AR", cancel_no=3480),
        _auth(flag="AR"),
        _auth(cancel_no=_NO),
        _auth(flag="AR", cancel_no=_NO, id=1),
    ),
)
def test_cancel_rejects_bad_bodies_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_CANCEL, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


def test_cancel_is_audited_with_the_cancel_no(capsys: pytest.CaptureFixture[str]) -> None:
    ok = _client().post(_CANCEL, json=_auth(flag="AR", cancel_no=_NO))
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert _audit_line(out, _CANCEL)["action"] == "co:arap/writeoff/cancel:cancel#" + _NO


def test_cancel_is_in_openapi_as_a_write_route() -> None:
    operation = _spec()["paths"][_CANCEL]["post"]
    assert operation["summary"] == "取消应收 / 应付核销"
    assert operation["operationId"] == "coArapWriteoffCancel"
    assert "权限：写" in operation["description"]
    assert "核销已制单" in operation["description"]
