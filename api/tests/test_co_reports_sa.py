"""销售类报表 /v1/co/reports/open_orders、sales_price_history、customer_churn：模型校验（缺省、上限、枚举、必填、未知字段）、
转发的字段、提前拒绝、响应字段原样返回、权限和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.support import base_claims
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_update_close_gen import _enum, _spec
from u8co_api.co_access import ACCESS, READ
from u8co_api.co_models_reports_sa import (
    CUSTOMER_CHURN_DOC,
    CUSTOMER_CHURN_SUMMARY,
    OPEN_ORDERS_DOC,
    OPEN_ORDERS_SUMMARY,
    SALES_PRICE_HISTORY_DOC,
    SALES_PRICE_HISTORY_SUMMARY,
    CustomerChurnIn,
    CustomerChurnOut,
    OpenOrdersIn,
    OpenOrdersOut,
    SalesPriceHistoryIn,
    SalesPriceHistoryOut,
)

_R = "/v1/co/reports/"
_NAMES = ("open_orders", "sales_price_history", "customer_churn")
_LOGIN = {"acc": "801", "operator": "op001", "password": "pw"}
_PRICE = {"date_from": "2026-09-01", "inv": ["9909001"]}
_CHURN = {"date_from": "2026-07-01"}


def _fwd(name: str, body: dict, *keys: str) -> tuple:
    return (_R + name, "/v1/reports/" + name, body, _sealed(*keys))


_FORWARD = (
    _fwd("open_orders", _auth()),
    _fwd(
        "open_orders",
        _auth(
            basis="invoice",
            group_by="order",
            customer=["C900001", "C900002"],
            inv=["9909001"],
            person=["P001"],
            date_from="2026-01-01",
            date_to="2026-09-30",
            due_before="2026-12-31",
            limit=1000,
        ),
        "basis", "group_by", "customer", "inv", "person", "date_from", "date_to", "due_before", "limit",
    ),
    _fwd("sales_price_history", _auth(**_PRICE), "date_from", "inv"),
    _fwd(
        "sales_price_history",
        _auth(customer=["C900001"], date_to="2026-09-30", include_unverified=True, limit=2000, **_PRICE),
        "customer", "inv", "date_from", "date_to", "include_unverified", "limit",
    ),
    _fwd("customer_churn", _auth(**_CHURN), "date_from"),
    _fwd(
        "customer_churn",
        _auth(date_to="2026-09-30", lookback_months=24, decline_pct=99, limit=200, **_CHURN),
        "date_from", "date_to", "lookback_months", "decline_pct", "limit",
    ),
)

_REJECTS = (
    (_R + "open_orders", _auth(basis="billed")),
    (_R + "open_orders", _auth(group_by="person")),
    (_R + "open_orders", _auth(customer=[])),
    (_R + "open_orders", _auth(customer="C900001")),
    (_R + "open_orders", _auth(customer=["C"] * 21)),
    (_R + "open_orders", _auth(inv=[" "])),
    (_R + "open_orders", _auth(person=["a\tb"])),
    (_R + "open_orders", _auth(date_from="2026-9-1")),
    (_R + "open_orders", _auth(date_from="2026-09-30", date_to="2026-09-01")),
    (_R + "open_orders", _auth(due_before="2026-02-30")),
    (_R + "open_orders", _auth(limit=0)),
    (_R + "open_orders", _auth(limit=1001)),
    (_R + "open_orders", _auth(after="x")),
    (_R + "open_orders", _auth(include_unverified=True)),
    (_R + "sales_price_history", _auth(date_from="2026-09-01")),
    (_R + "sales_price_history", _auth(inv=["9909001"])),
    (_R + "sales_price_history", _auth(date_from="2026-02-30", inv=["9909001"])),
    (_R + "sales_price_history", _auth(date_to="2026-08-31", **_PRICE)),
    (_R + "sales_price_history", _auth(customer=[], **_PRICE)),
    (_R + "sales_price_history", _auth(inv="9909001", date_from="2026-09-01")),
    (_R + "sales_price_history", _auth(include_unverified="true", **_PRICE)),
    (_R + "sales_price_history", _auth(limit=2001, **_PRICE)),
    (_R + "sales_price_history", _auth(after="x", **_PRICE)),
    (_R + "customer_churn", _auth()),
    (_R + "customer_churn", _auth(date_from="2026-13-01")),
    (_R + "customer_churn", _auth(date_to="2026-06-30", **_CHURN)),
    (_R + "customer_churn", _auth(lookback_months=0, **_CHURN)),
    (_R + "customer_churn", _auth(lookback_months=25, **_CHURN)),
    (_R + "customer_churn", _auth(decline_pct=0, **_CHURN)),
    (_R + "customer_churn", _auth(decline_pct=100, **_CHURN)),
    (_R + "customer_churn", _auth(limit=201, **_CHURN)),
    (_R + "customer_churn", _auth(limit="50", **_CHURN)),
    (_R + "customer_churn", _auth(customer=["C900001"], **_CHURN)),
)

_OPENAPI = {
    "open_orders": ("在手订单", "coReportOpenOrders"),
    "sales_price_history": ("销售价格历史", "coReportSalesPriceHistory"),
    "customer_churn": ("客户变动", "coReportCustomerChurn"),
}

_MINIMAL = {
    "open_orders": _auth(),
    "sales_price_history": _auth(**_PRICE),
    "customer_churn": _auth(**_CHURN),
}


def test_open_orders_defaults_and_values() -> None:
    minimal = OpenOrdersIn(**_LOGIN)
    assert minimal.basis is None
    assert minimal.group_by is None
    assert minimal.date_from is None
    assert minimal.limit is None
    full = OpenOrdersIn(
        **_LOGIN,
        basis="out",
        group_by="inventory",
        customer=["C900001"],
        inv=["9909001"],
        person=["P001"],
        date_from="2026-09-01",
        date_to="2026-09-01",
        due_before="2026-12-31",
        limit=1,
    )
    assert full.basis == "out"
    assert full.group_by == "inventory"
    assert OpenOrdersIn(**_LOGIN, date_to="2026-09-01").date_from is None


@pytest.mark.parametrize(
    "extra",
    (
        {"basis": "billed"},
        {"group_by": "week"},
        {"customer": []},
        {"customer": ["C"] * 21},
        {"inv": "9909001"},
        {"person": [" "]},
        {"date_from": "2026-09-31"},
        {"date_from": "2026-09-02", "date_to": "2026-09-01"},
        {"due_before": "2026-9-1"},
        {"limit": 0},
        {"limit": 1001},
        {"limit": True},
        {"unknown": 1},
    ),
)
def test_open_orders_in_rejects(extra: dict) -> None:
    with pytest.raises(ValidationError):
        OpenOrdersIn(**_LOGIN, **extra)


def test_sales_price_history_defaults_and_values() -> None:
    minimal = SalesPriceHistoryIn(**_LOGIN, **_PRICE)
    assert minimal.customer is None
    assert minimal.date_to is None
    assert minimal.include_unverified is None
    assert minimal.limit is None
    full = SalesPriceHistoryIn(
        **_LOGIN, date_from="2026-09-01", date_to="2026-09-01", customer=["C900001"], include_unverified=False, limit=2000
    )
    assert full.customer == ["C900001"]
    assert full.limit == 2000
    assert SalesPriceHistoryIn(**_LOGIN, date_from="2026-09-01", customer=["C900001"]).inv is None


@pytest.mark.parametrize(
    "extra",
    (
        {},
        {"date_from": "2026-09-01"},
        {"inv": ["9909001"]},
        {"date_from": "2026-13-01", "inv": ["9909001"]},
        {"date_from": "2026-09-01", "date_to": "2026-08-31", "inv": ["9909001"]},
        {"date_from": "2026-09-01", "inv": []},
        {"date_from": "2026-09-01", "inv": ["I"] * 21},
        {"date_from": "2026-09-01", "customer": "C900001"},
        {"date_from": "2026-09-01", "inv": ["9909001"], "include_unverified": "true"},
        {"date_from": "2026-09-01", "inv": ["9909001"], "limit": 0},
        {"date_from": "2026-09-01", "inv": ["9909001"], "limit": 2001},
        {"date_from": "2026-09-01", "inv": ["9909001"], "unknown": 1},
    ),
)
def test_sales_price_history_in_rejects(extra: dict) -> None:
    with pytest.raises(ValidationError):
        SalesPriceHistoryIn(**_LOGIN, **extra)


def test_customer_churn_defaults_and_values() -> None:
    minimal = CustomerChurnIn(**_LOGIN, **_CHURN)
    assert minimal.date_to is None
    assert minimal.lookback_months is None
    assert minimal.decline_pct is None
    assert minimal.limit is None
    full = CustomerChurnIn(
        **_LOGIN, date_from="2026-07-01", date_to="2026-07-01", lookback_months=1, decline_pct=1, limit=1
    )
    assert (full.lookback_months, full.decline_pct, full.limit) == (1, 1, 1)


@pytest.mark.parametrize(
    "extra",
    (
        {},
        {"date_from": "2026-02-30"},
        {"date_from": "2026-07-01", "date_to": "2026-06-30"},
        {"date_from": "2026-07-01", "lookback_months": 0},
        {"date_from": "2026-07-01", "lookback_months": 25},
        {"date_from": "2026-07-01", "lookback_months": 1.5},
        {"date_from": "2026-07-01", "decline_pct": 0},
        {"date_from": "2026-07-01", "decline_pct": 100},
        {"date_from": "2026-07-01", "limit": 0},
        {"date_from": "2026-07-01", "limit": 201},
        {"date_from": "2026-07-01", "customer": ["C900001"]},
    ),
)
def test_customer_churn_in_rejects(extra: dict) -> None:
    with pytest.raises(ValidationError):
        CustomerChurnIn(**_LOGIN, **extra)


def test_out_models_keep_bridge_fields() -> None:
    open_orders = OpenOrdersOut(
        ok=True,
        items=[{"customer_code": "C900001", "lines": 2, "open_qty": 5.0, "open_amount": 10.0, "x": 1}],
        totals={"lines": 2, "open_amount": 10.0, "customers": 1},
        extra_field=True,
    )
    assert open_orders.model_dump()["items"][0]["x"] == 1
    assert open_orders.model_dump()["extra_field"] is True
    price = SalesPriceHistoryOut(ok=True, items=[{"unit_price": None, "currency": "CNY"}], count=1, truncated=False)
    assert price.model_dump()["items"][0]["unit_price"] is None
    churn = CustomerChurnOut(ok=True, new=[], lost=[], declining=[], counts={"active": 0}, windows={}, other=1)
    assert churn.model_dump()["other"] == 1


def test_summaries_and_docs_are_filled() -> None:
    assert OPEN_ORDERS_SUMMARY == "在手订单"
    assert SALES_PRICE_HISTORY_SUMMARY == "销售价格历史"
    assert CUSTOMER_CHURN_SUMMARY == "客户变动"
    assert "basis" in OPEN_ORDERS_DOC
    assert "unit_price" in SALES_PRICE_HISTORY_DOC
    assert "decline_pct" in CUSTOMER_CHURN_DOC
    for text in (OPEN_ORDERS_DOC, SALES_PRICE_HISTORY_DOC, CUSTOMER_CHURN_DOC):
        assert "只查数据库" in text


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_sa_reports_forward_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + bridge
    assert set(sent) == keys
    assert _SECRET.encode() not in raw


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_sa_reports_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _SaBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        if path == "/v1/reports/open_orders":
            row = {"customer_code": "C900001", "customer_name": "张三", "lines": 2, "open_qty": 5.0, "open_amount": 10.0}
            return {
                "ok": True,
                "basis": "ship",
                "group_by": "customer",
                "as_of": "2026-09-30",
                "items": [dict(row, overdue_lines=1, earliest_due="2026-09-01")],
                "count": 1,
                "truncated": False,
                "totals": {"lines": 2, "open_amount": 10.0, "customers": 1},
            }
        if path == "/v1/reports/sales_price_history":
            row = {"date": "2026-09-02", "code": "INV0001", "qty": 0.0, "amount": 0.0, "unit_price": None, "currency": "CNY"}
            return {"ok": True, "date_from": "2026-09-01", "date_to": "2026-09-30", "items": [row], "count": 1, "truncated": False}
        row = {"customer_code": "C900002", "revenue_current": 0.0, "revenue_baseline": 12.5, "months_baseline": 2}
        return {
            "ok": True,
            "new": [],
            "lost": [row],
            "declining": [],
            "counts": {"new": 0, "lost": 1, "declining": 0, "active": 3},
            "windows": {"current_from": "2026-07-01", "current_to": "2026-09-30",
                        "baseline_from": "2026-01-01", "baseline_to": "2026-06-30"},
        }


def test_sa_report_responses_keep_bridge_fields() -> None:
    client = _client(_SaBridge())
    orders = client.post(_R + "open_orders", json=_MINIMAL["open_orders"]).json()
    assert orders["items"][0]["overdue_lines"] == 1
    assert orders["totals"]["customers"] == 1
    assert orders["truncated"] is False
    price = client.post(_R + "sales_price_history", json=_MINIMAL["sales_price_history"]).json()
    assert price["items"][0]["code"] == "INV0001"
    assert price["count"] == 1
    churn = client.post(_R + "customer_churn", json=_MINIMAL["customer_churn"]).json()
    assert churn["lost"][0]["revenue_baseline"] == 12.5
    assert churn["counts"]["active"] == 3
    assert churn["windows"]["baseline_from"] == "2026-01-01"


@pytest.mark.parametrize("name", _NAMES)
def test_sa_reports_are_read_only_and_gated(name: str) -> None:
    assert ACCESS["co:reports/" + name] == READ
    body = _MINIMAL[name]
    fake = FakeBridge()
    assert _client(fake, co_enabled=False).post(_R + name, json=body).status_code == 404
    assert _client(fake, claims=base_claims()).post(_R + name, json=body).status_code == 403
    outside = _client(fake).post(_R + name, json=dict(body, acc="001"))
    assert outside.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(_R + name, json=body)
    assert reader.status_code == 200, reader.text
    assert fake.calls[-1][0] == "/v1/reports/" + name


def test_sa_reports_openapi_paths_summaries_and_tags() -> None:
    spec = _spec()
    for name, (summary, operation_id) in _OPENAPI.items():
        operation = spec["paths"][_R + name]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert operation["tags"] == ["报表"]
        assert "权限：只读" in operation["description"]
    assert set(_enum(spec, "OpenOrdersIn", "basis")) == {"ship", "out", "invoice"}
    assert set(_enum(spec, "OpenOrdersIn", "group_by")) == {"customer", "inventory", "order"}
