"""Purchase generate (arrival, purchase_invoice, purchase_in from qm_incoming_check) and invoice delete."""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards

_GEN = "/v1/co/vouchers/generate"
_INV_HEAD = {"cPBVCode": "0000000123", "cPBVBillType": "01", "dPBVDate": "2026-09-27", "cPBVMemo": "测试"}
_LINES = [{"source_line_id": 1, "quantity": 2}, {"source_line_id": 2, "quantity": 0.5}]
_QM_LINE = [{"source_line_id": 8, "quantity": 3, "cbatch": "B1", "cbmemo": "备注"}]
_QM_HEAD = {"cwhcode": "01"}
_ARR_HEAD = {"cWhCode": "01", "dDate": "2026-09-27", "cMemo": "到货", "cDepCode": "D901"}


def _arr(head: dict | None = None, lines: list | None = None, **extra) -> dict:
    body = {"type": "arrival", "id": 3, "lines": _LINES if lines is None else lines}
    if head is not None:
        body["head"] = head
    body.update(extra)
    return _auth(**body)


def _inv(head: dict | None = None, lines: list | None = None, **extra) -> dict:
    body = {"type": "purchase_invoice", "id": 5, "head": _INV_HEAD if head is None else head}
    body["lines"] = _LINES if lines is None else lines
    body.update(extra)
    return _auth(**body)


def _qm(head: dict | None = None, lines: list | None = None, **extra) -> dict:
    body = {"type": "purchase_in", "id": 8, "source_type": "qm_incoming_check"}
    body["head"] = _QM_HEAD if head is None else head
    body["lines"] = _QM_LINE if lines is None else lines
    body.update(extra)
    return _auth(**body)


_KEYS = ("type", "id", "head", "lines")
_FORWARD = (
    (_GEN, "/v1/vouchers/generate", _inv(), _sealed(*_KEYS)),
    (_GEN, "/v1/vouchers/generate", _inv(source_type="purchase_in"), _sealed(*_KEYS, "source_type")),
    (_GEN, "/v1/vouchers/generate", _qm(), _sealed(*_KEYS, "source_type")),
    (
        _GEN,
        "/v1/vouchers/generate",
        _auth(type="purchase_in", id=3, source_type="purchase_order", lines=_LINES),
        _sealed("type", "id", "lines", "source_type"),
    ),
    ("/v1/co/vouchers/delete", "/v1/vouchers/delete", _auth(type="purchase_invoice", id=5), _sealed("type", "id")),
    # 契约 B1：采购发票复核 / 取消复核走 vouchers/verify。
    (
        "/v1/co/vouchers/verify",
        "/v1/vouchers/verify",
        _auth(type="purchase_invoice", id=5, action="verify"),
        _sealed("type", "id", "action"),
    ),
    (
        "/v1/co/vouchers/verify",
        "/v1/vouchers/verify",
        _auth(type="purchase_invoice", id=5, action="unverify"),
        _sealed("type", "id", "action"),
    ),
    (_GEN, "/v1/vouchers/generate", _arr(), _sealed("type", "id", "lines")),
    (_GEN, "/v1/vouchers/generate", _arr(_ARR_HEAD, source_type="purchase_order"), _sealed(*_KEYS, "source_type")),
)

_ACCEPTS = (
    _arr(),
    _arr(_ARR_HEAD),
    _arr({"cwhcode": "01", "ddate": "2026-09-01"}, lines=[{"source_line_id": 7, "quantity": 1}]),
    _arr({}, source_type="purchase_order"),
    _inv(),
    _inv(head={"cPBVCode": "X1"}),
    _inv(head={"cpbvcode": "X1", "cpbvbilltype": "02"}),
    _inv(lines=[{"source_line_id": 1, "quantity": 1}]),
    _qm(),
    _qm(head={"cWhCode": "01", "cMemo": "来料"}, lines=[{"source_line_id": 8, "quantity": 1}]),
    _qm(lines=[{"source_line_id": 8, "quantity": 1, "cPosition": "A01"}]),
    _auth(type="purchase_in", id=3, head={"cWhCode": "01"}, lines=[{"source_line_id": 1, "quantity": 1, "cposition": "A01"}]),
    _auth(
        type="purchase_in",
        id=4,
        source_type="purchase_return",
        head={"cWhCode": "01"},
        lines=[{"source_line_id": 2, "quantity": 1, "cposition": "A01"}],
    ),
    _auth(type="dispatch", id=1, source_type="sale_order", lines=[{"source_line_id": 1, "quantity": 1}]),
    _auth(type="sale_out", id=1, source_type="dispatch"),
)

