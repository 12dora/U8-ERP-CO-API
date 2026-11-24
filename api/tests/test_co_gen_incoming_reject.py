"""采购入库参照来料不良品处理单（qm_incoming_reject）的 /v1/co/vouchers/generate。桥是本地假桥。"""

from __future__ import annotations

import pytest
from tests.test_co_core_routes import _auth
from tests.test_co_gate import FakeBridge, _client
from u8co_api.co_gen_source import SOURCES

_PATH = "/v1/co/vouchers/generate"
_HEAD = {"cWhCode": "01", "cMemo": "文本A"}
_LINES = [{"source_line_id": 11, "quantity": 3, "cbatch": "B1"}, {"source_line_id": 12, "quantity": 5}]


def _body(**extra) -> dict:
    body = {"type": "purchase_in", "id": 7, "source_type": "qm_incoming_reject", "head": _HEAD, "lines": _LINES}
    body.update(extra)
    return _auth(**body)


def test_purchase_in_from_reject_forwards_source_type_and_lines() -> None:
    fake = FakeBridge()
    made = _client(fake).post(_PATH, json=_body())
    assert made.status_code == 200, made.text
    assert made.json()["source_type"] == "qm_incoming_reject"
    assert made.json()["source_id"] == 7
    path, sent = fake.calls[-1]
    assert path == "/v1/vouchers/generate"
    assert sent["source_type"] == "qm_incoming_reject"
    assert sent["lines"] == _LINES


def test_purchase_in_default_source_is_still_the_order() -> None:
    assert SOURCES["purchase_in"][0] == "purchase_order"
    assert "qm_incoming_reject" in SOURCES["purchase_in"]


def test_purchase_in_from_reject_accepts_up_to_200_lines() -> None:
    lines = [{"source_line_id": i, "quantity": 1, "cposition": "A01"} for i in range(1, 201)]
    made = _client(FakeBridge()).post(_PATH, json=_body(lines=lines))
    assert made.status_code == 200, made.text


@pytest.mark.parametrize(
    "body",
    (
        _body(head={}),
        _body(head={"cMemo": "x"}),
        _body(head={"cWhCode": "01", "cCode": "RK1"}),
        _body(lines=[]),
        _body(lines=[{"source_line_id": i, "quantity": 1} for i in range(1, 202)]),
        _body(lines=[{"source_line_id": 1}]),
        _body(lines=[{"source_line_id": 1, "quantity": 0}]),
        _body(lines=[{"source_line_id": 1, "quantity": 1, "cwhcode": "02"}]),
        _body(lines=[{"source_line_id": 1, "quantity": 1, "iposid": 5}]),
    ),
)
def test_purchase_in_from_reject_refuses_bad_shapes(body: dict) -> None:
    fake = FakeBridge()
    refused = _client(fake).post(_PATH, json=body)
    assert refused.status_code == 400, refused.text
    assert fake.calls == []


@pytest.mark.parametrize("kind", ("product_in", "material_out", "arrival", "purchase_invoice"))
def test_incoming_reject_source_only_for_purchase_in(kind: str) -> None:
    fake = FakeBridge()
    refused = _client(fake).post(_PATH, json=_body(type=kind))
    assert refused.status_code == 400, refused.text
    assert fake.calls == []
