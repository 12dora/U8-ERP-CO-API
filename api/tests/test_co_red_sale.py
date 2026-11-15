"""红字销售发票参照退货单（sale_invoice ← sale_return）的生单校验与转发。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards

_GEN = "/v1/co/vouchers/generate"
_LINES = [{"source_line_id": 11, "quantity": 2}, {"source_line_id": 12, "quantity": 0.5, "cMemo": "退"}]
_HEAD = {"cVouchType": "26", "dDate": "2026-09-28", "cMemo": "红冲", "cDefine1": "x"}


def _red(head: dict | None = None, lines: list | None = None, whole: bool = False, **extra) -> dict:
    body = {"type": "sale_invoice", "id": 7, "source_type": "sale_return"}
    if not whole:
        body["lines"] = _LINES if lines is None else lines
    if head is not None:
        body["head"] = head
    body.update(extra)
    return _auth(**body)


_ACCEPTS = (
    _red(),
    _red(whole=True),
    _red(_HEAD),
    _red({"cVouchType": 27}, whole=True),
    _red({"cvouchtype": "27", "CMEMO": "x"}),
    _red(lines=[{"source_line_id": 3, "quantity": 1, "cDefine22": "a", "cdefine37": "b"}]),
)

_REJECTS = (
    _red(lines=[]),
    _red({"cVouchType": "28"}),
    _red({"cVouchType": True}),
    _red({"cSBVCode": "X1"}),
    _red({"SBVID": 1}),
    _red({"cCusCode": "C1"}),
    _red({"cDefine17": "x"}),
    _red({"dDate": "2026/09/28"}),
    _red({"cMemo": 1}),
    _red({"cMemo": "a", "cmemo": "b"}),
    _red(lines=[{"source_line_id": 1, "quantity": 1, "cWhCode": "01"}]),
    _red(lines=[{"source_line_id": 1, "quantity": 1, "cDefine21": "x"}]),
    _red(lines=[{"source_line_id": 1, "quantity": -1}]),
    _red(lines=[{"source_line_id": 1, "quantity": 0}]),
    _red(lines=[{"source_line_id": 0, "quantity": 1}]),
    _red(lines=[{"source_line_id": "1", "quantity": 1}]),
    _red(lines=[{"source_line_id": 1, "quantity": 1}, {"source_line_id": 1, "quantity": 2}]),
    _red(lines=[{"source_line_id": i, "quantity": 1} for i in range(1, 202)]),
    _red(source_type="purchase_return"),
)


@pytest.mark.parametrize("body", _ACCEPTS)
def test_red_sale_invoice_generate_accepts(body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_GEN, json=body)
    assert ok.status_code == 200, ok.text
    sent = fake.calls[0][1]
    assert sent["type"] == "sale_invoice"
    assert sent["source_type"] == "sale_return"


@pytest.mark.parametrize("body", _REJECTS)
def test_red_sale_invoice_generate_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_red_sale_invoice_whole_return_omits_lines() -> None:
    fake = FakeBridge()
    made = _client(fake).post(_GEN, json=_red(_HEAD, whole=True))
    assert made.status_code == 200, made.text
    sent = fake.calls[-1][1]
    assert "lines" not in sent
    assert sent["head"] == _HEAD


def test_red_sale_invoice_forwards_exact_keys() -> None:
    _forwards(_GEN, "/v1/vouchers/generate", _red(_HEAD), _sealed("type", "id", "head", "lines", "source_type"))


def test_blue_sale_invoice_still_needs_lines() -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=_auth(type="sale_invoice", id=7))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []
