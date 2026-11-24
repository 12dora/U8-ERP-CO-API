"""成本核算期间数据 /v1/co/reports/ca_inputs：模型校验（缺省、上限、必填、未知字段）、转发的字段、提前拒绝、
响应字段原样返回、权限和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.support import base_claims
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, READ
from u8co_api.co_models_reports_ca import CA_INPUTS_DOC, CA_INPUTS_SUMMARY, CaInputsIn, CaInputsOut

_PATH = "/v1/co/reports/ca_inputs"
_BRIDGE = "/v1/reports/ca_inputs"
_LOGIN = {"acc": "801", "operator": "op001", "password": "pw"}
_TABLES = (
    "work_hours",
    "daily_hours",
    "labor",
    "overhead",
    "depreciation",
    "wip_fixed",
    "wip_variable",
    "distr_input",
    "product_qty",
)

_FORWARD = (
    (_auth(), _sealed()),
    (
        _auth(fiscal_year=2026, period=10, tables=["labor", "overhead"], limit=20000),
        _sealed("fiscal_year", "period", "tables", "limit"),
    ),
    (_auth(tables=list(_TABLES)), _sealed("tables")),
)

_REJECTS = (
    _auth(period=0),
    _auth(period=13),
    _auth(period="9"),
    _auth(period=True),
    _auth(fiscal_year=1999),
    _auth(fiscal_year=2100),
    _auth(limit=0),
    _auth(limit=20001),
    _auth(limit="5"),
    _auth(limit=1.5),
    _auth(tables=[]),
    _auth(tables="labor"),
    _auth(tables=["CA_DirMA"]),
    _auth(tables=["labor", "labor"]),
    _auth(tables=list(_TABLES) + ["labor"]),
    _auth(period_from=1),
    _auth(after="x"),
)


def test_ca_inputs_in_defaults_and_values() -> None:
    minimal = CaInputsIn(**_LOGIN)
    assert (minimal.fiscal_year, minimal.period, minimal.tables, minimal.limit) == (None, None, None, None)
    full = CaInputsIn(**_LOGIN, fiscal_year=2026, period=12, tables=list(_TABLES), limit=20000)
    assert (full.fiscal_year, full.period, full.tables, full.limit) == (2026, 12, list(_TABLES), 20000)
    assert CaInputsIn(**_LOGIN, period=1, limit=1).period == 1


@pytest.mark.parametrize(
    "extra",
    (
        {"period": 0},
        {"period": 13},
        {"period": "9"},
        {"period": 9.5},
        {"limit": 0},
        {"limit": 20001},
        {"tables": []},
        {"tables": ["labor", "labor"]},
        {"tables": ["direct_cost"]},
        {"unknown": 1},
    ),
)
def test_ca_inputs_in_rejects(extra: dict) -> None:
    with pytest.raises(ValidationError):
        CaInputsIn(**_LOGIN, **extra)


def test_out_model_keeps_bridge_fields() -> None:
    out = CaInputsOut(
        ok=True,
        fiscal_year=2026,
        period=10,
        status={"calculated_through": None, "closed": False},
        tables={"labor": {"count": 0, "rows": []}},
        extra_field=True,
    )
    dumped = out.model_dump()
    assert dumped["skipped"] is None
    assert dumped["status"]["closed"] is False
    assert dumped["tables"]["labor"]["count"] == 0
    assert dumped["extra_field"] is True


def test_summary_and_doc_are_filled() -> None:
    assert CA_INPUTS_SUMMARY == "成本核算期间数据"
    for word in ("calculated_through", "wip_fixed", "cost_center", "只查数据库"):
        assert word in CA_INPUTS_DOC


@pytest.mark.parametrize(("body", "keys"), _FORWARD)
def test_ca_inputs_forwards_exact_sealed_keys(body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(_PATH, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + _BRIDGE
    assert set(sent) == keys
    assert _SECRET.encode() not in raw


@pytest.mark.parametrize("body", _REJECTS)
def test_ca_inputs_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _CaBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        row = {
            "period": "2026-10",
            "object_id": 2232,
            "cost_center": "CC001",
            "cost_center_name": "文本A",
            "inv_code": "9909001",
            "mo_code": "MO-001",
            "type": "1",
            "values": {"iRealWkt": 12.5, "iOnpWkt": 0},
        }
        return {
            "ok": True,
            "fiscal_year": 2026,
            "period": 10,
            "status": {"calculated_through": "2026-10", "closed": False},
            "tables": {"work_hours": {"count": 1, "rows": [row]}},
            "skipped": [{"table": "overhead", "reason": "no_permission"}],
        }


def test_ca_inputs_response_keeps_bridge_fields() -> None:
    body = _client(_CaBridge()).post(_PATH, json=_auth()).json()
    assert body["status"] == {"calculated_through": "2026-10", "closed": False}
    assert body["tables"]["work_hours"]["count"] == 1
    assert body["tables"]["work_hours"]["rows"][0]["values"]["iRealWkt"] == 12.5
    assert body["tables"]["work_hours"]["rows"][0]["mo_code"] == "MO-001"
    assert body["skipped"] == [{"table": "overhead", "reason": "no_permission"}]


def test_ca_inputs_is_read_only_and_gated() -> None:
    assert ACCESS["co:reports/ca_inputs"] == READ
    body = _auth()
    fake = FakeBridge()
    assert _client(fake, co_enabled=False).post(_PATH, json=body).status_code == 404
    assert _client(fake, claims=base_claims()).post(_PATH, json=body).status_code == 403
    outside = _client(fake).post(_PATH, json=dict(body, acc="001"))
    assert outside.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=body)
    assert reader.status_code == 200, reader.text
    assert fake.calls[-1][0] == _BRIDGE


def test_ca_inputs_openapi_path_summary_and_tag() -> None:
    spec = _spec()
    operation = spec["paths"][_PATH]["post"]
    assert operation["summary"] == "成本核算期间数据"
    assert operation["operationId"] == "coReportCaInputs"
    assert operation["tags"] == ["报表"]
    assert "权限：只读" in operation["description"]
    properties = spec["components"]["schemas"]["CaInputsIn"]["properties"]
    assert {"fiscal_year", "period", "tables", "limit"} <= set(properties)
