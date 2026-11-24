"""产品检验单合并检验（qm_product_check + source_ids）：API 层的形状校验与转发。来源是否可合并由桥查库核对。"""

from __future__ import annotations

import pytest
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards

_GEN = "/v1/co/vouchers/generate"
_HEAD = {"cCheckPersonCode": "P900001", "project_code": "DEMO-001", "fDtQuantity": 1}


def _merge(ids: list | None = None, head: dict | None = None, **extra) -> dict:
    body = {"type": "qm_product_check", "source_ids": [11, 12] if ids is None else ids}
    body["head"] = _HEAD if head is None else head
    body.update(extra)
    return _auth(**body)


_ACCEPTS = (
    _merge(),
    _merge(source_type="qm_product_inspect"),
    _merge(list(range(1, 21))),
    _merge(head=dict(_HEAD, items=[{"cChkItemCode": "I01", "cChkGuideCode": "G01"}])),
    _merge(lines=[{"quantity": 3, "fDisQuantity": 1}, {"source_line_id": 12, "fConQuantiy": 0.5}]),
    _merge(lines=[{}, {"quantity": 2, "fRegQuantity": 2}]),
    _merge(merge_rules=["0", 1, "8"]),
)

_REJECTS = (
    _merge([11]),
    _merge([11, 11]),
    _merge([11, 0]),
    _merge(list(range(1, 22))),
    _merge(head={}),
    _merge(head={"cCheckPersonCode": "P900001", "cWhCode": "01"}),
    _merge(id=7),
    _merge(lines=[{"quantity": 1}]),
    _merge(lines=[{"quantity": 1}, {"source_line_id": 99}]),
    _merge(lines=[{"quantity": 1, "cWhCode": "01"}, {}]),
    _merge(lines=[{"quantity": 1, "fDisQuantity": 2}, {}]),
    _merge(lines=[{"fConQuantiy": -1}, {}]),
    _merge(lines=[{"quantity": 0}, {}]),
    _merge(source_type="production_order"),
    _auth(type="qm_incoming_check", source_ids=[11, 12], head=_HEAD),
    _auth(type="product_in", source_ids=[11, 12]),
    _merge(merge_rules=["3"]),
    _merge(merge_rules=["9"]),
    _merge(merge_rules=["0", 0]),
    _merge(merge_rules=[]),
    _auth(type="qm_product_check", id=7, head=_HEAD, lines=[{"source_line_id": 1, "quantity": 1}], merge_rules=["0"]),
)


@pytest.mark.parametrize("body", _ACCEPTS)
def test_qm_merge_accepts(body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_GEN, json=body)
    assert ok.status_code == 200, ok.text
    sent = fake.calls[0][1]
    assert sent["source_ids"] == body["source_ids"]
    assert "id" not in sent


@pytest.mark.parametrize("body", _REJECTS)
def test_qm_merge_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_qm_merge_forwards_exact_keys() -> None:
    _forwards(_GEN, "/v1/vouchers/generate", _merge(), _sealed("type", "source_ids", "head"))
