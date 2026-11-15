"""/v1/co/reports/receipts_payments、notes_due、cash_flow：请求模型（缺省、范围、枚举、必填、未知字段）、转发的字段、
提前拒绝、权限、审计和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.support import audit_line as _audit_line
from tests.support import base_claims
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_update_close_gen import _enum, _spec
from u8co_api.co_access import ACCESS, READ
from u8co_api.co_models_reports_fin import (
    CASH_FLOW_DOC,
    CASH_FLOW_SUMMARY,
    NOTES_DUE_DOC,
    NOTES_DUE_SUMMARY,
    RECEIPTS_PAYMENTS_DOC,
    RECEIPTS_PAYMENTS_SUMMARY,
    CashFlowIn,
    CashFlowOut,
    NotesDueIn,
    NotesDueOut,
    ReceiptsPaymentsIn,
    ReceiptsPaymentsOut,
)

_R = "/v1/co/reports/"
_REC = {"side": "ar", "date_from": "2026-09-01"}
_NOTE = {"side": "ap"}
_NAMES = ("receipts_payments", "notes_due", "cash_flow")
_LOGIN = {"acc": "801", "operator": "op001", "password": "x"}


def _model(cls, **extra):
    return cls(**_LOGIN, **extra)


def _fwd(name: str, body: dict, *keys: str) -> tuple:
    return (_R + name, "/v1/reports/" + name, body, _sealed(*keys))


# ---------- 模型 ----------


def test_models_minimal_requests_have_no_defaults_filled() -> None:
    rec = _model(ReceiptsPaymentsIn, **_REC)
    assert (rec.date_to, rec.group_by, rec.partner, rec.person, rec.dept, rec.include_unverified, rec.limit) == (
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    assert rec.audit_ref() == "ar"
    note = _model(NotesDueIn, **_NOTE)
    assert (note.expire_from, note.expire_to, note.open_only, note.partner, note.limit) == (None,) * 5
    flow = _model(CashFlowIn)
    assert (flow.fiscal_year, flow.period_from, flow.period_to, flow.include_unposted) == (None,) * 4


def test_models_accept_full_requests() -> None:
    rec = _model(
        ReceiptsPaymentsIn,
        side="ap",
        date_from="2026-09-01",
        date_to="2026-09-30",
        group_by=["settle", "month"],
        partner=["S900001", "S900002"],
        person=["P01"],
        dept=["D01"],
        include_unverified=True,
        limit=1000,
    )
    assert rec.group_by == ["settle", "month"] and rec.partner == ["S900001", "S900002"] and rec.limit == 1000
    assert _model(ReceiptsPaymentsIn, **_REC, group_by=[]).group_by == []
    note = _model(
        NotesDueIn,
        side="ar",
        expire_from="2026-10-01",
        expire_to="2026-10-31",
        open_only=False,
        partner=["C900001"],
        limit=5,
    )
    assert note.open_only is False and note.expire_to == "2026-10-31"
    flow = _model(CashFlowIn, fiscal_year=2026, period_from=3, period_to=9, include_unposted=True)
    assert (flow.fiscal_year, flow.period_from, flow.period_to, flow.include_unposted) == (2026, 3, 9, True)


@pytest.mark.parametrize(
    "extra",
    (
        {"date_from": "2026-09-01"},
        {"side": "ar"},
        {"side": "both", "date_from": "2026-09-01"},
        {"side": "ar", "date_from": "2026-9-1"},
        {"side": "ar", "date_from": "2026-02-30"},
        {"side": "ar", "date_from": "2026-09-01", "date_to": "2026-08-31"},
        {"side": "ar", "date_from": "2026-09-01", "date_to": "2026/09/30"},
        {"side": "ar", "date_from": "2026-09-01", "group_by": ["day", "month", "partner"]},
        {"side": "ar", "date_from": "2026-09-01", "group_by": ["day", "day"]},
        {"side": "ar", "date_from": "2026-09-01", "group_by": ["week"]},
        {"side": "ar", "date_from": "2026-09-01", "group_by": "day"},
        {"side": "ar", "date_from": "2026-09-01", "partner": []},
        {"side": "ar", "date_from": "2026-09-01", "partner": ["C"] * 21},
        {"side": "ar", "date_from": "2026-09-01", "partner": [" "]},
        {"side": "ar", "date_from": "2026-09-01", "partner": "C900001"},
        {"side": "ar", "date_from": "2026-09-01", "person": []},
        {"side": "ar", "date_from": "2026-09-01", "dept": ["D"] * 21},
        {"side": "ar", "date_from": "2026-09-01", "include_unverified": "true"},
        {"side": "ar", "date_from": "2026-09-01", "limit": 0},
        {"side": "ar", "date_from": "2026-09-01", "limit": 1001},
        {"side": "ar", "date_from": "2026-09-01", "limit": "5"},
        {"side": "ar", "date_from": "2026-09-01", "after": "x"},
    ),
)
def test_models_reject_bad_receipts_payments(extra: dict) -> None:
    with pytest.raises(ValidationError):
        _model(ReceiptsPaymentsIn, **extra)


@pytest.mark.parametrize(
    "extra",
    (
        {},
        {"side": "x"},
        {"side": "ar", "expire_from": "2026-10-1"},
        {"side": "ar", "expire_to": "2026-02-30"},
        {"side": "ar", "expire_from": "2026-10-31", "expire_to": "2026-10-01"},
        {"side": "ar", "open_only": "yes"},
        {"side": "ar", "partner": []},
        {"side": "ar", "partner": ["C"] * 21},
        {"side": "ar", "limit": 1001},
        {"side": "ar", "date_from": "2026-09-01"},
        {"side": "ar", "after": "x"},
    ),
)
def test_models_reject_bad_notes_due(extra: dict) -> None:
    with pytest.raises(ValidationError):
        _model(NotesDueIn, **extra)


@pytest.mark.parametrize(
    "extra",
    (
        {"fiscal_year": 1999},
        {"fiscal_year": 2100},
        {"fiscal_year": "2026"},
        {"period_from": 0},
        {"period_to": 13},
        {"period_from": 6, "period_to": 5},
        {"include_unposted": "no"},
        {"side": "ar"},
        {"limit": 10},
    ),
)
def test_models_reject_bad_cash_flow(extra: dict) -> None:
    with pytest.raises(ValidationError):
        _model(CashFlowIn, **extra)


def test_models_out_keep_extra_bridge_fields() -> None:
    out = ReceiptsPaymentsOut(ok=True, items=[{"date": "2026-09-02", "x": 1}], totals={"docs": 3}, extra_field=7)
    assert out.model_dump()["extra_field"] == 7 and out.totals == {"docs": 3}
    note = NotesDueOut(ok=True, items=[], totals={"count": 0}, count=0, truncated=False)
    assert note.truncated is False
    flow = CashFlowOut(ok=True, items=[{"code": "01", "periods": {"1": 1.5}}], unassigned={"vouchers": 0, "amount": 0})
    assert flow.unassigned == {"vouchers": 0, "amount": 0}


def test_models_texts_are_filled() -> None:
    texts = (
        RECEIPTS_PAYMENTS_SUMMARY,
        RECEIPTS_PAYMENTS_DOC,
        NOTES_DUE_SUMMARY,
        NOTES_DUE_DOC,
        CASH_FLOW_SUMMARY,
        CASH_FLOW_DOC,
    )
    for text in texts:
        assert text.strip()
    for doc in (RECEIPTS_PAYMENTS_DOC, NOTES_DUE_DOC, CASH_FLOW_DOC):
        assert "**用法**" in doc and "**规则**" in doc


# ---------- 路由 ----------

_FORWARD = (
    _fwd("receipts_payments", _auth(**_REC), "side", "date_from"),
    _fwd(
        "receipts_payments",
        _auth(
            side="ap",
            date_from="2026-09-01",
            date_to="2026-09-28",
            group_by=["partner", "month"],
            partner=["S900001"],
            person=["P01"],
            dept=["D01"],
            include_unverified=True,
            limit=1000,
        ),
        "side",
        "date_from",
        "date_to",
        "group_by",
        "partner",
        "person",
        "dept",
        "include_unverified",
        "limit",
    ),
    _fwd("receipts_payments", _auth(side="ar", date_from="2026-09-01", group_by=[]), "side", "date_from", "group_by"),
    _fwd("notes_due", _auth(**_NOTE), "side"),
    _fwd(
        "notes_due",
        _auth(
            side="ar", expire_from="2026-10-01", expire_to="2026-10-31", open_only=False, partner=["C900001"], limit=10
        ),
        "side",
        "expire_from",
        "expire_to",
        "open_only",
        "partner",
        "limit",
    ),
    _fwd("cash_flow", _auth()),
    _fwd(
        "cash_flow",
        _auth(fiscal_year=2026, period_from=2, period_to=9, include_unposted=True),
        "fiscal_year",
        "period_from",
        "period_to",
        "include_unposted",
    ),
)

_REJECTS = (
    (_R + "receipts_payments", _auth()),
    (_R + "receipts_payments", _auth(side="ar")),
    (_R + "receipts_payments", _auth(date_from="2026-09-01")),
    (_R + "receipts_payments", _auth(side="both", date_from="2026-09-01")),
    (_R + "receipts_payments", _auth(side="ar", date_from="2026-09-01", date_to="2026-08-01")),
    (_R + "receipts_payments", _auth(side="ar", date_from="2026-02-30")),
    (_R + "receipts_payments", _auth(side="ar", date_from="2026-09-01", group_by=["day", "day"])),
    (_R + "receipts_payments", _auth(side="ar", date_from="2026-09-01", group_by=["day", "month", "settle"])),
    (_R + "receipts_payments", _auth(side="ar", date_from="2026-09-01", group_by=["week"])),
    (_R + "receipts_payments", _auth(side="ar", date_from="2026-09-01", partner=[])),
    (_R + "receipts_payments", _auth(side="ar", date_from="2026-09-01", person=["P"] * 21)),
    (_R + "receipts_payments", _auth(side="ar", date_from="2026-09-01", limit=1001)),
    (_R + "receipts_payments", _auth(side="ar", date_from="2026-09-01", after="x")),
    (_R + "notes_due", _auth()),
    (_R + "notes_due", _auth(side="x")),
    (_R + "notes_due", _auth(side="ar", expire_from="2026-10-31", expire_to="2026-10-01")),
    (_R + "notes_due", _auth(side="ar", open_only="yes")),
    (_R + "notes_due", _auth(side="ar", limit=0)),
    (_R + "notes_due", _auth(side="ar", date_from="2026-09-01")),
    (_R + "cash_flow", _auth(period_from=6, period_to=5)),
    (_R + "cash_flow", _auth(period_to=13)),
    (_R + "cash_flow", _auth(fiscal_year=1999)),
    (_R + "cash_flow", _auth(include_unposted="no")),
    (_R + "cash_flow", _auth(side="ar")),
)

_OPENAPI = {
    "receipts_payments": ("收付款汇总", "coReportReceiptsPayments"),
    "notes_due": ("到期票据", "coReportNotesDue"),
    "cash_flow": ("现金流量统计", "coReportCashFlow"),
}

_MINIMAL = {
    "receipts_payments": _auth(**_REC),
    "notes_due": _auth(**_NOTE),
    "cash_flow": _auth(),
}


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_fin_reports_forward_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
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
def test_fin_reports_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _FinBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        if path == "/v1/reports/receipts_payments":
            row = {"date": "2026-09-02", "docs": 3, "amount": 10.5, "refund_amount": 0.5, "net_amount": 10.0, "x": 1}
            totals = {"docs": 3, "amount": 10.5, "refund_amount": 0.5, "net_amount": 10.0}
            return {"ok": True, "side": "ar", "items": [row], "count": 1, "truncated": False, "totals": totals}
        if path == "/v1/reports/notes_due":
            row = {"code": "N1", "partner_code": None, "status": "open", "days_to_due": 10, "y": 2}
            totals = {"count": 1, "amount": 5.0, "remainder": 2.0}
            return {"ok": True, "side": "ar", "items": [row], "count": 1, "truncated": False, "totals": totals}
        row = {"code": "01", "direction": "in", "amount": 3.5, "periods": {"1": 1.5, "2": 2.0}}
        return {"ok": True, "fiscal_year": 2026, "items": [row], "unassigned": {"vouchers": 2, "amount": 9.0}}


def test_fin_report_responses_keep_bridge_fields() -> None:
    client = _client(_FinBridge())
    rec = client.post(_R + "receipts_payments", json=_auth(**_REC)).json()
    assert rec["items"][0]["x"] == 1 and rec["items"][0]["net_amount"] == 10.0
    assert rec["count"] == 1 and rec["truncated"] is False
    assert rec["totals"] == {"docs": 3, "amount": 10.5, "refund_amount": 0.5, "net_amount": 10.0}
    note = client.post(_R + "notes_due", json=_auth(**_NOTE)).json()
    assert note["items"][0]["y"] == 2 and note["items"][0]["days_to_due"] == 10
    assert note["totals"]["remainder"] == 2.0
    flow = client.post(_R + "cash_flow", json=_auth()).json()
    assert flow["items"][0]["periods"] == {"1": 1.5, "2": 2.0}
    assert flow["unassigned"] == {"vouchers": 2, "amount": 9.0}


@pytest.mark.parametrize("name", _NAMES)
def test_fin_reports_are_read_only_and_gated(name: str) -> None:
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
        (
            "receipts_payments",
            _auth(side="ap", date_from="2026-09-01"),
            "co:reports/receipts_payments:receipts_payments#ap",
        ),
        ("notes_due", _auth(**_NOTE), "co:reports/notes_due:notes_due#ap"),
    ),
)
def test_fin_report_audit_names_the_report(name: str, body: dict, view: str, capsys) -> None:
    ok = _client().post(_R + name, json=body)
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert _audit_line(out, _R + name)["action"] == view


def test_fin_reports_openapi_paths_summaries_and_tags() -> None:
    spec = _spec()
    for name, (summary, operation_id) in _OPENAPI.items():
        operation = spec["paths"][_R + name]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert operation["tags"] == ["报表"]
        assert "权限：只读" in operation["description"]
    assert set(_enum(spec, "ReceiptsPaymentsIn", "side")) == {"ar", "ap"}
    assert set(_enum(spec, "NotesDueIn", "side")) == {"ar", "ap"}
