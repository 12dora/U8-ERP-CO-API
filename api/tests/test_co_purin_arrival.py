"""采购入库参照到货单（source_type=arrival）：lines 可省、仓库取表头或各行一致的 cWhCode，原样转给桥。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth

_GEN = "/v1/co/vouchers/generate"
_LINE = {"source_line_id": 11, "quantity": 2}


def _arr_in(head: dict | None = None, lines: list | None = None) -> dict:
    body: dict = {"type": "purchase_in", "id": 3, "source_type": "arrival"}
    if head is not None:
        body["head"] = head
    if lines is not None:
        body["lines"] = lines
    return _auth(**body)


_ACCEPTS = (
    _arr_in({"cWhCode": "01"}),
    _arr_in({"cWhCode": "01", "dDate": "2026-10-03", "cMemo": "到货入库"}, [_LINE]),
    _arr_in(None, [dict(_LINE, cWhCode="01"), {"source_line_id": 12, "quantity": 1, "cwhcode": "01"}]),
    _arr_in({"cWhCode": "01"}, [dict(_LINE, cbatch="B1", cposition="A01", cbmemo="备注")]),
    _arr_in({"cWhCode": "01"}, [dict(_LINE, cWhCode="01")]),
)
_REJECTS = (
    _arr_in(),
    _arr_in(None, [_LINE]),
    _arr_in({"cWhCode": "01"}, [dict(_LINE, cWhCode="02")]),
    _arr_in(None, [dict(_LINE, cWhCode="01"), {"source_line_id": 12, "quantity": 1, "cWhCode": "02"}]),
    _arr_in({"cWhCode": "01"}, []),
    _arr_in({"cWhCode": "01"}, [{"source_line_id": 11, "quantity": 0}]),
    _arr_in({"cWhCode": "01"}, [_LINE, dict(_LINE)]),
    _arr_in({"cWhCode": "01"}, [dict(_LINE, cInvCode="X1")]),
    _arr_in({"cWhCode": "01", "ID": 9}),
)


@pytest.mark.parametrize("body", _ACCEPTS)
def test_purchase_in_from_arrival_accepts(body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_GEN, json=body)
    assert ok.status_code == 200, ok.text
    sent = fake.calls[0][1]
    assert (sent["type"], sent["source_type"]) == ("purchase_in", "arrival")
    assert ("lines" in sent) == ("lines" in body)


@pytest.mark.parametrize("body", _REJECTS)
def test_purchase_in_from_arrival_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_other_purchase_in_sources_still_need_lines() -> None:
    fake = FakeBridge()
    body = _auth(type="purchase_in", id=3, source_type="purchase_order", head={"cWhCode": "01"})
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []
