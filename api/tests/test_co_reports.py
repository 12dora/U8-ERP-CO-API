"""/v1/co/reports/*：转发的字段、提前拒绝、权限、审计和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line as _audit_line
from tests.support import base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec
from u8co_api.errors import ApiError

_R = "/v1/co/reports/"
_GL = {"period_from": 1, "period_to": 8}
_NAMES = ("close_status", "gl_balance", "gl_aux_balance", "arap_balance", "arap_aging", "bom")


def _fwd(name: str, body: dict, *keys: str) -> tuple:
    return (_R + name, "/v1/reports/" + name, body, _sealed(*keys))


_FORWARD = (
    _fwd("close_status", _auth()),
    _fwd("close_status", _auth(fiscal_year=2026), "fiscal_year"),
    _fwd("gl_balance", _auth(**_GL), "period_from", "period_to"),
    _fwd(
        "gl_balance",
        _auth(
            fiscal_year=2026,
            grade_from=1,
            grade_to=3,
            code_prefix="1002.01-A",
            leaf_only=True,
            include_unposted=False,
            nonzero=True,
            after="1001",
            limit=1000,
            **_GL,
        ),
        "fiscal_year",
        "period_from",
        "period_to",
        "grade_from",
        "grade_to",
        "code_prefix",
        "leaf_only",
        "include_unposted",
        "nonzero",
        "after",
        "limit",
    ),
    _fwd("gl_aux_balance", _auth(dim="vendor", **_GL), "dim", "period_from", "period_to"),
    _fwd(
        "gl_aux_balance",
        _auth(
            dim="project",
            project_class="00",
            dim_code="P01",
            code_prefix="5001",
            nonzero=False,
            after="x|y",
            limit=1,
            **_GL,
        ),
        "dim",
        "period_from",
        "period_to",
        "project_class",
        "dim_code",
        "code_prefix",
        "nonzero",
        "after",
        "limit",
    ),
    _fwd("arap_balance", _auth(side="ar"), "side"),
    _fwd(
        "arap_balance",
        _auth(
            side="ap",
            as_of="2026-09-26",
            accounts=["220201", "220202"],
            exclude_accounts=[],
            partner="V001",
            nonzero=False,
            after="V000",
            limit=200,
        ),
        "side",
        "as_of",
        "accounts",
        "exclude_accounts",
        "partner",
        "nonzero",
        "after",
        "limit",
    ),
    _fwd("arap_aging", _auth(side="ar"), "side"),
    _fwd(
        "arap_aging",
        _auth(side="ar", as_of="2026-09-26", basis="due", buckets=[30, 60, 90], exclude_accounts=["112204"]),
        "side",
        "as_of",
        "basis",
        "buckets",
        "exclude_accounts",
    ),
    _fwd(
        "arap_aging",
        _auth(side="ar", basis="due", group_by="person", person=["P001", "P002"], overdue_only=True),
        "side",
        "basis",
        "group_by",
        "person",
        "overdue_only",
    ),
    _fwd("arap_aging", _auth(side="ap", group_by="partner_person", overdue_only=False), "side", "group_by", "overdue_only"),
    _fwd("arap_aging", _auth(side="ar", basis="due", default_credit_days=30), "side", "basis", "default_credit_days"),
    _fwd("arap_aging", _auth(side="ap", basis="due", default_credit_days=0), "side", "basis", "default_credit_days"),
    _fwd("bom", _auth(parent="INV0012"), "parent"),
    _fwd(
        "bom",
        _auth(parent="INV0012", as_of="2026-09-26", levels=10, limit=5000),
        "parent",
        "as_of",
        "levels",
        "limit",
    ),
)

_REJECTS = (
    (_R + "close_status", _auth(fiscal_year=1999)),
    (_R + "close_status", _auth(fiscal_year=2100)),
    (_R + "close_status", _auth(period_from=1)),
    (_R + "gl_balance", _auth(period_from=1)),
    (_R + "gl_balance", _auth(period_from=0, period_to=8)),
    (_R + "gl_balance", _auth(period_from=1, period_to=13)),
    (_R + "gl_balance", _auth(period_from=9, period_to=8)),
    (_R + "gl_balance", _auth(grade_from=0, **_GL)),
    (_R + "gl_balance", _auth(grade_to=10, **_GL)),
    (_R + "gl_balance", _auth(grade_from=4, grade_to=3, **_GL)),
    (_R + "gl_balance", _auth(grade_to=0, **_GL)),
    (_R + "gl_balance", _auth(code_prefix="", **_GL)),
    (_R + "gl_balance", _auth(code_prefix="10%", **_GL)),
    (_R + "gl_balance", _auth(code_prefix="1" * 41, **_GL)),
    (_R + "gl_balance", _auth(code_prefix="10 01", **_GL)),
    (_R + "gl_balance", _auth(limit=0, **_GL)),
    (_R + "gl_balance", _auth(limit=1001, **_GL)),
    (_R + "gl_balance", _auth(after="", **_GL)),
    (_R + "gl_balance", _auth(after="x" * 513, **_GL)),
    (_R + "gl_balance", _auth(after="x y", **_GL)),
    (_R + "gl_balance", _auth(period_from="1", period_to=8)),
    (_R + "gl_balance", _auth(leaf_only="true", **_GL)),
    (_R + "gl_aux_balance", _auth(dim="customer", dim_code="  ", **_GL)),
    (_R + "gl_balance", _auth(period_from=1.5, period_to=8)),
    (_R + "gl_balance", _auth(dim="customer", **_GL)),
    (_R + "gl_aux_balance", _auth(**_GL)),
    (_R + "gl_aux_balance", _auth(dim="account", **_GL)),
    (_R + "gl_aux_balance", _auth(dim="customer", period_from=3, period_to=2)),
    (_R + "gl_aux_balance", _auth(dim="customer", project_class="00", **_GL)),
    (_R + "gl_aux_balance", _auth(dim="project", project_class="0-0", **_GL)),
    (_R + "gl_aux_balance", _auth(dim="customer", dim_code="", **_GL)),
    (_R + "gl_aux_balance", _auth(dim="customer", dim_code="a\nb", **_GL)),
    (_R + "gl_aux_balance", _auth(dim="customer", dim_code="x" * 61, **_GL)),
    (_R + "gl_aux_balance", _auth(dim="customer", include_unposted=True, **_GL)),
    (_R + "arap_balance", _auth()),
    (_R + "arap_balance", _auth(side="both")),
    (_R + "arap_balance", _auth(side="ar", as_of="2026-9-26")),
    (_R + "arap_balance", _auth(side="ar", as_of="2026-02-30")),
    (_R + "arap_balance", _auth(side="ar", accounts=[])),
    (_R + "arap_balance", _auth(side="ar", accounts=["1122"] * 21)),
    (_R + "arap_balance", _auth(side="ar", accounts=["11%"])),
    (_R + "arap_balance", _auth(side="ar", accounts="1122")),
    (_R + "arap_balance", _auth(side="ar", exclude_accounts=["1"] * 21)),
    (_R + "arap_balance", _auth(side="ar", partner="")),
    (_R + "arap_balance", _auth(side="ar", limit=1001)),
    (_R + "arap_balance", _auth(side="ar", basis="due")),
    (_R + "arap_balance", _auth(side="ar", buckets=[30])),
    (_R + "arap_aging", _auth(side="ar", basis="invoice")),
    (_R + "arap_aging", _auth(side="ar", buckets=[])),
    (_R + "arap_aging", _auth(side="ar", buckets=list(range(1, 12)))),
    (_R + "arap_aging", _auth(side="ar", buckets=[60, 30])),
    (_R + "arap_aging", _auth(side="ar", buckets=[30, 30])),
    (_R + "arap_aging", _auth(side="ar", buckets=[0, 30])),
    (_R + "arap_aging", _auth(side="ar", buckets=[30, 3651])),
    (_R + "arap_aging", _auth(side="ar", buckets=[30.5])),
    (_R + "arap_aging", _auth(side="ar", group_by="dept")),
    (_R + "arap_aging", _auth(side="ar", person=[])),
    (_R + "arap_aging", _auth(side="ar", person=["P"] * 21)),
    (_R + "arap_aging", _auth(side="ar", person=[" "])),
    (_R + "arap_aging", _auth(side="ar", person=["P" * 21])),
    (_R + "arap_aging", _auth(side="ar", person="P001")),
    (_R + "arap_aging", _auth(side="ar", overdue_only=True)),
    (_R + "arap_aging", _auth(side="ar", basis="document", overdue_only=True)),
    (_R + "arap_aging", _auth(side="ar", basis="due", overdue_only=1)),
    (_R + "arap_aging", _auth(side="ar", default_credit_days=30)),
    (_R + "arap_aging", _auth(side="ar", basis="document", default_credit_days=30)),
    (_R + "arap_aging", _auth(side="ar", basis="due", default_credit_days=-1)),
    (_R + "arap_aging", _auth(side="ar", basis="due", default_credit_days=3651)),
    (_R + "arap_aging", _auth(side="ar", basis="due", default_credit_days="30")),
    (_R + "arap_aging", _auth(side="ar", basis="due", default_credit_days=30.5)),
    (_R + "arap_aging", _auth(side="ar", basis="due", default_credit_days=True)),
    (_R + "arap_balance", _auth(side="ar", default_credit_days=30)),
    (_R + "arap_balance", _auth(side="ar", group_by="person")),
    (_R + "arap_balance", _auth(side="ar", person=["P001"])),
    (_R + "bom", _auth()),
    (_R + "bom", _auth(parent="")),
    (_R + "bom", _auth(parent="x" * 61)),
    (_R + "bom", _auth(parent="a\tb")),
    (_R + "bom", _auth(parent="P1", levels=0)),
    (_R + "bom", _auth(parent="P1", levels=11)),
    (_R + "bom", _auth(parent="P1", limit=5001)),
    (_R + "bom", _auth(parent="P1", as_of="26-09-2026")),
    (_R + "bom", _auth(parent="P1", id=1)),
)

_OPENAPI = {
    "close_status": ("月结状态", "coReportCloseStatus"),
    "gl_balance": ("科目余额表", "coReportGlBalance"),
    "gl_aux_balance": ("辅助核算余额表", "coReportGlAuxBalance"),
    "arap_balance": ("往来余额", "coReportArapBalance"),
    "arap_aging": ("账龄分析", "coReportArapAging"),
    "bom": ("物料清单", "coReportBom"),
}

_MINIMAL = {
    "close_status": _auth(),
    "gl_balance": _auth(**_GL),
    "gl_aux_balance": _auth(dim="customer", **_GL),
    "arap_balance": _auth(side="ar"),
    "arap_aging": _auth(side="ap"),
    "bom": _auth(parent="INV0012"),
}


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_reports_forward_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
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
    assert b'"password"' not in raw


def test_after_and_lists_pass_through_unchanged() -> None:
    fake = FakeBridge()
    client = _client(fake)
    opaque = "112201|C001|~"
    ok = client.post(_R + "gl_aux_balance", json=_auth(dim="customer", after=opaque, **_GL))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["after"] == opaque
    body = _auth(side="ar", accounts=["1122"], exclude_accounts=["112204", "112205"], buckets=[1, 3650], nonzero=False)
    ok = client.post(_R + "arap_aging", json=body)
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert sent["accounts"] == ["1122"]
    assert sent["exclude_accounts"] == ["112204", "112205"]
    assert sent["buckets"] == [1, 3650]
    assert sent["nonzero"] is False


def test_fiscal_year_is_not_filled_by_the_api() -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_R + "gl_balance", json=_auth(year="2024", **_GL))
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert sent["year"] == "2024"
    assert "fiscal_year" not in sent


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_reports_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    raw = json.dumps(body, allow_nan=True)
    denied = client.post(path, content=raw, headers={"content-type": "application/json"})
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize(
    "body",
    [
        _auth(side="ar", default_credit_days=30),
        _auth(side="ar", basis="document", default_credit_days=30),
        _auth(side="ar", basis="due", default_credit_days=3651),
    ],
)
def test_aging_default_credit_days_names_the_field(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_R + "arap_aging", json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["field"] == "default_credit_days"
    assert fake.calls == []


class _ReportBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        if path == "/v1/reports/arap_aging":
            buckets = [{"key": "not_due", "from": None, "to": 0}, {"key": "1+", "from": 1, "to": None}]
            item = {"partner": "C001", "name": "示例客户", "balance": 7.5, "aging": [0.0, 7.5], "prepaid": 0.0, "x": 1}
            return {"ok": True, "side": "ar", "basis": "document", "buckets": buckets, "items": [item], "next": None}
        if path == "/v1/reports/bom":
            row = {"level": 1, "parent": "P", "component": "C", "qty": 0.8, "path": "/P/C/"}
            return {"ok": True, "parent": "P", "bom_id": 15, "version": 10, "items": [row], "truncated": False}
        return {"ok": True, "fiscal_year": 2026, "items": [{"code": "1001", "close_debit": 1.00}], "next": "1001"}


def test_report_responses_keep_bridge_fields() -> None:
    client = _client(_ReportBridge())
    aging = client.post(_R + "arap_aging", json=_auth(side="ar")).json()
    assert aging["items"][0]["aging"] == [0.0, 7.5]
    assert aging["items"][0]["x"] == 1
    assert aging["buckets"][0]["from"] is None or "from" not in aging["buckets"][0]
    bom = client.post(_R + "bom", json=_auth(parent="P")).json()
    assert bom["version"] == 10
    assert bom["truncated"] is False
    assert bom["items"][0]["qty"] == 0.8
    gl = client.post(_R + "gl_balance", json=_auth(**_GL)).json()
    assert gl["items"][0]["close_debit"] == 1.00
    assert gl["next"] == "1001"


def test_bom_not_found_is_404() -> None:
    fake = FakeBridge()
    fake.error = ApiError(404, "not_found", "该物料在指定日期没有已审核的物料清单")
    missing = _client(fake).post(_R + "bom", json=_auth(parent="P"))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize("name", _NAMES)
def test_reports_are_read_only_and_gated(name: str) -> None:
    body = _MINIMAL[name]
    fake = FakeBridge()
    assert _client(fake, co_enabled=False).post(_R + name, json=body).status_code == 404
    denied = _client(fake, claims=base_claims()).post(_R + name, json=body)
    assert denied.status_code == 403
    outside = _client(fake).post(_R + name, json=dict(body, acc="001"))
    assert outside.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(_R + name, json=body)
    assert reader.status_code == 200, reader.text
    assert fake.calls[-1][0] == "/v1/reports/" + name


@pytest.mark.parametrize(
    ("name", "body", "view"),
    (
        ("gl_balance", _auth(**_GL), "co:reports/gl_balance:gl_balance"),
        ("gl_aux_balance", _auth(dim="person", **_GL), "co:reports/gl_aux_balance:gl_aux_balance#person"),
        ("arap_aging", _auth(side="ap"), "co:reports/arap_aging:arap_aging#ap"),
        ("bom", _auth(parent="INV0012"), "co:reports/bom:bom#INV0012"),
    ),
)
def test_report_audit_names_the_report(name: str, body: dict, view: str, capsys) -> None:
    ok = _client().post(_R + name, json=body)
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert _audit_line(out, _R + name)["action"] == view


def test_reports_openapi_paths_summaries_and_tags() -> None:
    spec = _spec()
    for name, (summary, operation_id) in _OPENAPI.items():
        operation = spec["paths"][_R + name]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert operation["tags"] == ["报表"]
        assert "权限：只读" in operation["description"]
    assert set(_enum(spec, "ReportGlAuxIn", "dim")) == {"customer", "vendor", "dept", "person", "project"}
    assert set(_enum(spec, "ReportArapIn", "side")) == {"ar", "ap"}
    assert "报表" in spec["info"]["description"]
