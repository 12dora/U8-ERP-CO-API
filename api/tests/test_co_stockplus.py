"""销售出库生单行带批号 / 货位、按批号货位拆行；无来源红字采购入库（表头 red=true）原样转给桥。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards

_GEN = "/v1/co/vouchers/generate"
_CREATE = "/v1/co/vouchers/create"
_SPLIT = [
    {"source_line_id": 11, "quantity": 1, "cposition": "A01"},
    {"source_line_id": 11, "quantity": 2, "cposition": "A02"},
    {"source_line_id": 12, "quantity": 0.5, "cBatch": "B1", "cPosition": "A01"},
]
_RED_HEAD = {"cWhCode": "01", "cVenCode": "V001", "dDate": "2026-09-28", "red": True}
_RED_LINES = [{"cInvCode": "A001", "iQuantity": 2, "iUnitCost": 10.5}]


def _out(lines: object) -> dict:
    return _auth(type="sale_out", id=7, lines=lines)


_ACCEPTS = (
    _out(_SPLIT),
    _out([{"source_line_id": 1, "quantity": 1, "cbatch": "B"}]),
    _out([{"source_line_id": 1, "quantity": 1, "CBATCH": "B", "cposition": ""}]),
    _out([{"source_line_id": 1, "quantity": 1, "cbatch": "B1"}, {"source_line_id": 1, "quantity": 1, "cbatch": "B2"}]),
    _out([{"source_line_id": 1, "quantity": 1, "cbatch": "x" * 60, "cposition": "p" * 20}]),
)

_REJECTS = (
    _out([{"source_line_id": 1, "quantity": 1, "cwhcode": "01"}]),
    _out([{"source_line_id": 1, "quantity": 1, "cbatch": 5}]),
    _out([{"source_line_id": 1, "quantity": 1, "cposition": True}]),
    _out([{"source_line_id": 1, "quantity": 1, "cbatch": "x" * 61}]),
    _out([{"source_line_id": 1, "quantity": 1, "cposition": "p" * 21}]),
    _out([{"source_line_id": 1, "quantity": 1, "cbatch": "B", "CBATCH": "B"}]),
    _out([{"source_line_id": 1, "quantity": 1, "cbatch": "b1"}, {"source_line_id": 1, "quantity": 2, "cbatch": " B1 "}]),
    _out([{"source_line_id": 1, "quantity": 1}, {"source_line_id": 1, "quantity": 2}]),
    _out([{"source_line_id": 1, "quantity": 0, "cbatch": "B"}]),
    _out([{"source_line_id": 1, "quantity": True, "cbatch": "B"}]),
    _auth(type="sale_out", id=7, lines=_SPLIT, head={"cWhCode": "02"}),
)


@pytest.mark.parametrize("body", _ACCEPTS)
def test_sale_out_batch_lines_accepted(body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_GEN, json=body)
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["lines"] == body["lines"]


@pytest.mark.parametrize("body", _REJECTS)
def test_sale_out_batch_lines_rejected_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_sale_out_split_forwards_exact_keys() -> None:
    _forwards(_GEN, "/v1/vouchers/generate", _out(_SPLIT), _sealed("type", "id", "lines"))


def test_red_purchase_in_create_forwards_red_flag() -> None:
    body = _auth(type="purchase_in", head=_RED_HEAD, lines=_RED_LINES)
    _forwards(_CREATE, "/v1/vouchers/create", body, _sealed("type", "head", "lines"))
    fake = FakeBridge()
    made = _client(fake).post(_CREATE, json=body)
    assert made.status_code == 200, made.text
    sent = fake.calls[-1][1]
    assert sent["head"]["red"] is True
    assert sent["lines"] == _RED_LINES
