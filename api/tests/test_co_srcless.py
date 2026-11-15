"""无来源新增（发货单、先开票销售发票、到货单、材料出库单）与产成品入库参照生产订单。桥是本地假桥。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth

_CREATE = "/v1/co/vouchers/create"
_GEN = "/v1/co/vouchers/generate"
_SA_HEAD = {"cCusCode": "C001", "cSTCode": "01", "dDate": "2026-09-28"}
_SA_LINE = {"cWhCode": "01", "cInvCode": "A001", "iQuantity": 2, "iTaxUnitPrice": 11.3}
_BODIES = {
    "dispatch": (_SA_HEAD, _SA_LINE),
    "sale_invoice": (dict(_SA_HEAD, cVouchType="27"), _SA_LINE),
    "arrival": ({"cVenCode": "V001", "cMemo": "m"}, {"cInvCode": "A001", "iQuantity": 1, "iOriTaxCost": 5}),
    "material_out": (
        {"cWhCode": "01", "cRdCode": "103"},
        {"cInvCode": "A001", "iQuantity": 1, "cBatch": "B1", "cPosition": "01"},
    ),
}


class _MadeDispatch(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        body = super().call(path, payload)
        if path == "/v1/vouchers/create" and payload.get("type") == "sale_invoice":
            body = dict(body, dispatch_id=77)
        return body


@pytest.mark.parametrize("kind", sorted(_BODIES))
def test_srcless_create_forwards_head_and_lines(kind: str) -> None:
    fake = FakeBridge()
    head, line = _BODIES[kind]
    made = _client(fake).post(_CREATE, json=_auth(type=kind, head=head, lines=[line]))
    assert made.status_code == 200, made.text
    path, sent = fake.calls[-1]
    assert path == "/v1/vouchers/create"
    assert sent["type"] == kind
    assert sent["head"] == head
    assert sent["lines"] == [line]


def test_invoice_create_returns_generated_dispatch() -> None:
    head, line = _BODIES["sale_invoice"]
    made = _client(_MadeDispatch()).post(_CREATE, json=_auth(type="sale_invoice", head=head, lines=[line]))
    assert made.status_code == 200, made.text
    assert made.json()["dispatch_id"] == 77


@pytest.mark.parametrize(
    ("kind", "head", "line"),
    (
        ("dispatch", {"cCusCode": "C001"}, _SA_LINE),
        ("dispatch", _SA_HEAD, {"cInvCode": "A001", "iQuantity": 1}),
        ("dispatch", dict(_SA_HEAD, iSOsID=1), _SA_LINE),
        ("dispatch", _SA_HEAD, dict(_SA_LINE, iQuantity=0)),
        ("sale_invoice", dict(_SA_HEAD, cVouchType="28"), _SA_LINE),
        ("sale_invoice", _SA_HEAD, dict(_SA_LINE, iDLsID=5)),
        ("arrival", {"cMemo": "m"}, {"cInvCode": "A001", "iQuantity": 1}),
        ("arrival", {"cVenCode": "V001"}, {"cInvCode": "A001", "iQuantity": 1, "iPOsID": 3}),
        ("material_out", {"cRdCode": "103"}, {"cInvCode": "A001", "iQuantity": 1}),
        ("material_out", {"cWhCode": "01", "dDate": "2026/09/28"}, {"cInvCode": "A001", "iQuantity": 1}),
        ("material_out", {"cWhCode": "01"}, {"cInvCode": "A001", "iQuantity": 1, "iMPoIds": 9}),
        ("material_out", {"cWhCode": "01"}, {"cInvCode": "A001", "iQuantity": True}),
    ),
)
def test_srcless_create_rejects_bad_body_before_bridge(kind: str, head: dict, line: dict) -> None:
    fake = FakeBridge()
    refused = _client(fake).post(_CREATE, json=_auth(type=kind, head=head, lines=[line]))
    assert refused.status_code == 400, refused.text
    assert fake.calls == []


def test_product_in_from_production_order_forwards_source_type() -> None:
    fake = FakeBridge()
    lines = [{"source_line_id": 1000000051, "quantity": 2}]
    body = _auth(type="product_in", id=1000000052, source_type="production_order", head={"cWhCode": "10"}, lines=lines)
    made = _client(fake).post(_GEN, json=body)
    assert made.status_code == 200, made.text
    assert made.json()["source_type"] == "production_order"
    path, sent = fake.calls[-1]
    assert path == "/v1/vouchers/generate"
    assert sent["source_type"] == "production_order"
    assert sent["lines"] == lines


def test_product_in_generate_forwards_position() -> None:
    fake = FakeBridge()
    lines = [{"source_line_id": 1000000051, "quantity": 1, "cposition": "01"}]
    body = _auth(type="product_in", id=1000000052, source_type="production_order", head={"cWhCode": "01"}, lines=lines)
    made = _client(fake).post(_GEN, json=body)
    assert made.status_code == 200, made.text
    assert fake.calls[-1][1]["lines"] == lines


def test_product_in_from_production_order_keeps_one_line() -> None:
    fake = FakeBridge()
    lines = [{"source_line_id": 1, "quantity": 1}, {"source_line_id": 2, "quantity": 1}]
    body = _auth(type="product_in", id=1, source_type="production_order", head={"cWhCode": "10"}, lines=lines)
    refused = _client(fake).post(_GEN, json=body)
    assert refused.status_code == 400, refused.text
    assert fake.calls == []


def test_srcless_create_rejects_more_than_six_decimals() -> None:
    fake = FakeBridge()
    head, line = _BODIES["material_out"]
    refused = _client(fake).post(_CREATE, json=_auth(type="material_out", head=head, lines=[dict(line, iQuantity=0.1234567)]))
    assert refused.status_code == 400, refused.text
    assert fake.calls == []
