"""库存与销售支持报表 /v1/co/reports/stock_ledger、stock_summary、position_stock、batch_stock、customer_credit、price_list：
转发的字段、提前拒绝、权限、审计和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line as _audit_line
from tests.support import base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec
from u8co_api.co_access import ACCESS, READ
from u8co_api.errors import ApiError

_R = "/v1/co/reports/"
_LEDGER = {"inv": "INV0001", "date_from": "2026-09-01"}
_SUMMARY = {"date_from": "2026-09-01"}
_NAMES = ("stock_ledger", "stock_summary", "position_stock", "batch_stock", "customer_credit", "price_list")


def _fwd(name: str, body: dict, *keys: str) -> tuple:
    return (_R + name, "/v1/reports/" + name, body, _sealed(*keys))


_FORWARD = (
    _fwd("stock_ledger", _auth(**_LEDGER), "inv", "date_from"),
    _fwd(
        "stock_ledger",
        _auth(wh="01", batch="B1", date_to="2026-09-28", include_unverified=True, after="opaque", limit=1000, **_LEDGER),
        "inv", "wh", "batch", "date_from", "date_to", "include_unverified", "after", "limit",
    ),
    _fwd("stock_summary", _auth(**_SUMMARY), "date_from"),
    _fwd(
        "stock_summary",
        _auth(date_to="2026-09-28", wh="01", inv="I1", inv_class="0102", by_wh=False, include_unverified=False,
              nonzero=False, after="x", limit=1, **_SUMMARY),
        "date_from", "date_to", "wh", "inv", "inv_class", "by_wh", "include_unverified", "nonzero", "after", "limit",
    ),
    _fwd("position_stock", _auth()),
    _fwd(
        "position_stock",
        _auth(wh="05", inv="I1", batch="B1", position="5F01", nonzero=False, after="MTM5MA", limit=5),
        "wh", "inv", "batch", "position", "nonzero", "after", "limit",
    ),
    _fwd("batch_stock", _auth(expiring_before="2026-12-31"), "expiring_before"),
    _fwd("batch_stock", _auth(wh="01", inv="I1", batch="B1", nonzero=True, limit=10), "wh", "inv", "batch", "nonzero", "limit"),
    _fwd("customer_credit", _auth()),
    _fwd(
        "customer_credit",
        _auth(customer=["C001", "C002"], controlled_only=True, after="QzAwMQ", limit=200),
        "customer", "controlled_only", "after", "limit",
    ),
    _fwd("price_list", _auth(kind="inventory"), "kind"),
    _fwd("price_list", _auth(kind="customer", customer="C001", inv="I1", as_of="2026-09-28"), "kind", "customer", "inv", "as_of"),
    _fwd("price_list", _auth(kind="vendor", vendor="V001", all_dates=True, limit=1000), "kind", "vendor", "all_dates", "limit"),
)

_REJECTS = (
    (_R + "stock_ledger", _auth(date_from="2026-09-01")),
    (_R + "stock_ledger", _auth(inv="I1")),
    (_R + "stock_ledger", _auth(inv="", date_from="2026-09-01")),
    (_R + "stock_ledger", _auth(inv="a\tb", date_from="2026-09-01")),
    (_R + "stock_ledger", _auth(date_from="2026-9-1", inv="I1")),
    (_R + "stock_ledger", _auth(date_from="2026-02-30", inv="I1")),
    (_R + "stock_ledger", _auth(date_to="2026-08-01", **_LEDGER)),
    (_R + "stock_ledger", _auth(include_unverified="true", **_LEDGER)),
    (_R + "stock_ledger", _auth(limit=1001, **_LEDGER)),
    (_R + "stock_ledger", _auth(after="", **_LEDGER)),
    (_R + "stock_ledger", _auth(nonzero=True, **_LEDGER)),
    (_R + "stock_summary", _auth()),
    (_R + "stock_summary", _auth(inv_class="01%", **_SUMMARY)),
    (_R + "stock_summary", _auth(by_wh=1, **_SUMMARY)),
    (_R + "stock_summary", _auth(position="5F", **_SUMMARY)),
    (_R + "position_stock", _auth(position="5F 01")),
    (_R + "position_stock", _auth(expiring_before="2026-12-31")),
    (_R + "position_stock", _auth(limit=0)),
    (_R + "batch_stock", _auth(expiring_before="2026-13-01")),
    (_R + "batch_stock", _auth(position="5F")),
    (_R + "customer_credit", _auth(customer=[])),
    (_R + "customer_credit", _auth(customer=["C"] * 21)),
    (_R + "customer_credit", _auth(customer="C001")),
    (_R + "customer_credit", _auth(customer=[" "])),
    (_R + "customer_credit", _auth(limit=201)),
    (_R + "customer_credit", _auth(controlled_only="yes")),
    (_R + "price_list", _auth()),
    (_R + "price_list", _auth(kind="purchase")),
    (_R + "price_list", _auth(kind="inventory", customer="C001")),
    (_R + "price_list", _auth(kind="customer", vendor="V001")),
    (_R + "price_list", _auth(kind="vendor", as_of="2026-09-28", all_dates=True)),
    (_R + "price_list", _auth(kind="vendor", as_of="2026/09/28")),
)

_OPENAPI = {
    "stock_ledger": ("库存台账", "coReportStockLedger"),
    "stock_summary": ("收发存汇总表", "coReportStockSummary"),
    "position_stock": ("货位存量", "coReportPositionStock"),
    "batch_stock": ("批次存量", "coReportBatchStock"),
    "customer_credit": ("客户信用", "coReportCustomerCredit"),
    "price_list": ("价格表", "coReportPriceList"),
}

_MINIMAL = {
    "stock_ledger": _auth(**_LEDGER),
    "stock_summary": _auth(**_SUMMARY),
    "position_stock": _auth(),
    "batch_stock": _auth(),
    "customer_credit": _auth(),
    "price_list": _auth(kind="customer"),
}


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_stock_reports_forward_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
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
def test_stock_reports_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _StockBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        if path == "/v1/reports/stock_ledger":
            row = {"date": "2026-09-02", "type": "material_out", "in_qty": 0.0, "out_qty": 580.0, "balance": 1.5, "x": 1}
            return {"ok": True, "inv": "I1", "opening": 581.5, "carry": 581.5, "closing": None, "items": [row], "next": "n"}
        if path == "/v1/reports/customer_credit":
            row = {"code": "C001", "used": 10.0, "available": None, "order": 10.0}
            return {"ok": True, "check_point": "save", "formula": {"order": True}, "unsupported": [], "items": [row], "next": None}
        return {"ok": True, "items": [{"id": 1, "levels": [{"level": 1, "price": 2.5, "tax_price": None}]}], "next": None}


def test_stock_report_responses_keep_bridge_fields() -> None:
    client = _client(_StockBridge())
    ledger = client.post(_R + "stock_ledger", json=_auth(**_LEDGER)).json()
    assert ledger.get("closing") is None  # 路由 response_model_exclude_none：null 字段不输出
    assert ledger["items"][0]["x"] == 1
    assert ledger["items"][0]["balance"] == 1.5
    credit = client.post(_R + "customer_credit", json=_auth()).json()
    assert credit["formula"] == {"order": True}
    assert credit["items"][0]["available"] is None
    prices = client.post(_R + "price_list", json=_auth(kind="inventory")).json()
    assert prices["items"][0]["levels"][0]["price"] == 2.5


def test_stock_ledger_unknown_inventory_is_404() -> None:
    fake = FakeBridge()
    fake.error = ApiError(404, "not_found", "存货不存在")
    missing = _client(fake).post(_R + "stock_ledger", json=_auth(**_LEDGER))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("name", _NAMES)
def test_stock_reports_are_read_only_and_gated(name: str) -> None:
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


@pytest.mark.parametrize(
    ("name", "body", "view"),
    (
        ("stock_ledger", _auth(**_LEDGER), "co:reports/stock_ledger:stock_ledger#INV0001"),
        ("price_list", _auth(kind="vendor"), "co:reports/price_list:price_list#vendor"),
        ("stock_summary", _auth(**_SUMMARY), "co:reports/stock_summary:stock_summary"),
    ),
)
def test_stock_report_audit_names_the_report(name: str, body: dict, view: str, capsys) -> None:
    ok = _client().post(_R + name, json=body)
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert _audit_line(out, _R + name)["action"] == view


def test_stock_reports_openapi_paths_summaries_and_tags() -> None:
    spec = _spec()
    for name, (summary, operation_id) in _OPENAPI.items():
        operation = spec["paths"][_R + name]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert operation["tags"] == ["报表"]
        assert "权限：只读" in operation["description"]
    assert set(_enum(spec, "ReportPriceListIn", "kind")) == {"customer", "inventory", "vendor"}
