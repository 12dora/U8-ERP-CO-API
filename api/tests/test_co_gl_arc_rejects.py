"""GL voucher, archive, list and stock bodies rejected before the bridge is called."""

from __future__ import annotations

import json

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth

_KEY = {"period": 9, "sign": "转", "no": 3}
_HEAD = {"sign": "转"}
_DEBIT = {"account": "660202", "digest": "报销", "debit": 1}
_CREDIT = {"account": "224199", "digest": "报销", "credit": 1}
_PAIR = [_DEBIT, _CREDIT]
_GEN = [{"source_line_id": 1, "quantity": 1}]
_GEN_21 = [{"source_line_id": n, "quantity": 1} for n in range(1, 22)]


def _key(**extra) -> dict:
    body = dict(_KEY)
    body.update(extra)
    return _auth(**body)


def _create(*lines: dict, **extra) -> dict:
    body = {"head": _HEAD, "lines": list(lines)}
    body.update(extra)
    return _auth(**body)


def _line(**extra) -> dict:
    row = dict(_DEBIT)
    row.update(extra)
    return row


_GL = "/v1/co/gl/vouchers/"
_ARC = "/v1/co/archives/"

_REJECTS = (
    (_GL + "load", _auth(sign="转", no=3)),
    (_GL + "load", _key(period=0)),
    (_GL + "load", _key(period=13)),
    (_GL + "void", _key(sign="")),
    (_GL + "void", _key(sign="转账凭")),
    (_GL + "void", _key(sign=" 转")),
    (_GL + "verify", _key(no=0)),
    (_GL + "verify", _key(no=32768)),
    (_GL + "sign", _key(id=1)),
    (_GL + "delete", _key(action="delete")),
    (_GL + "list", _auth(period_from=1)),
    (_GL + "list", _auth(period_from=10, period_to=9)),
    (_GL + "list", _auth(period_from=1, period_to=12, state="done")),
    (_GL + "list", _auth(period_from=1, period_to=12, limit=201)),
    (_GL + "list", _auth(period_from=1, period_to=12, limit=0)),
    (_GL + "list", _auth(period_from=1, period_to=12, after="")),
    (_GL + "list", _auth(period_from=1, period_to=12, date_from="2026-9-1")),
    (_GL + "list", _auth(period_from=1, period_to=12, date_from="2026-09-30", date_to="2026-09-01")),
    (_GL + "create", _auth(head=_HEAD, lines=[_DEBIT])),
    (_GL + "create", _auth(head=_HEAD, lines=[_DEBIT, _CREDIT] * 101)),
    (_GL + "create", _auth(lines=_PAIR)),
    (_GL + "create", _create(_DEBIT, dict(_CREDIT, credit=2))),
    (_GL + "create", _create(dict(_DEBIT, debit=-1), dict(_CREDIT, credit=-1))),
    (_GL + "create", _create(_line(credit=1), _CREDIT)),
    (_GL + "create", _create({"account": "660202", "digest": "报销"}, _CREDIT)),
    (_GL + "create", _create(_line(debit=0), dict(_CREDIT, credit=0))),
    (_GL + "create", _create(_line(debit="1"), _CREDIT)),
    (_GL + "create", _create(_line(debit=True), _CREDIT)),
    (_GL + "create", _create(_line(debit=float("nan")), dict(_CREDIT, credit=float("nan")))),
    (_GL + "create", _create(_line(debit=float("inf")), dict(_CREDIT, credit=float("inf")))),
    (_GL + "create", _create(_line(debit=10**12 + 1), dict(_CREDIT, credit=10**12 + 1))),
    (_GL + "create", _create(_line(digest=""), _CREDIT)),
    (_GL + "create", _create(_line(digest="x" * 121), _CREDIT)),
    (_GL + "create", _create(_line(account=""), _CREDIT)),
    (_GL + "create", _create(_line(dept="x" * 13), _CREDIT)),
    (_GL + "create", _create(_line(item_class="123"), _CREDIT)),
    (_GL + "create", _create(_line(doc_date="27/09/2026"), _CREDIT)),
    (_GL + "create", _create(_line(rate=0), _CREDIT)),
    (_GL + "create", _create(_line(memo="x"), _CREDIT)),
    (_GL + "create", _create(_line(cash_flow=[]), _CREDIT)),
    (_GL + "create", _create(_line(cash_flow=[{"item": "07"}]), _CREDIT)),
    (_GL + "create", _create(_line(cash_flow=[{"item": "07", "debit": 1, "credit": 1}]), _CREDIT)),
    (_GL + "create", _create(_line(cash_flow=[{"item": "", "debit": 1}]), _CREDIT)),
    (_GL + "create", _create(_DEBIT, _CREDIT, head={"sign": "转", "voucher_id": 1})),
    (_GL + "create", _create(_DEBIT, _CREDIT, head={"sign": "转", "attachments": -1})),
    (_GL + "create", _create(_DEBIT, _CREDIT, head={"sign": "转", "date": "2025-12-31"}, year="2025")),
    (_GL + "create", _create(_DEBIT, _CREDIT, head={"sign": "转", "date": "2024-12-31"}, year="2024")),
    (_GL + "create", _create(_DEBIT, _CREDIT, head={"sign": "转", "date": "2025-12-31"}, date="2026-01-05")),
    (_GL + "create", _create(_DEBIT, _CREDIT, period=9)),
    (_GL + "update", _create(_DEBIT, _CREDIT)),
    (_GL + "update", _auth(head=_HEAD, **_KEY)),
    (_ARC + "get", _auth(archive="salary", code="01")),
    (_ARC + "get", _auth(archive="customer")),
    (_ARC + "get", _auth(archive="customer", code="")),
    (_ARC + "get", _auth(archive="customer", code="C1 ")),
    (_ARC + "get", _auth(archive="customer", code="x" * 31)),
    (_ARC + "list", _auth(archive="customer", code_prefix="x" * 31)),
    (_ARC + "list", _auth(archive="customer", limit="5")),
    (_ARC + "get", _auth(archive="customer", code="x" * 61)),
    (_ARC + "delete", _auth(archive="customer", code="C1", fields={"name": "x"})),
    (_ARC + "create", _auth(archive="customer", code="C1")),
    (_ARC + "create", _auth(archive="customer", code="C1", fields={"code": "C2"})),
    (_ARC + "create", _auth(archive="customer", code="C1", fields={"Code": "C2"})),
    (_ARC + "create", _auth(archive="customer", code="C1", fields={"bad-tag": "x"})),
    (_ARC + "create", _auth(archive="customer", code="C1", fields={"1name": "x"})),
    (_ARC + "create", _auth(archive="customer", code="C1", fields={"name": {"a": 1}})),
    (_ARC + "create", _auth(archive="customer", code="C1", fields={"name": ["x"]})),
    (_ARC + "create", _auth(archive="customer", code="C1", fields={"name": "x" * 2001})),
    (_ARC + "create", _auth(archive="customer", code="C1", fields={f"t{i}": 1 for i in range(301)})),
    (_ARC + "create", _auth(archive="inventory", code="I1", fields={"name": "x"}, template="")),
    (_ARC + "update", _auth(archive="customer", code="C1", fields={})),
    (_ARC + "update", _auth(archive="customer", code="C1", fields={"name": None})),
    (_ARC + "update", _auth(archive="customer", code="C1", fields={"code": "C2"})),
    (_ARC + "update", _auth(archive="customer", code="C1", fields={"name": "x"}, template="C0")),
    (_ARC + "list", _auth()),
    (_ARC + "list", _auth(archive="customer", limit=501)),
    (_ARC + "list", _auth(archive="customer", changed_since="abc")),
    (_ARC + "list", _auth(archive="customer", changed_since="1" * 20)),
    (_ARC + "list", _auth(archive="customer", code="C1")),
    ("/v1/co/vouchers/list", _auth()),
    ("/v1/co/vouchers/list", _auth(type="gl")),
    ("/v1/co/vouchers/list", _auth(type="sale_order", limit=501)),
    ("/v1/co/vouchers/list", _auth(type="sale_order", after=-1)),
    ("/v1/co/vouchers/list", _auth(type="sale_order", after="C1")),
    ("/v1/co/vouchers/list", _auth(type="sale_order", changed_since="-1")),
    ("/v1/co/vouchers/list", _auth(type="sale_order", filter={"unknown": "x"})),
    ("/v1/co/vouchers/list", _auth(type="sale_order", filter={"ufts_after": "1"})),
    ("/v1/co/vouchers/list", _auth(type="sale_order", filter={"date_from": "2026-09-01T00:00"})),
    ("/v1/co/vouchers/list", _auth(type="sale_order", filter={"date_from": "2026-09-02", "date_to": "2026-09-01"})),
    ("/v1/co/vouchers/list", _auth(type="sale_order", filter={"code": "x" * 61})),
    ("/v1/co/vouchers/list", _auth(type="sale_order", filter={"cus_code": "a\nb"})),
    ("/v1/co/vouchers/list", _auth(type="sale_order", filter={"verified": "maybe"})),
    ("/v1/co/vouchers/list", _auth(type="sale_order", id=1)),
    ("/v1/co/stock/current", _auth(wh_code="01")),
    ("/v1/co/stock/current", _auth(inv="")),
    ("/v1/co/stock/current", _auth(limit=1000)),
    ("/v1/co/stock/current", _auth(after=-1)),
    ("/v1/co/vouchers/create", _auth(type="material_out", head={"a": "b"}, lines=[{"a": 1}])),
    ("/v1/co/vouchers/delete", _auth(type="qm_incoming_reject", id=0)),
    ("/v1/co/vouchers/verify", _auth(type="purchase_invoice", id=1, action="review")),
    # 参照产品检验单最多 20 行（合并检验一个来源一行）。
    ("/v1/co/vouchers/generate", _auth(type="product_in", id=1, lines=_GEN_21)),
    ("/v1/co/vouchers/generate", _auth(type="product_in", id=1)),
    ("/v1/co/vouchers/generate", _auth(type="material_out", id=1)),
    ("/v1/co/vouchers/generate", _auth(type="material_out", id=1, lines=[{"source_line_id": 1, "iQuantity": 1}])),
    ("/v1/co/vouchers/generate", _auth(type="material_out", id=1, head={"cCode": "CK1"}, lines=_GEN)),
    ("/v1/co/vouchers/generate", _auth(type="product_in", id=1, head={"ID": 9}, lines=_GEN)),
    ("/v1/co/vouchers/generate", _auth(type="ar_bill", id=1, lines=_GEN)),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_gl_arc_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    raw = json.dumps(body, allow_nan=True)
    denied = client.post(path, content=raw, headers={"content-type": "application/json"})
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []
