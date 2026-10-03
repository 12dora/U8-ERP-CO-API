"""销售出库按发货单行部分生成（sale_out 生单可带 lines）。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards

_GEN = "/v1/co/vouchers/generate"
_LINES = [{"source_line_id": 11, "quantity": 1}, {"source_line_id": 12, "quantity": 0.5}]


def _out(lines: object = None, **extra) -> dict:
    body: dict = {"type": "sale_out", "id": 7}
    if lines is not None:
        body["lines"] = lines
    body.update(extra)
    return _auth(**body)


_ACCEPTS = (
    _out(),
    _out(_LINES),
    _out([{"source_line_id": 1, "quantity": 1}], source_type="dispatch"),
    _out([{"source_line_id": i, "quantity": 1} for i in range(1, 201)]),
)

_REJECTS = (
    _out([]),
    _out([{"source_line_id": 1, "quantity": 1, "cWhCode": "01"}]),
    _out([{"source_line_id": 1, "quantity": 1, "cBatch": 5}]),
    _out([{"source_line_id": 1}]),
    _out([{"SOURCE_LINE_ID": 1, "quantity": 2}]),
    _out([{"quantity": 1}]),
    _out([{"source_line_id": 0, "quantity": 1}]),
    _out([{"source_line_id": 1, "quantity": 0}]),
    _out([{"source_line_id": 1, "quantity": -1}]),
    _out([{"source_line_id": 1, "quantity": "1"}]),
    _out([{"source_line_id": 1, "quantity": 1}, {"source_line_id": 1, "quantity": 2}]),
    _out([{"source_line_id": i, "quantity": 1} for i in range(1, 202)]),
    _out(_LINES, head={"cMemo": "x"}),
)


def test_sale_out_lines_forward_exact_keys() -> None:
    _forwards(_GEN, "/v1/vouchers/generate", _out(_LINES), _sealed("type", "id", "lines"))


def test_sale_out_without_lines_forwards_no_lines() -> None:
    _forwards(_GEN, "/v1/vouchers/generate", _out(), _sealed("type", "id"))


@pytest.mark.parametrize("body", _ACCEPTS)
def test_sale_out_generate_accepts(body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_GEN, json=body)
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["type"] == "sale_out"


@pytest.mark.parametrize("body", _REJECTS)
def test_sale_out_generate_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_sale_out_lines_are_sent_unchanged() -> None:
    fake = FakeBridge()
    made = _client(fake).post(_GEN, json=_out(_LINES))
    assert made.status_code == 200, made.text
    sent = fake.calls[-1][1]
    assert sent["lines"] == _LINES
    assert "head" not in sent
    assert made.json()["source_type"] == "dispatch"
    assert made.json()["source_id"] == 7
