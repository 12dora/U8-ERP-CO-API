"""采购类报表 /v1/co/reports/purchase_analysis、arrival_otif：模型校验（缺省、上限、枚举、必填、未知字段）、
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
from u8co_api.co_models_reports_pu import (
    ARRIVAL_OTIF_DOC,
    ARRIVAL_OTIF_SUMMARY,
    PURCHASE_ANALYSIS_DOC,
    PURCHASE_ANALYSIS_SUMMARY,
    ArrivalOtifIn,
    ArrivalOtifOut,
    PurchaseAnalysisIn,
    PurchaseAnalysisOut,
)

_R = "/v1/co/reports/"
_NAMES = ("purchase_analysis", "arrival_otif")
_LOGIN = {"acc": "801", "operator": "op001", "password": "pw"}
_PURCHASE = {"date_from": "2026-09-01"}
_ARRIVAL = {"date_from": "2026-09-01"}


def _fwd(name: str, body: dict, *keys: str) -> tuple:
    return (_R + name, "/v1/reports/" + name, body, _sealed(*keys))


_FORWARD = (
    _fwd("purchase_analysis", _auth(**_PURCHASE), "date_from"),
    _fwd(
        "purchase_analysis",
        _auth(
            date_to="2026-09-30",
            group_by=["vendor", "month", "material"],
            inv=["9909001", "9909002"],
            inv_class="01",
            vendor=["S900001"],
            source="receipt",
            limit=1000,
            **_PURCHASE,
        ),
        "date_from",
        "date_to",
        "group_by",
        "inv",
        "inv_class",
        "vendor",
        "source",
        "limit",
    ),
    _fwd("arrival_otif", _auth(**_ARRIVAL), "date_from"),
    _fwd(
        "arrival_otif",
        _auth(
            date_to="2026-09-30", vendor=["S900001"], inv=["9909001"], group_by="none", overdue_limit=200, **_ARRIVAL
        ),
        "date_from",
        "date_to",
        "vendor",
        "inv",
        "group_by",
        "overdue_limit",
    ),
)

_REJECTS = (
    (_R + "purchase_analysis", _auth()),
    (_R + "purchase_analysis", _auth(date_from="2026-9-1")),
    (_R + "purchase_analysis", _auth(date_from="2026-02-30")),
    (_R + "purchase_analysis", _auth(date_to="2026-08-31", **_PURCHASE)),
    (_R + "purchase_analysis", _auth(group_by=[], **_PURCHASE)),
    (_R + "purchase_analysis", _auth(group_by="material", **_PURCHASE)),
    (_R + "purchase_analysis", _auth(group_by=["day"], **_PURCHASE)),
    (_R + "purchase_analysis", _auth(group_by=["vendor", "vendor"], **_PURCHASE)),
    (_R + "purchase_analysis", _auth(group_by=["material", "vendor", "month", "month"], **_PURCHASE)),
    (_R + "purchase_analysis", _auth(source="order", **_PURCHASE)),
    (_R + "purchase_analysis", _auth(limit=0, **_PURCHASE)),
    (_R + "purchase_analysis", _auth(limit=1001, **_PURCHASE)),
    (_R + "purchase_analysis", _auth(limit="5", **_PURCHASE)),
    (_R + "purchase_analysis", _auth(inv=[], **_PURCHASE)),
    (_R + "purchase_analysis", _auth(inv=["I"] * 21, **_PURCHASE)),
    (_R + "purchase_analysis", _auth(inv="9909001", **_PURCHASE)),
    (_R + "purchase_analysis", _auth(vendor=[" "], **_PURCHASE)),
    (_R + "purchase_analysis", _auth(inv_class="01%", **_PURCHASE)),
    (_R + "purchase_analysis", _auth(after="x", **_PURCHASE)),
    (_R + "purchase_analysis", _auth(include_unverified=True, **_PURCHASE)),
    (_R + "arrival_otif", _auth()),
    (_R + "arrival_otif", _auth(date_to="2026-08-31", **_ARRIVAL)),
    (_R + "arrival_otif", _auth(group_by="month", **_ARRIVAL)),
    (_R + "arrival_otif", _auth(group_by=["vendor"], **_ARRIVAL)),
    (_R + "arrival_otif", _auth(overdue_limit=0, **_ARRIVAL)),
    (_R + "arrival_otif", _auth(overdue_limit=201, **_ARRIVAL)),
    (_R + "arrival_otif", _auth(vendor="S900001", **_ARRIVAL)),
    (_R + "arrival_otif", _auth(inv=["I"] * 21, **_ARRIVAL)),
    (_R + "arrival_otif", _auth(inv_class="01", **_ARRIVAL)),
    (_R + "arrival_otif", _auth(limit=10, **_ARRIVAL)),
)

_OPENAPI = {
    "purchase_analysis": ("采购综合统计", "coReportPurchaseAnalysis"),
    "arrival_otif": ("采购到货及时率", "coReportArrivalOtif"),
}

_MINIMAL = {"purchase_analysis": _auth(**_PURCHASE), "arrival_otif": _auth(**_ARRIVAL)}


def test_purchase_analysis_defaults_and_values() -> None:
    minimal = PurchaseAnalysisIn(**_LOGIN, **_PURCHASE)
    assert minimal.date_to is None
    assert minimal.group_by is None
    assert minimal.source is None
    assert minimal.limit is None
    full = PurchaseAnalysisIn(
        **_LOGIN,
        date_from="2026-09-01",
        date_to="2026-09-01",
        group_by=["month", "vendor", "material"],
        inv=["9909001"],
        vendor=["S900001"],
        inv_class="01.02",
        source="receipt",
        limit=1,
    )
    assert full.group_by == ["month", "vendor", "material"]
    assert full.source == "receipt"


@pytest.mark.parametrize(
    "extra",
    (
        {},
        {"date_from": "2026-13-01"},
        {"date_from": "2026-09-01", "date_to": "2026-08-31"},
        {"date_from": "2026-09-01", "group_by": []},
        {"date_from": "2026-09-01", "group_by": ["material", "material"]},
        {"date_from": "2026-09-01", "group_by": ["material", "vendor", "month", "material"]},
        {"date_from": "2026-09-01", "group_by": ["week"]},
        {"date_from": "2026-09-01", "source": "po"},
        {"date_from": "2026-09-01", "limit": 0},
        {"date_from": "2026-09-01", "limit": 1001},
        {"date_from": "2026-09-01", "limit": True},
        {"date_from": "2026-09-01", "inv": []},
        {"date_from": "2026-09-01", "inv": ["I"] * 21},
        {"date_from": "2026-09-01", "vendor": ["a\tb"]},
        {"date_from": "2026-09-01", "inv_class": "01 02"},
        {"date_from": "2026-09-01", "unknown": 1},
    ),
)
def test_purchase_analysis_in_rejects(extra: dict) -> None:
    with pytest.raises(ValidationError):
        PurchaseAnalysisIn(**_LOGIN, **extra)


def test_arrival_otif_defaults_and_values() -> None:
    minimal = ArrivalOtifIn(**_LOGIN, **_ARRIVAL)
    assert minimal.date_to is None
    assert minimal.group_by is None
    assert minimal.overdue_limit is None
    full = ArrivalOtifIn(
        **_LOGIN,
        date_from="2026-09-01",
        date_to="2026-09-30",
        vendor=["S900001"],
        inv=["9909001"],
        group_by="material",
        overdue_limit=200,
    )
    assert full.group_by == "material"
    assert ArrivalOtifIn(**_LOGIN, group_by="none", **_ARRIVAL).group_by == "none"


@pytest.mark.parametrize(
    "extra",
    (
        {},
        {"date_from": "2026-09-31"},
        {"date_from": "2026-09-01", "date_to": "2026-08-31"},
        {"date_from": "2026-09-01", "group_by": "month"},
        {"date_from": "2026-09-01", "group_by": ["vendor"]},
        {"date_from": "2026-09-01", "overdue_limit": 0},
        {"date_from": "2026-09-01", "overdue_limit": 201},
        {"date_from": "2026-09-01", "overdue_limit": 1.5},
        {"date_from": "2026-09-01", "vendor": []},
        {"date_from": "2026-09-01", "vendor": ["S"] * 21},
        {"date_from": "2026-09-01", "inv": "9909001"},
        {"date_from": "2026-09-01", "limit": 10},
        {"date_from": "2026-09-01", "as_of": "2026-09-30"},
    ),
)
def test_arrival_otif_in_rejects(extra: dict) -> None:
    with pytest.raises(ValidationError):
        ArrivalOtifIn(**_LOGIN, **extra)


def test_out_models_keep_bridge_fields() -> None:
    purchase = PurchaseAnalysisOut(
        ok=True,
        source="invoice",
        items=[{"inv_code": "9909001", "qty": 1.5, "amount": 10.0, "amount_tax": None, "docs": 1, "x": 1}],
        totals={"amount": 10.0, "amount_tax": None, "docs": 1},
        extra_field=True,
    )
    assert purchase.model_dump()["items"][0]["x"] == 1
    assert purchase.model_dump()["extra_field"] is True
    arrival = ArrivalOtifOut(ok=True, groups=[], overdue=[], overdue_total=0, as_of="2026-09-30", other=1)
    assert arrival.model_dump()["other"] == 1


def test_summaries_and_docs_are_filled() -> None:
    assert PURCHASE_ANALYSIS_SUMMARY == "采购综合统计"
    assert ARRIVAL_OTIF_SUMMARY == "采购到货及时率"
    assert "group_by" in PURCHASE_ANALYSIS_DOC
    assert "overdue_total" in ARRIVAL_OTIF_DOC
    assert "只查数据库" in PURCHASE_ANALYSIS_DOC
    assert "只查数据库" in ARRIVAL_OTIF_DOC


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_pu_reports_forward_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
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
def test_pu_reports_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _PuBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        if path == "/v1/reports/purchase_analysis":
            row = {
                "inv_code": "9909001",
                "inv_name": "原料甲",
                "unit": "千克",
                "qty": 10.0,
                "amount": 100.0,
                "amount_tax": None,
                "docs": 2,
            }
            return {
                "ok": True,
                "source": "invoice",
                "date_from": "2026-09-01",
                "date_to": "2026-09-30",
                "group_by": ["material"],
                "items": [row],
                "count": 1,
                "truncated": False,
                "totals": {"amount": 100.0, "amount_tax": None, "docs": 2},
            }
        row = {"po_code": "PO0001", "qty": 5.0, "arrived_qty": 0.0, "planned_date": "2026-09-01", "days_overdue": 3}
        return {
            "ok": True,
            "as_of": "2026-09-04",
            "date_from": "2026-09-01",
            "date_to": None,
            "group_by": "vendor",
            "arrival_basis": "arrival_or_receipt",
            "groups": [
                {
                    "vendor_code": "S900001",
                    "lines": 3,
                    "arrived_lines": 2,
                    "on_time": 1,
                    "late": 1,
                    "open_overdue": 1,
                    "not_due": 0,
                }
            ],
            "truncated": False,
            "totals": {"lines": 3, "arrived_lines": 2, "on_time": 1, "late": 1, "open_overdue": 1, "not_due": 0},
            "overdue": [row],
            "overdue_total": 1,
        }


def test_pu_report_responses_keep_bridge_fields() -> None:
    client = _client(_PuBridge())
    purchase = client.post(_R + "purchase_analysis", json=_MINIMAL["purchase_analysis"]).json()
    assert purchase["items"][0]["inv_code"] == "9909001"
    assert purchase["items"][0]["docs"] == 2
    assert purchase["totals"]["docs"] == 2
    assert purchase["count"] == 1
    assert purchase["truncated"] is False
    arrival = client.post(_R + "arrival_otif", json=_MINIMAL["arrival_otif"]).json()
    assert arrival["groups"][0]["open_overdue"] == 1
    assert arrival["overdue"][0]["days_overdue"] == 3
    assert arrival["overdue_total"] == 1
    assert arrival["totals"]["lines"] == 3
    assert arrival["arrival_basis"] == "arrival_or_receipt"


@pytest.mark.parametrize("name", _NAMES)
def test_pu_reports_are_read_only_and_gated(name: str) -> None:
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


def test_pu_reports_openapi_paths_summaries_and_tags() -> None:
    spec = _spec()
    for name, (summary, operation_id) in _OPENAPI.items():
        operation = spec["paths"][_R + name]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert operation["tags"] == ["报表"]
        assert "权限：只读" in operation["description"]
    assert set(_enum(spec, "PurchaseAnalysisIn", "source")) == {"invoice", "receipt"}
    assert set(_enum(spec, "ArrivalOtifIn", "group_by")) == {"vendor", "material", "none"}
