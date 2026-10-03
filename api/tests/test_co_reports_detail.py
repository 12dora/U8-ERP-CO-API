"""明细账报表 /v1/co/reports/arap_detail、gl_detail：转发的字段、提前拒绝、权限、审计和 OpenAPI。假桥在本机。"""

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
_ARAP = {"side": "ar", "partner": "C001", "date_from": "2026-08-01"}
_GL = {"code": "1122", "period_from": 8, "period_to": 8}
_GL_DATES = {"code": "1002", "date_from": "2026-09-05", "date_to": "2026-09-20"}


def _fwd(name: str, body: dict, *keys: str) -> tuple:
    return (_R + name, "/v1/reports/" + name, body, _sealed(*keys))


_FORWARD = (
    _fwd("arap_detail", _auth(**_ARAP), "side", "partner", "date_from"),
    _fwd(
        "arap_detail",
        _auth(
            side="ap",
            partner=["V001", "V002"],
            date_from="2026-01-01",
            date_to="2026-08-31",
            basis="register",
            accounts=["2202"],
            exclude_accounts=["220299"],
            dept="D01",
            person="P001",
            include_writeoff=True,
            after="opaque",
            limit=1000,
        ),
        "side",
        "partner",
        "date_from",
        "date_to",
        "basis",
        "accounts",
        "exclude_accounts",
        "dept",
        "person",
        "include_writeoff",
        "after",
        "limit",
    ),
    _fwd("gl_detail", _auth(**_GL), "code", "period_from", "period_to"),
    _fwd(
        "gl_detail",
        _auth(
            include_sub=False,
            fiscal_year=2026,
            include_unposted=True,
            customer="C001",
            vendor="V001",
            dept="D01",
            person="P001",
            project="01",
            project_class="00",
            after="x",
            limit=1,
            **_GL_DATES,
        ),
        "code",
        "date_from",
        "date_to",
        "include_sub",
        "fiscal_year",
        "include_unposted",
        "customer",
        "vendor",
        "dept",
        "person",
        "project",
        "project_class",
        "after",
        "limit",
    ),
)

_REJECTS = (
    (_R + "arap_detail", _auth(partner="C001", date_from="2026-08-01")),
    (_R + "arap_detail", _auth(side="ar", date_from="2026-08-01")),
    (_R + "arap_detail", _auth(side="ar", partner="C001")),
    (_R + "arap_detail", _auth(side="ar", partner="", date_from="2026-08-01")),
    (_R + "arap_detail", _auth(side="ar", partner=[], date_from="2026-08-01")),
    (_R + "arap_detail", _auth(side="ar", partner=["C"] * 21, date_from="2026-08-01")),
    (_R + "arap_detail", _auth(side="ar", partner=["a\nb"], date_from="2026-08-01")),
    (_R + "arap_detail", _auth(side="ar", partner=1, date_from="2026-08-01")),
    (_R + "arap_detail", _auth(date_from="2026-8-01", side="ar", partner="C001")),
    (_R + "arap_detail", _auth(date_to="2026-07-31", **_ARAP)),
    (_R + "arap_detail", _auth(date_to="2026-02-30", **_ARAP)),
    (_R + "arap_detail", _auth(basis="due", **_ARAP)),
    (_R + "arap_detail", _auth(accounts=[], **_ARAP)),
    (_R + "arap_detail", _auth(accounts=["11%"], **_ARAP)),
    (_R + "arap_detail", _auth(dept="D" * 21, **_ARAP)),
    (_R + "arap_detail", _auth(person=" ", **_ARAP)),
    (_R + "arap_detail", _auth(include_writeoff="true", **_ARAP)),
    (_R + "arap_detail", _auth(limit=1001, **_ARAP)),
    (_R + "arap_detail", _auth(as_of="2026-08-31", **_ARAP)),
    (_R + "arap_detail", _auth(fiscal_year=2026, **_ARAP)),
    (_R + "gl_detail", _auth(period_from=8, period_to=8)),
    (_R + "gl_detail", _auth(code="11%", period_from=8, period_to=8)),
    (_R + "gl_detail", _auth(code="1" * 41, period_from=8, period_to=8)),
    (_R + "gl_detail", _auth(code="1122")),
    (_R + "gl_detail", _auth(code="1122", period_from=8)),
    (_R + "gl_detail", _auth(code="1122", period_from=9, period_to=8)),
    (_R + "gl_detail", _auth(code="1122", period_from=0, period_to=8)),
    (_R + "gl_detail", _auth(date_from="2026-09-05", **_GL)),
    (_R + "gl_detail", _auth(code="1122", date_from="2026-09-05")),
    (_R + "gl_detail", _auth(code="1122", date_from="2026-09-05", date_to="2026-09-04")),
    (_R + "gl_detail", _auth(code="1122", date_from="2025-12-31", date_to="2026-01-01")),
    (_R + "gl_detail", _auth(fiscal_year=2025, **_GL_DATES)),
    (_R + "gl_detail", _auth(project_class="00", **_GL)),
    (_R + "gl_detail", _auth(customer="", **_GL)),
    (_R + "gl_detail", _auth(include_sub=1, **_GL)),
    (_R + "gl_detail", _auth(include_unposted="false", **_GL)),
    (_R + "gl_detail", _auth(limit=0, **_GL)),
    (_R + "gl_detail", _auth(after="x y", **_GL)),
    (_R + "gl_detail", _auth(dim="customer", **_GL)),
)


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_detail_forward_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
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


