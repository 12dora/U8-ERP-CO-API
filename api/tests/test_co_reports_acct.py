"""账务核对报表 /v1/co/reports/close_check、ia_anomalies、cost_unit：模型校验（缺省、上限、必填、未知字段）、
转发的字段、提前拒绝、响应字段原样返回、权限和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.support import base_claims
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, READ
from u8co_api.co_models_reports_acct import (
    CLOSE_CHECK_DOC,
    CLOSE_CHECK_SUMMARY,
    COST_UNIT_DOC,
    COST_UNIT_SUMMARY,
    IA_ANOMALIES_DOC,
    IA_ANOMALIES_SUMMARY,
    CloseCheckIn,
    CloseCheckOut,
    CostUnitIn,
    CostUnitOut,
    IaAnomaliesIn,
    IaAnomaliesOut,
)

_R = "/v1/co/reports/"
_NAMES = ("close_check", "ia_anomalies", "cost_unit")
_LOGIN = {"acc": "801", "operator": "op001", "password": "pw"}


def _fwd(name: str, body: dict, *keys: str) -> tuple:
    return (_R + name, "/v1/reports/" + name, body, _sealed(*keys))


_FORWARD = (
    _fwd("close_check", _auth()),
    _fwd("close_check", _auth(fiscal_year=2025, period=12), "fiscal_year", "period"),
    _fwd("ia_anomalies", _auth()),
    _fwd("ia_anomalies", _auth(fiscal_year=2026, period=9, limit=200), "fiscal_year", "period", "limit"),
    _fwd("cost_unit", _auth()),
    _fwd(
        "cost_unit",
        _auth(fiscal_year=2026, period_from=8, period_to=9, inv=["9909001", "9909002"], inv_class="01.02"),
        "fiscal_year", "period_from", "period_to", "inv", "inv_class",
    ),
)

_REJECTS = (
    (_R + "close_check", _auth(period=0)),
    (_R + "close_check", _auth(period=13)),
    (_R + "close_check", _auth(period="9")),
    (_R + "close_check", _auth(period=True)),
    (_R + "close_check", _auth(fiscal_year=1999)),
    (_R + "close_check", _auth(fiscal_year=2100)),
    (_R + "close_check", _auth(limit=10)),
    (_R + "close_check", _auth(after="x")),
    (_R + "ia_anomalies", _auth(period=0)),
    (_R + "ia_anomalies", _auth(period=13)),
    (_R + "ia_anomalies", _auth(limit=0)),
    (_R + "ia_anomalies", _auth(limit=201)),
    (_R + "ia_anomalies", _auth(limit="5")),
    (_R + "ia_anomalies", _auth(limit=1.5)),
    (_R + "ia_anomalies", _auth(inv=["9909001"])),
    (_R + "cost_unit", _auth(period_from=0)),
    (_R + "cost_unit", _auth(period_to=13)),
    (_R + "cost_unit", _auth(period_from=6, period_to=5)),
    (_R + "cost_unit", _auth(period=9)),
    (_R + "cost_unit", _auth(inv=[])),
    (_R + "cost_unit", _auth(inv=["I"] * 21)),
    (_R + "cost_unit", _auth(inv="9909001")),
    (_R + "cost_unit", _auth(inv=[" "])),
    (_R + "cost_unit", _auth(inv_class="01%")),
    (_R + "cost_unit", _auth(inv_class="")),
    (_R + "cost_unit", _auth(limit=10)),
)

_OPENAPI = {
    "close_check": ("月末结账检查", "coReportCloseCheck"),
    "ia_anomalies": ("存货核算检查", "coReportIaAnomalies"),
    "cost_unit": ("完工产品单位成本", "coReportCostUnit"),
}

_MINIMAL = {name: _auth() for name in _NAMES}


def test_close_check_in_defaults_and_values() -> None:
    minimal = CloseCheckIn(**_LOGIN)
    assert minimal.fiscal_year is None
    assert minimal.period is None
    full = CloseCheckIn(**_LOGIN, fiscal_year=2026, period=12)
    assert (full.fiscal_year, full.period) == (2026, 12)
    assert CloseCheckIn(**_LOGIN, period=1).period == 1


@pytest.mark.parametrize(
    "extra",
    (
        {"period": 0},
        {"period": 13},
        {"period": "9"},
        {"period": True},
        {"period": 9.5},
        {"fiscal_year": 1999},
        {"fiscal_year": 2100},
        {"unknown": 1},
        {"limit": 10},
    ),
)
def test_close_check_in_rejects(extra: dict) -> None:
    with pytest.raises(ValidationError):
        CloseCheckIn(**_LOGIN, **extra)


def test_ia_anomalies_in_defaults_and_values() -> None:
    minimal = IaAnomaliesIn(**_LOGIN)
    assert (minimal.fiscal_year, minimal.period, minimal.limit) == (None, None, None)
    full = IaAnomaliesIn(**_LOGIN, fiscal_year=2026, period=9, limit=200)
    assert (full.fiscal_year, full.period, full.limit) == (2026, 9, 200)
    assert IaAnomaliesIn(**_LOGIN, limit=1).limit == 1


@pytest.mark.parametrize(
    "extra",
    (
        {"period": 0},
        {"period": 13},
        {"limit": 0},
        {"limit": 201},
        {"limit": "5"},
        {"limit": True},
        {"limit": 1.5},
        {"inv": ["9909001"]},
    ),
)
def test_ia_anomalies_in_rejects(extra: dict) -> None:
    with pytest.raises(ValidationError):
        IaAnomaliesIn(**_LOGIN, **extra)


def test_cost_unit_in_defaults_and_values() -> None:
    minimal = CostUnitIn(**_LOGIN)
    assert minimal.period_from is None
    assert minimal.period_to is None
    assert minimal.inv is None
    assert minimal.inv_class is None
    full = CostUnitIn(
        **_LOGIN, fiscal_year=2026, period_from=8, period_to=8, inv=["9909001", "9909002"], inv_class="01.02-A"
    )
    assert full.period_from == full.period_to == 8
    assert full.inv == ["9909001", "9909002"]
    assert CostUnitIn(**_LOGIN, period_to=9).period_to == 9
    assert CostUnitIn(**_LOGIN, period_from=3).period_from == 3


@pytest.mark.parametrize(
    "extra",
    (
        {"period_from": 0},
        {"period_from": 13},
        {"period_to": 0},
        {"period_to": 13},
        {"period_from": 9, "period_to": 8},
        {"period_from": "1"},
        {"inv": []},
        {"inv": ["I"] * 21},
        {"inv": "9909001"},
        {"inv": ["a\tb"]},
        {"inv": [" "]},
        {"inv_class": ""},
        {"inv_class": "01 02"},
        {"inv_class": "0" * 41},
        {"period": 9},
    ),
)
def test_cost_unit_in_rejects(extra: dict) -> None:
    with pytest.raises(ValidationError):
        CostUnitIn(**_LOGIN, **extra)


def test_out_models_keep_bridge_fields() -> None:
    close = CloseCheckOut(
        ok=True,
        fiscal_year=2026,
        period=9,
        closed={"SA": True, "GL": False},
        checks=[{"module": "SA", "item": "unaudited_dispatch", "count": 0, "samples": [], "x": 1}],
        extra_field=True,
    )
    assert close.model_dump()["checks"][0]["x"] == 1
    assert close.model_dump()["extra_field"] is True
    ia = IaAnomaliesOut(ok=True, unbilled={"count": 0, "est_amount": 0, "items": []}, unbooked={"count": 0, "items": []}, other=1)
    assert ia.model_dump()["unbilled"]["est_amount"] == 0
    assert ia.model_dump()["other"] == 1
    cost = CostUnitOut(ok=True, period_from=None, period_to=None, items=[], element_names={}, closed_periods=[], other=1)
    assert cost.model_dump()["period_from"] is None
    assert cost.model_dump()["other"] == 1


def test_summaries_and_docs_are_filled() -> None:
    assert CLOSE_CHECK_SUMMARY == "月末结账检查"
    assert IA_ANOMALIES_SUMMARY == "存货核算检查"
    assert COST_UNIT_SUMMARY == "完工产品单位成本"
    for doc, word in ((CLOSE_CHECK_DOC, "unposted_vouchers"), (IA_ANOMALIES_DOC, "billing_as_of"), (COST_UNIT_DOC, "unit_total")):
        assert word in doc
        assert "只查数据库" in doc


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_acct_reports_forward_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
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
def test_acct_reports_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _AcctBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        if path == "/v1/reports/close_check":
            return {
                "ok": True,
                "fiscal_year": 2026,
                "period": 9,
                "closed": {"SA": True, "PU": True, "ST": False, "IA": False, "GL": False, "AR": True, "AP": True, "CA": False, "FA": False},
                "checks": [
                    {"module": "ST", "item": "unverified_sale_out", "count": 2, "samples": ["XS0001", "XS0002"]},
                    {"module": "GL", "item": "unposted_vouchers", "count": 0, "samples": []},
                ],
            }
        if path == "/v1/reports/ia_anomalies":
            line = {"code": "CG0001", "date": "2026-09-03", "inv_code": "9909001", "qty": 10.0, "billed_qty": 4.5}
            return {
                "ok": True,
                "fiscal_year": 2026,
                "period": 9,
                "period_end": "2026-09-30",
                "billing_as_of": "2026-10-15",
                "unbilled": {"count": 1, "est_amount": 12.35, "items": [dict(line, residual_qty=5.5, est_amount=12.35, days=27)]},
                "unbooked": {"count": 1, "items": [{"type": "material_out", "code": "LL0001", "date": "2026-09-04", "qty": 3.5}]},
            }
        return {
            "ok": True,
            "fiscal_year": 2026,
            "period_from": 9,
            "period_to": 9,
            "items": [
                {
                    "inv_code": "9909001",
                    "period": "2026-09",
                    "qty": 1500.5,
                    "elements": {"0": 3.1, "1": 0.125},
                    "unit_total": 3.225,
                }
            ],
            "element_names": {"0": "材料费用", "1": "人工费用"},
            "closed_periods": ["2026-08", "2026-09"],
        }


def test_acct_report_responses_keep_bridge_fields() -> None:
    client = _client(_AcctBridge())
    close = client.post(_R + "close_check", json=_MINIMAL["close_check"]).json()
    assert close["closed"]["ST"] is False
    assert close["checks"][0]["samples"] == ["XS0001", "XS0002"]
    assert close["checks"][1]["count"] == 0
    ia = client.post(_R + "ia_anomalies", json=_MINIMAL["ia_anomalies"]).json()
    assert ia["period_end"] == "2026-09-30"
    assert ia["billing_as_of"] == "2026-10-15"
    assert ia["unbilled"]["items"][0]["residual_qty"] == 5.5
    assert ia["unbilled"]["est_amount"] == 12.35
    assert ia["unbooked"]["items"][0]["type"] == "material_out"
    cost = client.post(_R + "cost_unit", json=_MINIMAL["cost_unit"]).json()
    assert cost["items"][0]["elements"]["1"] == 0.125
    assert cost["items"][0]["unit_total"] == 3.225
    assert cost["element_names"]["0"] == "材料费用"
    assert cost["closed_periods"] == ["2026-08", "2026-09"]


@pytest.mark.parametrize("name", _NAMES)
def test_acct_reports_are_read_only_and_gated(name: str) -> None:
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


def test_acct_reports_openapi_paths_summaries_and_tags() -> None:
    spec = _spec()
    for name, (summary, operation_id) in _OPENAPI.items():
        operation = spec["paths"][_R + name]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert operation["tags"] == ["报表"]
        assert "权限：只读" in operation["description"]
    schemas = spec["components"]["schemas"]
    assert {"fiscal_year", "period", "limit"} <= set(schemas["IaAnomaliesIn"]["properties"])
    assert {"period_from", "period_to", "inv", "inv_class"} <= set(schemas["CostUnitIn"]["properties"])
    assert {"fiscal_year", "period"} <= set(schemas["CloseCheckIn"]["properties"])
