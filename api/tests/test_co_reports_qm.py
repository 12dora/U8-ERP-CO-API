"""/v1/co/reports/qm_indicators、qm_pass_rate：请求模型（缺省、范围、枚举、必填、未知字段）、转发的字段、提前拒绝、
权限、审计和 OpenAPI。假桥在本机。"""

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
from u8co_api.co_models_reports_qm import (
    QM_INDICATORS_DOC,
    QM_INDICATORS_SUMMARY,
    QM_PASS_RATE_DOC,
    QM_PASS_RATE_SUMMARY,
    QmIndicatorsIn,
    QmIndicatorsOut,
    QmPassRateIn,
    QmPassRateOut,
)

_R = "/v1/co/reports/"
_BASE = {"kind": "incoming", "date_from": "2026-09-01"}
_NAMES = ("qm_indicators", "qm_pass_rate")
_LOGIN = {"acc": "801", "operator": "op001", "password": "x"}


def _model(cls, **extra):
    return cls(**_LOGIN, **extra)


def _fwd(name: str, body: dict, *keys: str) -> tuple:
    return (_R + name, "/v1/reports/" + name, body, _sealed(*keys))


# ---------- 模型 ----------


def test_models_minimal_request_has_no_defaults_filled() -> None:
    ind = _model(QmIndicatorsIn, **_BASE)
    assert ind.kind == "incoming"
    assert (ind.date_to, ind.inv, ind.inv_class, ind.vendor, ind.guide, ind.include_unverified, ind.limit) == (
        None,
        None,
        None,
        None,
        None,
        None,
        None,
    )
    rate = _model(QmPassRateIn, **_BASE)
    assert rate.group_by is None
    assert rate.audit_ref() == "incoming"


def test_models_accept_full_requests() -> None:
    ind = _model(
        QmIndicatorsIn,
        kind="product",
        date_from="2026-09-01",
        date_to="2026-09-30",
        inv=["9909001", "9909002"],
        inv_class="01.02",
        guide=["001"],
        include_unverified=True,
        limit=1000,
    )
    assert ind.limit == 1000 and ind.guide == ["001"] and ind.inv_class == "01.02"
    rate = _model(QmPassRateIn, kind="incoming", date_from="2026-09-01", group_by="vendor", vendor=["S900001"])
    assert rate.group_by == "vendor"
    assert _model(QmPassRateIn, kind="product", date_from="2026-09-01", group_by="inventory").group_by == "inventory"


@pytest.mark.parametrize("cls", (QmIndicatorsIn, QmPassRateIn))
@pytest.mark.parametrize(
    "extra",
    (
        {"date_from": "2026-09-01"},
        {"kind": "incoming"},
        {"kind": "other", "date_from": "2026-09-01"},
        {"kind": "incoming", "date_from": "2026-9-1"},
        {"kind": "incoming", "date_from": "2026-02-30"},
        {"kind": "incoming", "date_from": "2026-09-01", "date_to": "2026-08-31"},
        {"kind": "incoming", "date_from": "2026-09-01", "date_to": "2026/09/30"},
        {"kind": "product", "date_from": "2026-09-01", "vendor": ["S900001"]},
        {"kind": "incoming", "date_from": "2026-09-01", "vendor": []},
        {"kind": "incoming", "date_from": "2026-09-01", "vendor": ["S"] * 21},
        {"kind": "incoming", "date_from": "2026-09-01", "inv": []},
        {"kind": "incoming", "date_from": "2026-09-01", "inv": ["C"] * 21},
        {"kind": "incoming", "date_from": "2026-09-01", "inv": [" "]},
        {"kind": "incoming", "date_from": "2026-09-01", "inv": "9909001"},
        {"kind": "incoming", "date_from": "2026-09-01", "inv_class": "01%"},
        {"kind": "incoming", "date_from": "2026-09-01", "include_unverified": "true"},
        {"kind": "incoming", "date_from": "2026-09-01", "limit": 0},
        {"kind": "incoming", "date_from": "2026-09-01", "limit": 1001},
        {"kind": "incoming", "date_from": "2026-09-01", "limit": "5"},
        {"kind": "incoming", "date_from": "2026-09-01", "after": "x"},
    ),
)
def test_models_reject_bad_requests(cls, extra: dict) -> None:
    with pytest.raises(ValidationError):
        _model(cls, **extra)


@pytest.mark.parametrize(
    "extra",
    (
        {"guide": ["001"]},
        {"group_by": "vendor", "kind": "product"},
        {"group_by": "week"},
        {"group_by": ["month"]},
    ),
)
def test_models_family_specific_fields(extra: dict) -> None:
    with pytest.raises(ValidationError):
        _model(QmPassRateIn, **{**_BASE, **extra})
    if "guide" not in extra:
        with pytest.raises(ValidationError):
            _model(QmIndicatorsIn, **{**_BASE, **extra})


def test_models_guide_is_indicators_only_and_bounded() -> None:
    with pytest.raises(ValidationError):
        _model(QmIndicatorsIn, **_BASE, guide=[])
    with pytest.raises(ValidationError):
        _model(QmIndicatorsIn, **_BASE, guide=["G"] * 21)


def test_models_out_keep_extra_bridge_fields() -> None:
    out = QmIndicatorsOut(ok=True, items=[{"inv_code": "I1", "x": 1}], extra_field=7)
    assert out.model_dump()["extra_field"] == 7
    rate = QmPassRateOut(ok=True, totals={"docs": 3}, items=[], group_by="month")
    assert rate.totals == {"docs": 3}


def test_models_texts_are_filled() -> None:
    for text in (QM_INDICATORS_SUMMARY, QM_INDICATORS_DOC, QM_PASS_RATE_SUMMARY, QM_PASS_RATE_DOC):
        assert text.strip()
    assert "**规则**" in QM_INDICATORS_DOC and "**规则**" in QM_PASS_RATE_DOC


# ---------- 路由 ----------

_FORWARD = (
    _fwd("qm_indicators", _auth(**_BASE), "kind", "date_from"),
    _fwd(
        "qm_indicators",
        _auth(
            kind="incoming",
            date_from="2026-09-01",
            date_to="2026-09-28",
            inv=["I1", "I2"],
            inv_class="0102",
            vendor=["S900001"],
            guide=["001", "002"],
            include_unverified=True,
            limit=1000,
        ),
        "kind", "date_from", "date_to", "inv", "inv_class", "vendor", "guide", "include_unverified", "limit",
    ),
    _fwd("qm_indicators", _auth(kind="product", date_from="2026-09-01", limit=1), "kind", "date_from", "limit"),
    _fwd("qm_pass_rate", _auth(**_BASE), "kind", "date_from"),
    _fwd(
        "qm_pass_rate",
        _auth(
            kind="incoming",
            date_from="2026-09-01",
            date_to="2026-09-28",
            group_by="vendor",
            inv=["I1"],
            inv_class="0102",
            vendor=["S900001"],
            include_unverified=False,
            limit=10,
        ),
        "kind", "date_from", "date_to", "group_by", "inv", "inv_class", "vendor", "include_unverified", "limit",
    ),
    _fwd(
        "qm_pass_rate",
        _auth(kind="product", date_from="2026-09-01", group_by="inventory"),
        "kind", "date_from", "group_by",
    ),
)

_REJECTS = (
    (_R + "qm_indicators", _auth()),
    (_R + "qm_indicators", _auth(date_from="2026-09-01")),
    (_R + "qm_indicators", _auth(kind="incoming")),
    (_R + "qm_indicators", _auth(kind="other", date_from="2026-09-01")),
    (_R + "qm_indicators", _auth(kind="product", date_from="2026-09-01", vendor=["S900001"])),
    (_R + "qm_indicators", _auth(kind="incoming", date_from="2026-09-01", date_to="2026-08-01")),
    (_R + "qm_indicators", _auth(kind="incoming", date_from="2026-02-30")),
    (_R + "qm_indicators", _auth(kind="incoming", date_from="2026-09-01", inv=[])),
    (_R + "qm_indicators", _auth(kind="incoming", date_from="2026-09-01", guide=["G"] * 21)),
    (_R + "qm_indicators", _auth(kind="incoming", date_from="2026-09-01", inv_class="01%")),
    (_R + "qm_indicators", _auth(kind="incoming", date_from="2026-09-01", limit=1001)),
    (_R + "qm_indicators", _auth(kind="incoming", date_from="2026-09-01", group_by="month")),
    (_R + "qm_indicators", _auth(kind="incoming", date_from="2026-09-01", after="x")),
    (_R + "qm_pass_rate", _auth()),
    (_R + "qm_pass_rate", _auth(kind="product", date_from="2026-09-01", group_by="vendor")),
    (_R + "qm_pass_rate", _auth(kind="incoming", date_from="2026-09-01", group_by="week")),
    (_R + "qm_pass_rate", _auth(kind="incoming", date_from="2026-09-01", guide=["001"])),
    (_R + "qm_pass_rate", _auth(kind="incoming", date_from="2026-09-01", include_unverified="yes")),
    (_R + "qm_pass_rate", _auth(kind="incoming", date_from="2026-09-01", limit=0)),
)

_OPENAPI = {
    "qm_indicators": ("检验指标统计", "coReportQmIndicators"),
    "qm_pass_rate": ("检验合格率统计", "coReportQmPassRate"),
}

_MINIMAL = {name: _auth(**_BASE) for name in _NAMES}


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_qm_reports_forward_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
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
def test_qm_reports_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _QmBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        if path == "/v1/reports/qm_indicators":
            row = {"inv_code": "I1", "guide_code": "001", "n": 3, "mean": 9.5, "stdev": None, "months": [], "x": 1}
            return {"ok": True, "kind": "product", "items": [row], "count": 1, "truncated": False}
        row = {"month": "2026-09", "docs": 4, "pass_docs": 2, "fail_docs": 1, "undecided_docs": 1, "qty": 10.0, "y": 2}
        return {"ok": True, "group_by": "month", "items": [row], "count": 1, "truncated": False, "totals": {"docs": 4}}


def test_qm_report_responses_keep_bridge_fields() -> None:
    client = _client(_QmBridge())
    ind = client.post(_R + "qm_indicators", json=_auth(kind="product", date_from="2026-09-01")).json()
    assert ind["items"][0]["x"] == 1
    assert ind["items"][0]["mean"] == 9.5
    assert ind["items"][0]["stdev"] is None
    assert ind["count"] == 1 and ind["truncated"] is False
    rate = client.post(_R + "qm_pass_rate", json=_auth(**_BASE)).json()
    assert rate["items"][0]["y"] == 2
    assert rate["items"][0]["undecided_docs"] == 1
    assert rate["totals"] == {"docs": 4}
    assert rate["group_by"] == "month"


@pytest.mark.parametrize("name", _NAMES)
def test_qm_reports_are_read_only_and_gated(name: str) -> None:
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
            "qm_indicators",
            _auth(kind="product", date_from="2026-09-01"),
            "co:reports/qm_indicators:qm_indicators#product",
        ),
        ("qm_pass_rate", _auth(**_BASE), "co:reports/qm_pass_rate:qm_pass_rate#incoming"),
    ),
)
def test_qm_report_audit_names_the_report(name: str, body: dict, view: str, capsys) -> None:
    ok = _client().post(_R + name, json=body)
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert _audit_line(out, _R + name)["action"] == view


def test_qm_reports_openapi_paths_summaries_and_tags() -> None:
    spec = _spec()
    for name, (summary, operation_id) in _OPENAPI.items():
        operation = spec["paths"][_R + name]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert operation["tags"] == ["报表"]
        assert "权限：只读" in operation["description"]
    assert set(_enum(spec, "QmIndicatorsIn", "kind")) == {"incoming", "product"}
    assert set(_enum(spec, "QmPassRateIn", "group_by")) == {"month", "vendor", "inventory"}