def test_partner_single_and_list_pass_through_unchanged() -> None:
    fake = FakeBridge()
    client = _client(fake)
    ok = client.post(_R + "arap_detail", json=_auth(**_ARAP))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["partner"] == "C001"
    body = dict(_ARAP, partner=["C001", "C002"], include_writeoff=False)
    ok = client.post(_R + "arap_detail", json=_auth(**body))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["partner"] == ["C001", "C002"]
    assert fake.calls[-1][1]["include_writeoff"] is False


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_detail_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, content=json.dumps(body), headers={"content-type": "application/json"})
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _DetailBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        if path == "/v1/reports/arap_detail":
            partner = {"partner": "C001", "name": "示例客户", "opening": 10.0, "debit": 5.0, "closing": 13.0}
            row = {"partner": "C001", "date": "2026-08-13", "doc_type": "26", "debit": 5.0, "balance": 15.0, "x": 1}
            return {"ok": True, "side": "ar", "partners": [partner], "items": [row], "next": "abc"}
        row = {"date": "2026-08-01", "sign": "转", "no": 1, "entry": 2, "credit": 100.0, "dir": "借", "balance": 9.5}
        return {"ok": True, "code": "1122", "open_dir": "借", "open_debit": 109.5, "items": [row], "next": None}


def test_detail_responses_keep_bridge_fields() -> None:
    client = _client(_DetailBridge())
    arap = client.post(_R + "arap_detail", json=_auth(**_ARAP)).json()
    assert arap["partners"][0]["closing"] == 13.0
    assert arap["items"][0]["x"] == 1
    assert arap["next"] == "abc"
    gl = client.post(_R + "gl_detail", json=_auth(**_GL)).json()
    assert gl["open_debit"] == 109.5
    assert gl["items"][0]["dir"] == "借"
    assert gl.get("next") is None  # 路由 response_model_exclude_none：null 字段不输出


def test_gl_detail_missing_account_is_404() -> None:
    fake = FakeBridge()
    fake.error = ApiError(404, "not_found", "科目 9999 在 2026 年度不存在")
    missing = _client(fake).post(_R + "gl_detail", json=_auth(code="9999", period_from=1, period_to=1))
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(("name", "body"), (("arap_detail", _auth(**_ARAP)), ("gl_detail", _auth(**_GL))))
def test_detail_reports_are_read_only_and_gated(name: str, body: dict) -> None:
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
        ("arap_detail", _auth(**dict(_ARAP, side="ap")), "co:reports/arap_detail:arap_detail#ap"),
        ("gl_detail", _auth(**_GL), "co:reports/gl_detail:gl_detail#1122"),
    ),
)
def test_detail_audit_names_the_report(name: str, body: dict, view: str, capsys) -> None:
    ok = _client().post(_R + name, json=body)
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert _audit_line(out, _R + name)["action"] == view


def test_detail_openapi_paths_summaries_and_tags() -> None:
    spec = _spec()
    names = {"arap_detail": ("往来明细账", "coReportArapDetail"), "gl_detail": ("科目明细账", "coReportGlDetail")}
    for name, (summary, operation_id) in names.items():
        operation = spec["paths"][_R + name]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert operation["tags"] == ["报表"]
        assert "权限：只读" in operation["description"]
    basis = str(spec["components"]["schemas"]["ReportArapDetailIn"]["properties"]["basis"])
    assert "register" in basis and "document" not in basis