_REJECTS = (
    _arr(lines=[]),
    _arr({"cWhCode": "01"}, lines=None, source_type="purchase_in"),
    _arr({"cPOID": "PO1"}),
    _arr({"cCode": "DH1"}),
    _arr({"cWhCode": 1}),
    _arr({"cWhCode": "01", "cwhcode": "02"}),
    _arr({"dDate": "2026-9-27"}),
    _arr(lines=[{"source_line_id": 1, "quantity": 1, "cBatch": "B"}]),
    _arr(lines=[{"source_line_id": 1, "quantity": -1}]),
    _arr(lines=[{"source_line_id": i, "quantity": 1} for i in range(1, 202)]),
    _auth(type="arrival", id=3),
    _inv(head={}),
    _auth(type="purchase_invoice", id=5, lines=_LINES),
    _inv(head={"cPBVBillType": "01"}),
    _inv(head={"cPBVCode": ""}),
    _inv(head={"cPBVCode": "   "}),
    _inv(head={"cPBVCode": "x" * 31}),
    _inv(head={"cPBVCode": 123}),
    _inv(head={"cPBVCode": "X1", "cPBVBillType": "03"}),
    _inv(head={"cPBVCode": "X1", "cPBVBillType": 1}),
    _inv(head={"cPBVCode": "X1", "dPBVDate": "2026/09/27"}),
    _inv(head={"cPBVCode": "X1", "cPBVMemo": "x" * 256}),
    _inv(head={"cPBVCode": "X1", "cMemo": "x"}),
    _inv(head={"cPBVCode": "X1", "cpbvcode": "X2"}),
    _inv(head={"cPBVCode": "X1", "PBVID": 9}),
    _inv(lines=[]),
    _inv(lines=[{"source_line_id": 1, "quantity": 1, "cBatch": "B"}]),
    _inv(lines=[{"source_line_id": 1}]),
    _inv(lines=[{"source_line_id": i, "quantity": 1} for i in range(1, 202)]),
    _inv(source_type="purchase_order"),
    _auth(type="dispatch", id=1, source_type="qm_incoming_check", lines=[{"source_line_id": 1, "quantity": 1}]),
    _auth(type="purchase_in", id=1, source_type="arrival", lines=[{"source_line_id": 1, "quantity": 1}]),
    _qm(head={}),
    _qm(head={"cMemo": "x"}),
    _qm(lines=[*_QM_LINE, {"source_line_id": 9, "quantity": 1}]),
    _qm(lines=[{"source_line_id": 8, "quantity": 1, "cwhcode": "02"}]),
    _qm(lines=[{"source_line_id": 8, "quantity": 0}]),
    _qm(head={"cWhCode": "01", "cCode": "RK1"}),
)


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_purchase_generate_forwards_exact_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    _forwards(path, bridge, body, keys)


@pytest.mark.parametrize("body", _ACCEPTS)
def test_purchase_generate_accepts(body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_GEN, json=body)
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["type"] == body["type"]


@pytest.mark.parametrize("body", _REJECTS)
def test_purchase_generate_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_invoice_code_reaches_the_bridge_and_source_is_reported() -> None:
    fake = FakeBridge()
    client = _client(fake)
    made = client.post(_GEN, json=_inv())
    assert made.status_code == 200, made.text
    assert fake.calls[-1][1]["head"] == _INV_HEAD
    assert made.json()["source_type"] == "purchase_in"
    qm = client.post(_GEN, json=_qm())
    assert qm.status_code == 200, qm.text
    assert qm.json()["source_type"] == "qm_incoming_check"
    assert fake.calls[-1][1]["lines"] == _QM_LINE


def test_arrival_generate_reports_the_order() -> None:
    fake = FakeBridge()
    made = _client(fake).post(_GEN, json=_arr(_ARR_HEAD))
    assert made.status_code == 200, made.text
    assert fake.calls[-1][1]["head"] == _ARR_HEAD
    assert fake.calls[-1][1]["lines"] == _LINES
    assert made.json()["source_type"] == "purchase_order"
    assert made.json()["source_id"] == 3


def test_purchase_invoice_delete_is_allowed() -> None:
    fake = FakeBridge()
    ok = _client(fake).post("/v1/co/vouchers/delete", json=_auth(type="purchase_invoice", id=5))
    assert ok.status_code == 200, ok.text
    assert ok.json()["deleted"] is True
