"""采购手工结算：vouchers/create，type=purchase_settle（第一级，分级由桥判断）。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth

_CREATE = "/v1/co/vouchers/create"
_KIND = "purchase_settle"


def _body(lines: list[dict], **extra) -> dict:
    body = {"type": _KIND, "head": {"settle_date": "2026-09-30"}, "lines": lines}
    body.update(extra)
    return _auth(**body)


_PAIR = {"in_line_id": 1000000031, "invoice_line_id": 1000000032, "quantity": 2}


def test_pairs_and_offsets_reach_the_bridge_unchanged() -> None:
    lines = [
        dict(_PAIR, amount=100.5),
        {"in_line_id": 11, "quantity": 1},
        {"in_line_id": 12, "invoice_line_id": 0, "quantity": -1},
        {"invoice_line_id": 21, "quantity": -3},
        {"in_line_id": 0, "invoice_line_id": 22, "quantity": 3},
    ]
    fake = FakeBridge()
    done = _client(fake).post(_CREATE, json=_body(lines))
    assert done.status_code == 200, done.text
    path, sent = fake.calls[-1]
    assert path == "/v1/vouchers/create"
    assert (sent["type"], sent["head"], sent["lines"]) == (_KIND, {"settle_date": "2026-09-30"}, lines)


def test_up_to_400_lines_for_settle_only() -> None:
    many = [{"in_line_id": i, "invoice_line_id": i, "quantity": 1} for i in range(1, 401)]
    fake = FakeBridge()
    assert _client(fake).post(_CREATE, json=_body(many)).status_code == 200
    too_many = [{"in_line_id": i, "invoice_line_id": i, "quantity": 1} for i in range(1, 402)]
    assert _client(fake).post(_CREATE, json=_body(too_many)).status_code == 400
    other = _auth(type="other_in", head={"cwhcode": "01"}, lines=[{"cinvcode": "A", "iquantity": 1}] * 201)
    assert _client(fake).post(_CREATE, json=other).status_code == 400
    assert len(fake.calls) == 1


_REJECTS = (
    _body([{"quantity": 1}]),
    _body([{"in_line_id": 0, "invoice_line_id": 0, "quantity": 1}]),
    _body([{"in_line_id": 1, "invoice_line_id": 2, "quantity": 0}]),
    _body([{"in_line_id": 1, "invoice_line_id": 2}]),
    _body([{"in_line_id": True, "quantity": 1}]),
    _body([{"in_line_id": -1, "quantity": 1}]),
    _body([{"in_line_id": "1", "quantity": 1}]),
    _body([{"in_line_id": 1, "quantity": "1"}]),
    _body([{"in_line_id": 1, "quantity": 1, "cMemo": "x"}]),
    _body([{"in_line_id": 1, "quantity": 1, "amount": 5}]),
    _body([dict(_PAIR, amount=-5)]),
    _body([_PAIR, _PAIR]),
    _body([_PAIR], head={"cMemo": "x"}),
    _body([_PAIR], head={"settle_date": "2026/09/30"}),
    _body([_PAIR], head={"settle_date": "2099-01-31"}),
    _body([]),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_create_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_CREATE, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_head_may_be_empty() -> None:
    fake = FakeBridge()
    done = _client(fake).post(_CREATE, json=_body([_PAIR], head={}))
    assert done.status_code == 200, done.text
