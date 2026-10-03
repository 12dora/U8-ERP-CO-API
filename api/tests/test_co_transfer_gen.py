"""调拨单参照调拨申请单（type=transfer，source_type=transfer_request）和申请单的核准数量 iTvChkQuantity。"""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.co_stmisc import check_stmisc_lines

_GEN = "/v1/co/vouchers/generate"
_HEAD = {"dTVDate": "2026-09-28", "cMemo": "参照申请", "cDefine1": "x"}
_LINES = [{"source_line_id": 7, "quantity": 1}, {"source_line_id": 8, "quantity": 2.5, "cbMemo": "备注"}]


def _gen(**extra) -> dict:
    body = {"type": "transfer", "id": 9, "source_type": "transfer_request", "head": _HEAD, "lines": _LINES}
    body.update(extra)
    return _auth(**body)


def test_generate_forwards_exact_keys() -> None:
    _forwards(_GEN, "/v1/vouchers/generate", _gen(), _sealed("type", "id", "source_type", "head", "lines"))


def test_generate_reaches_the_bridge_unchanged() -> None:
    fake = FakeBridge()
    done = _client(fake).post(_GEN, json=_gen())
    assert done.status_code == 200, done.text
    sent = fake.calls[-1][1]
    assert (sent["type"], sent["id"], sent["head"], sent["lines"]) == ("transfer", 9, _HEAD, _LINES)


def test_source_type_may_be_omitted() -> None:
    fake = FakeBridge()
    body = _gen()
    body.pop("source_type")
    done = _client(fake).post(_GEN, json=body)
    assert done.status_code == 200, done.text


_REJECTS = (
    _gen(head={"cOWhCode": "01"}),
    _gen(head={"cIWhCode": "01"}),
    _gen(head={"dTVDate": "2026/09/28"}),
    _gen(head={"cDefine17": "x"}),
    _gen(lines=None),
    _gen(lines=[{"source_line_id": n, "quantity": 1} for n in range(1, 202)]),
    _gen(lines=[{"source_line_id": 7, "quantity": 0}]),
    _gen(lines=[{"source_line_id": 7, "quantity": 1.1234567}]),
    _gen(lines=[{"source_line_id": 7, "quantity": 1, "cInvCode": "A"}]),
    _gen(lines=[{"source_line_id": 7, "quantity": 1}, {"source_line_id": 7, "quantity": 1}]),
    _gen(lines=[{"source_line_id": "7", "quantity": 1}]),
    _gen(source_type="purchase_order"),
    _auth(type="transfer_request", id=9, lines=_LINES),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_generate_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_GEN, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_request_accepts_approved_quantity() -> None:
    fake = FakeBridge()
    client = _client(fake)
    head = {"dTVDate": "2026-09-28", "cOWhCode": "01", "cIWhCode": "02"}
    lines = [{"cInvCode": "A001", "iTVQuantity": 3, "iTvChkQuantity": 2}]
    made = client.post("/v1/co/vouchers/create", json=_auth(type="transfer_request", head=head, lines=lines))
    assert made.status_code == 200, made.text
    edit = [{"op": "update", "line_id": 7, "iTvChkQuantity": 0}]
    done = client.post("/v1/co/vouchers/update", json=_auth(type="transfer_request", id=9, lines=edit))
    assert done.status_code == 200, done.text
    bad = [{"op": "update", "line_id": 7, "iTvChkQuantity": -1}]
    denied = client.post("/v1/co/vouchers/update", json=_auth(type="transfer_request", id=9, lines=bad))
    assert denied.status_code == 400, denied.text


def test_openapi_lists_transfer_generate() -> None:
    spec = make_client().get("/v1/openapi.json").json()
    schemas = spec["components"]["schemas"]
    assert "transfer" in schemas["CoGenerateIn"]["properties"]["type"]["enum"]
    assert "调拨申请单" in schemas["CoGenerateIn"]["properties"]["type"]["description"]


def test_generate_keys_are_case_insensitive() -> None:
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post(_GEN, json=_gen(head={"DTVDATE": "2026-09-28"}, lines=[{"Source_Line_Id": 5, "Quantity": 1}]))
    assert ok.status_code == 200, ok.text
    count = len(fake.calls)
    dup = client.post(_GEN, json=_gen(lines=[{"source_line_id": 5, "Source_Line_Id": 6, "quantity": 1}]))
    assert dup.status_code == 400, dup.text
    assert len(fake.calls) == count


def test_approved_quantity_not_above_requested() -> None:
    fake = FakeBridge()
    client = _client(fake)
    head = {"dTVDate": "2026-09-28", "cOWhCode": "01", "cIWhCode": "02"}
    lines = [{"cInvCode": "A001", "iTVQuantity": 1, "iTvChkQuantity": 2}]
    denied = client.post("/v1/co/vouchers/create", json=_auth(type="transfer_request", head=head, lines=lines))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []
    # API 对外只回「请求参数无效」，原因文字按函数核对。
    with pytest.raises(ValueError, match="核准数量不能大于申请数量"):
        check_stmisc_lines("transfer_request", lines)
    with pytest.raises(ValueError, match="核准数量必须大于或等于 0"):
        check_stmisc_lines("transfer_request", [{"iTvChkQuantity": -1}])
    with pytest.raises(ValueError, match="盘点数量必须大于或等于 0"):
        check_stmisc_lines("stock_check", [{"iCVCQuantity": -1}])

