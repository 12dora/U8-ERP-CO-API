"""产成品入库参照产品不良品处理单（qm_product_reject）的 /v1/co/vouchers/generate。桥是本地假桥。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth

_PATH = "/v1/co/vouchers/generate"
_LINES = [{"source_line_id": 42, "quantity": 15551, "cbatch": "B1"}]


def test_product_in_from_reject_forwards_source_type() -> None:
    fake = FakeBridge()
    body = _auth(type="product_in", id=42, source_type="qm_product_reject", head={"cWhCode": "10"}, lines=_LINES)
    made = _client(fake).post(_PATH, json=body)
    assert made.status_code == 200, made.text
    assert made.json()["source_type"] == "qm_product_reject"
    assert made.json()["source_id"] == 42
    path, sent = fake.calls[-1]
    assert path == "/v1/vouchers/generate"
    assert sent["source_type"] == "qm_product_reject"
    assert sent["lines"] == _LINES


def test_product_in_default_source_is_still_the_check() -> None:
    fake = FakeBridge()
    made = _client(fake).post(_PATH, json=_auth(type="product_in", id=9, lines=[{"source_line_id": 9, "quantity": 1}]))
    assert made.status_code == 200, made.text
    assert made.json()["source_type"] == "qm_product_check"


@pytest.mark.parametrize(
    ("kind", "source"),
    (("product_in", "qm_incoming_reject"), ("material_out", "qm_product_reject"), ("purchase_in", "qm_product_reject")),
)
def test_reject_source_only_for_product_in(kind: str, source: str) -> None:
    fake = FakeBridge()
    lines = [{"source_line_id": 1, "quantity": 1}]
    refused = _client(fake).post(_PATH, json=_auth(type=kind, id=1, source_type=source, lines=lines))
    assert refused.status_code == 400, refused.text
    assert fake.calls == []


def test_product_in_from_reject_keeps_one_line() -> None:
    fake = FakeBridge()
    lines = [{"source_line_id": 1, "quantity": 1}, {"source_line_id": 2, "quantity": 1}]
    refused = _client(fake).post(
        _PATH, json=_auth(type="product_in", id=1, source_type="qm_product_reject", lines=lines)
    )
    assert refused.status_code == 400, refused.text
    assert fake.calls == []


def test_product_in_from_merged_check_forwards_lines() -> None:
    """合并检验的产品检验单一个来源一行，API 放行多行，非合并检验只能 1 行由桥判断。"""
    fake = FakeBridge()
    lines = [{"source_line_id": 1500000552, "quantity": 1}, {"source_line_id": 1500000551, "quantity": 2}]
    body = _auth(type="product_in", id=6751, head={"cWhCode": "10", "cRdCode": "203"}, lines=lines)
    made = _client(fake).post(_PATH, json=body)
    assert made.status_code == 200, made.text
    assert fake.calls[-1][1]["lines"] == lines
