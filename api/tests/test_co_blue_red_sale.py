"""红冲蓝字销售发票（sale_invoice ← sale_invoice）的生单校验与转发。与红字发票参照退货单同一套校验。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth

_GEN = "/v1/co/vouchers/generate"
_LINES = [{"source_line_id": 21, "quantity": 1}, {"source_line_id": 22, "quantity": 0.5, "cMemo": "红冲"}]


def _blue(head: dict | None = None, lines: list | None = None, whole: bool = False) -> dict:
    body = {"type": "sale_invoice", "id": 9, "source_type": "sale_invoice"}
    if not whole:
        body["lines"] = _LINES if lines is None else lines
    if head is not None:
        body["head"] = head
    return _auth(**body)


_ACCEPTS = (
    _blue(),
    _blue(whole=True),
    _blue({"cVouchType": "27", "dDate": "2026-09-30", "cMemo": "红冲", "cDefine2": "x"}),
)

_REJECTS = (
    _blue(lines=[]),
    _blue({"cVouchType": "28"}),
    _blue({"cCusCode": "C1"}),
    _blue({"dDate": "2026/09/30"}),
    _blue(lines=[{"source_line_id": 1, "quantity": 0}]),
    _blue(lines=[{"source_line_id": 1, "quantity": 1, "cWhCode": "01"}]),
    _blue(lines=[{"source_line_id": 1, "quantity": 1}, {"source_line_id": 1, "quantity": 1}]),
)


@pytest.mark.parametrize("body", _ACCEPTS)
def test_blue_red_generate_accepts(body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_GEN, json=body)
    assert ok.status_code == 200, ok.text
    sent = fake.calls[0][1]
    assert (sent["type"], sent["id"], sent["source_type"]) == ("sale_invoice", 9, "sale_invoice")
    assert ("lines" in sent) == ("lines" in body)


@pytest.mark.parametrize("body", _REJECTS)
def test_blue_red_generate_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_other_targets_cannot_use_sale_invoice_source() -> None:
    fake = FakeBridge()
    body = _auth(type="dispatch", id=9, source_type="sale_invoice", lines=[{"source_line_id": 1, "quantity": 1}])
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []
