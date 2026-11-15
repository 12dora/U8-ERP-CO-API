"""/v1/co update / close / generate bodies rejected before the bridge is called."""

from __future__ import annotations

import json

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth

_LINE = [{"source_line_id": 1, "quantity": 1}]

_REJECTS = (
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1)),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, head={})),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, head={}, lines=[])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "move", "line_id": 1}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "ADD", "cInvCode": "A"}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"line_id": 1, "iQuantity": 2}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": 1, "line_id": 1}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "update", "iQuantity": 2}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "delete"}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "add", "line_id": 1, "cInvCode": "A"}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "delete", "line_id": 1, "cMemo": "x"}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "update", "line_id": 1}])),
    (
        "/v1/co/vouchers/update",
        _auth(
            type="sale_order",
            id=1,
            lines=[{"op": "update", "line_id": 5, "iQuantity": 2}, {"op": "delete", "line_id": 5}],
        ),
    ),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "update", "line_id": 0, "iQuantity": 1}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "delete", "line_id": -1}])),
    (
        "/v1/co/vouchers/update",
        _auth(type="sale_order", id=1, lines=[{"op": "update", "line_id": 2147483648, "iQuantity": 1}]),
    ),
    (
        "/v1/co/vouchers/update",
        _auth(type="sale_order", id=1, lines=[{"op": "update", "line_id": "1", "iQuantity": 1}]),
    ),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "update", "line_id": 1.5, "qty": 1}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "update", "line_id": True, "qty": 1}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=["x"])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "add", "cMemo": {"a": 1}}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "add", "cInvCode": ["A"]}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, head={"cMemo": ["x"]})),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, head="x")),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, head={"cMaker": "张三"})),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "update", "line_id": 1, "autoid": 9}])),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, head={"cSOCode": "SO1"})),
    ("/v1/co/vouchers/update", _auth(type="other_in", id=1, head={"cCode": "QC1"})),
    ("/v1/co/vouchers/update", _auth(type="transfer", id=1, head={"cTVCode": "DB1"})),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, head={"": "x"})),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, lines=[{"op": "add", "cInvCode": "A"}] * 201)),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, head={"cMemo": "a"}, note="x")),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=0, head={"cMemo": "a"})),
    ("/v1/co/vouchers/close", _auth(type="other_in", id=1, action="close")),
    ("/v1/co/vouchers/close", _auth(type="transfer", id=1, action="open")),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1)),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="shut")),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close", line_ids=[])),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close", line_ids=list(range(1, 202)))),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close", line_ids=[1, 2, 1])),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close", line_ids=[0])),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close", line_ids=[-1])),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close", line_ids=[2147483648])),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close", extra=1)),
    ("/v1/co/vouchers/generate", _auth(type="sale_order", id=1, lines=_LINE)),
    ("/v1/co/vouchers/generate", _auth(type="sale_out", id=1, lines=[])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1)),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[])),
    ("/v1/co/vouchers/generate", _auth(type="purchase_in", id=1)),
    ("/v1/co/vouchers/generate", _auth(type="sale_invoice", id=1)),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=["x"])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"quantity": 1}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": 1}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": 0, "quantity": 1}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": -1, "quantity": 1}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": 1.5, "quantity": 1}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": "1", "quantity": 1}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": True, "quantity": 1}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": 2147483648, "quantity": 1}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": 1, "quantity": 0}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": 1, "quantity": -1}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": 1, "quantity": 10**12 + 1}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": 1, "quantity": "1"}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": 1, "quantity": True}])),
    (
        "/v1/co/vouchers/generate",
        _auth(
            type="dispatch",
            id=1,
            lines=[{"source_line_id": 4, "quantity": 1}, {"source_line_id": 4, "quantity": 2}],
        ),
    ),
    (
        "/v1/co/vouchers/generate",
        _auth(type="dispatch", id=1, lines=[{"source_line_id": i, "quantity": 1} for i in range(1, 202)]),
    ),
    (
        "/v1/co/vouchers/generate",
        _auth(type="dispatch", id=1, lines=[{"source_line_id": 1, "quantity": 1, "cMaker": "张"}]),
    ),
    (
        "/v1/co/vouchers/generate",
        _auth(type="dispatch", id=1, head={"cDLCode": "FH1"}, lines=_LINE),
    ),
    (
        "/v1/co/vouchers/generate",
        _auth(type="dispatch", id=1, lines=[{"source_line_id": 1, "quantity": 1, "cBatch": {"a": 1}}]),
    ),
    ("/v1/co/vouchers/generate", _auth(type="purchase_in", id=1, head={"": "x"}, lines=_LINE)),
    ("/v1/co/vouchers/generate", _auth(type="sale_out", id=1, note="x")),
    ("/v1/co/vouchers/verify", _auth(type="purchase_invoice", id=1, action="review")),
    ("/v1/co/vouchers/verify", _auth(type="production_order", id=1, action="close")),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, head={"ID": 9})),
    ("/v1/co/vouchers/update", _auth(type="purchase_order", id=1, head={"POID": 9})),
    ("/v1/co/vouchers/update", _auth(type="other_in", id=1, head={"ID": 9})),
    ("/v1/co/vouchers/update", _auth(type="other_out", id=1, head={"ID": 9})),
    ("/v1/co/vouchers/update", _auth(type="transfer", id=1, head={"ID": 9})),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, head={"DLID": 9}, lines=_LINE)),
    ("/v1/co/vouchers/generate", _auth(type="purchase_in", id=1, head={"ID": 9}, lines=_LINE)),
    ("/v1/co/vouchers/generate", _auth(type="sale_out", id=1, head={"ID": 9})),
    ("/v1/co/vouchers/generate", _auth(type="sale_invoice", id=1, head={"SBVID": 9}, lines=_LINE)),
    ("/v1/co/vouchers/generate", _auth(type="sale_invoice", id=1, head={"sbvid": 9}, lines=_LINE)),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": 1, "quantity": float("nan")}])),
    ("/v1/co/vouchers/generate", _auth(type="dispatch", id=1, lines=[{"source_line_id": 1, "quantity": float("inf")}])),
    (
        "/v1/co/vouchers/generate",
        _auth(type="sale_invoice", id=1, lines=[{"source_line_id": 1, "quantity": float("-inf")}]),
    ),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close", line_ids=None)),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close", line_ids=[True])),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="open", line_ids=["1"])),
    ("/v1/co/vouchers/close", _auth(type="purchase_order", id=1, action="close", line_ids=[1.0])),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close", line_ids=[1.5])),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_update_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    # NaN / Infinity 不是标准 JSON，httpx 的 json= 会先拒绝；这里直接发原始文本，验证服务端也拒绝。
    raw = json.dumps(body, allow_nan=True)
    denied = client.post(path, content=raw, headers={"content-type": "application/json"})
    assert denied.status_code == 400
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []
