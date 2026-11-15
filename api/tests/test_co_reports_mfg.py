"""生产类报表 /v1/co/reports/production_daily、mo_progress、mo_consumption：
模型校验（缺省、上下限、枚举、必填、未知字段）、转发的字段、提前拒绝、权限和响应。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.support import base_claims
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_gate import FakeBridge, _client
from u8co_api.co_access import ACCESS, READ
from u8co_api.co_models_reports_mfg import (
    MO_CONSUMPTION_DOC,
    MO_CONSUMPTION_SUMMARY,
    MO_PROGRESS_DOC,
    MO_PROGRESS_SUMMARY,
    PRODUCTION_DAILY_DOC,
    PRODUCTION_DAILY_SUMMARY,
    MoConsumptionIn,
    MoConsumptionOut,
    MoProgressIn,
    MoProgressOut,
    ProductionDailyIn,
    ProductionDailyOut,
)

_R = "/v1/co/reports/"
_NAMES = ("production_daily", "mo_progress", "mo_consumption")
_LOGIN = {"acc": "801", "operator": "op001", "password": "x"}
_FROM = {"date_from": "2026-09-01"}


def _model(cls, **extra):
    return cls(**_LOGIN, **extra)


def _fwd(name: str, body: dict, *keys: str) -> tuple:
    return (_R + name, "/v1/reports/" + name, body, _sealed(*keys))


_FORWARD = (
    _fwd("production_daily", _auth(**_FROM), "date_from"),
    _fwd(
        "production_daily",
        _auth(date_to="2026-09-30", group_by="month", by="class", class_grade=3, inv=["9909001"], inv_class="0101",
              include_unverified=True, limit=5000, **_FROM),
        "date_from", "date_to", "group_by", "by", "class_grade", "inv", "inv_class", "include_unverified", "limit",
    ),
    _fwd("mo_progress", _auth(**_FROM), "date_from"),
    _fwd(
        "mo_progress",
        _auth(date_to="2026-09-30", inv=["9909001", "9909002"], inv_class="01", open_only=True, limit=2000, **_FROM),
        "date_from", "date_to", "inv", "inv_class", "open_only", "limit",
    ),
    _fwd("mo_consumption", _auth(**_FROM), "date_from"),
    _fwd(
        "mo_consumption",
        _auth(date_to="2026-09-30", parent=["0301001"], inv_class="03", limit=1, **_FROM),
        "date_from", "date_to", "parent", "inv_class", "limit",
    ),
)

_REJECTS = tuple(
    (_R + name, _auth(**body))
    for name, body in (
        ("production_daily", {}),
        ("production_daily", {"date_from": "2026-9-1"}),
        ("production_daily", {"date_from": "2026-02-30"}),
        ("production_daily", {"date_from": "2026-09-30", "date_to": "2026-09-01"}),
        ("production_daily", dict(_FROM, group_by="week")),
        ("production_daily", dict(_FROM, by="customer")),
        ("production_daily", dict(_FROM, class_grade=0)),
        ("production_daily", dict(_FROM, class_grade=10)),
        ("production_daily", dict(_FROM, limit=5001)),
        ("production_daily", dict(_FROM, limit=0)),
        ("production_daily", dict(_FROM, include_unverified="true")),
        ("production_daily", dict(_FROM, inv=[])),
        ("production_daily", dict(_FROM, inv="9909001")),
        ("production_daily", dict(_FROM, inv=["9909001"] * 21)),
        ("production_daily", dict(_FROM, inv=[" "])),
        ("production_daily", dict(_FROM, inv_class="01%")),
        ("production_daily", dict(_FROM, after="x")),
        ("production_daily", dict(_FROM, parent=["0301001"])),
        ("mo_progress", {}),
        ("mo_progress", dict(_FROM, limit=2001)),
        ("mo_progress", dict(_FROM, open_only=1)),
        ("mo_progress", dict(_FROM, inv=[])),
        ("mo_progress", dict(_FROM, group_by="day")),
        ("mo_consumption", {}),
        ("mo_consumption", dict(_FROM, limit=0)),
        ("mo_consumption", dict(_FROM, parent=[])),
        ("mo_consumption", dict(_FROM, parent=["a\tb"])),
        ("mo_consumption", dict(_FROM, inv=["9909001"])),
    )
)


def test_daily_defaults_are_left_to_the_bridge() -> None:
    m = _model(ProductionDailyIn, **_FROM)
    dumped = m.model_dump(exclude_none=True)
    assert dumped["date_from"] == "2026-09-01"
    optional = {"date_to", "group_by", "by", "class_grade", "inv", "inv_class", "include_unverified", "limit"}
    assert not optional & set(dumped)


@pytest.mark.parametrize(
    ("cls", "good", "bad"),
    (
        (ProductionDailyIn, {"limit": 5000}, {"limit": 5001}),
        (ProductionDailyIn, {"class_grade": 9}, {"class_grade": 10}),
        (ProductionDailyIn, {"class_grade": 1}, {"class_grade": 0}),
        (MoProgressIn, {"limit": 2000}, {"limit": 2001}),
        (MoConsumptionIn, {"limit": 1}, {"limit": 0}),
        (ProductionDailyIn, {"inv": ["A"] * 20}, {"inv": ["A"] * 21}),
        (MoConsumptionIn, {"parent": ["A"] * 20}, {"parent": ["A"] * 21}),
    ),
)
def test_limits_are_inclusive(cls, good: dict, bad: dict) -> None:
    assert _model(cls, **_FROM, **good)
    with pytest.raises(ValidationError):
        _model(cls, **_FROM, **bad)


def test_enums_and_flags_are_strict() -> None:
    assert _model(ProductionDailyIn, group_by="month", by="class", **_FROM).by == "class"
    for bad in ({"group_by": "week"}, {"by": "vendor"}, {"include_unverified": 1}, {"limit": "10"}):
        with pytest.raises(ValidationError):
            _model(ProductionDailyIn, **_FROM, **bad)
    with pytest.raises(ValidationError):
        _model(MoProgressIn, open_only="yes", **_FROM)
    assert _model(MoProgressIn, open_only=False, **_FROM).open_only is False


@pytest.mark.parametrize("cls", (ProductionDailyIn, MoProgressIn, MoConsumptionIn))
def test_date_from_is_required_real_and_ordered(cls) -> None:
    with pytest.raises(ValidationError):
        _model(cls)
    for bad in ("2026-9-1", "2026-02-30", "20260901", ""):
        with pytest.raises(ValidationError):
            _model(cls, date_from=bad)
    with pytest.raises(ValidationError):
        _model(cls, date_from="2026-09-30", date_to="2026-09-01")
    assert _model(cls, date_from="2026-09-01", date_to="2026-09-01").date_to == "2026-09-01"


@pytest.mark.parametrize(
    ("cls", "extra"),
    ((ProductionDailyIn, "parent"), (MoProgressIn, "by"), (MoConsumptionIn, "inv"), (MoProgressIn, "after")),
)
def test_unknown_and_foreign_fields_are_rejected(cls, extra: str) -> None:
    with pytest.raises(ValidationError):
        _model(cls, **_FROM, **{extra: ["A"] if extra in {"parent", "inv"} else "x"})


def test_codes_follow_the_bridge_rules() -> None:
    assert _model(ProductionDailyIn, inv=[" 9909001"], **_FROM)
    for bad in ([" "], [""], ["a\tb"], ["x" * 61]):
        with pytest.raises(ValidationError):
            _model(ProductionDailyIn, inv=bad, **_FROM)
    for bad in ("01%", "01 02", "", "x" * 41):
        with pytest.raises(ValidationError):
            _model(MoProgressIn, inv_class=bad, **_FROM)


def test_out_models_keep_bridge_fields() -> None:
    rows = [{"date": "2026-09-01", "qty": 1.5, "x": 1}]
    out = ProductionDailyOut(ok=True, count=1, truncated=False, days=3, items=rows, y=2)
    assert out.model_dump()["y"] == 2
    assert out.items[0]["x"] == 1
    assert MoProgressOut(ok=True, items=[{"status": "open"}]).items[0]["status"] == "open"
    assert MoConsumptionOut(ok=True, count=0, truncated=True, items=[]).truncated is True


def test_texts_are_chinese_and_name_the_source() -> None:
    for summary, doc in (
        (PRODUCTION_DAILY_SUMMARY, PRODUCTION_DAILY_DOC),
        (MO_PROGRESS_SUMMARY, MO_PROGRESS_DOC),
        (MO_CONSUMPTION_SUMMARY, MO_CONSUMPTION_DOC),
    ):
        assert summary
        assert "**用法**" in doc
        assert "**规则**" in doc
        assert "只查数据库，不调用 U8 组件" in doc


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_mfg_reports_forward_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + bridge
    assert set(sent) == keys


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_mfg_reports_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _MfgBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        if path == "/v1/reports/production_daily":
            row = {"date": "2026-09-01", "inv_code": "9909001", "unit": "KG", "qty": 10.5, "docs": 1, "x": 1}
            return {"ok": True, "items": [row], "count": 1, "truncated": False, "days": 1}
        if path == "/v1/reports/mo_progress":
            row = {"mo_code": "MO001", "mo_line": 1, "qty": 100.0, "qualified_in": 90.0, "closed": False}
            row["status"] = "open"
            return {"ok": True, "items": [row], "count": 1, "truncated": False}
        row = {"parent_code": "0301001", "comp_code": "9909001", "issued_qty": 2.5}
        return {"ok": True, "items": [row], "count": 1, "truncated": True}


def test_mfg_report_responses_keep_bridge_fields() -> None:
    client = _client(_MfgBridge())
    daily = client.post(_R + "production_daily", json=_auth(**_FROM)).json()
    assert daily["days"] == 1
    assert daily["items"][0]["x"] == 1
    assert daily["items"][0]["qty"] == 10.5
    progress = client.post(_R + "mo_progress", json=_auth(**_FROM)).json()
    assert progress["items"][0]["status"] == "open"
    assert progress["items"][0]["closed"] is False
    usage = client.post(_R + "mo_consumption", json=_auth(**_FROM)).json()
    assert usage["truncated"] is True
    assert usage["items"][0]["issued_qty"] == 2.5


@pytest.mark.parametrize("name", _NAMES)
def test_mfg_reports_are_read_only_and_gated(name: str) -> None:
    assert ACCESS["co:reports/" + name] == READ
    body = _auth(**_FROM)
    fake = FakeBridge()
    assert _client(fake, co_enabled=False).post(_R + name, json=body).status_code == 404
    assert _client(fake, claims=base_claims()).post(_R + name, json=body).status_code == 403
    outside = _client(fake).post(_R + name, json=dict(body, acc="001"))
    assert outside.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(_R + name, json=body)
    assert reader.status_code == 200, reader.text
    assert fake.calls[-1][0] == "/v1/reports/" + name
