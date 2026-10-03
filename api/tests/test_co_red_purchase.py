"""红字采购入库参照采购退货单（purchase_in ← purchase_return），红字采购发票参照红字采购入库单。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_update_close_gen import _enum, _spec
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards

_GEN = "/v1/co/vouchers/generate"
_LINES = [{"source_line_id": 21, "quantity": 1}]
_HEAD = {"cWhCode": "01", "dDate": "2026-09-28", "cMemo": "退货入库"}
_INV_HEAD = {"cPBVCode": "RED-0001", "cPBVBillType": "01"}


def _red_in(head: dict | None = None, lines: list | None = None, **extra) -> dict:
    body = {"type": "purchase_in", "id": 9, "source_type": "purchase_return", "lines": _LINES if lines is None else lines}
    if head is not None:
        body["head"] = head
    body.update(extra)
    return _auth(**body)


def _red_inv(**extra) -> dict:
    body = {"type": "purchase_invoice", "id": 5, "head": _INV_HEAD, "lines": _LINES}
    body.update(extra)
    return _auth(**body)


@pytest.mark.parametrize(
    ("body", "keys"),
    [
        (_red_in(_HEAD), _sealed("type", "id", "head", "lines", "source_type")),
        (_red_inv(), _sealed("type", "id", "head", "lines")),
    ],
)
def test_red_purchase_generate_forwards_exact_keys(body: dict, keys: set[str]) -> None:
    _forwards(_GEN, "/v1/vouchers/generate", body, keys)


@pytest.mark.parametrize(
    "body",
    [
        _red_in(_HEAD),
        _red_in({"cWhCode": "01"}, lines=[{"source_line_id": 3, "quantity": 2, "cBatch": "B1", "cbMemo": "x"}]),
        _red_inv(),
        _red_inv(source_type="purchase_in"),
    ],
)
def test_red_purchase_generate_accepts(body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_GEN, json=body)
    assert ok.status_code == 200, ok.text
    sent = fake.calls[0][1]
    assert sent["type"] == body["type"]
    assert sent["lines"] == body["lines"]


@pytest.mark.parametrize(
    "body",
    [
        _red_inv(source_type="purchase_return"),
        _auth(type="arrival", id=9, source_type="purchase_return", lines=_LINES),
        _auth(type="sale_invoice", id=9, source_type="purchase_return", lines=_LINES),
        _red_in(_HEAD, lines=[{"source_line_id": 1, "quantity": -1}]),
        _red_in(_HEAD, lines=[{"source_line_id": 1, "quantity": 0}]),
        _red_in(_HEAD, lines=[]),
        _red_in({"cCode": "RK1"}),
    ],
)
def test_red_purchase_generate_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_red_purchase_in_echoes_the_return_source() -> None:
    fake = FakeBridge()
    made = _client(fake).post(_GEN, json=_red_in(_HEAD))
    assert made.status_code == 200, made.text
    assert fake.calls[-1][1]["source_type"] == "purchase_return"
    assert made.json()["source_type"] == "purchase_return"
    assert made.json()["source_id"] == 9


def test_red_purchase_is_in_the_openapi() -> None:
    spec = _spec()
    assert "purchase_return" in _enum(spec, "CoGenerateIn", "source_type")
    source = spec["components"]["schemas"]["CoGenerateIn"]["properties"]["source_type"]["description"]
    assert "purchase_return" in source
    assert "红字采购入库" in source
