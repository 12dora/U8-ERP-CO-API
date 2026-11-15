"""订单执行、单据追溯 /v1/co/reports/order_execution、doc_trace：转发的字段、提前拒绝、权限、审计和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line as _audit_line
from tests.support import base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec
from u8co_api.co_models_reports_trace import TRACE_KINDS
from u8co_api.errors import ApiError

_R = "/v1/co/reports/"
_NAMES = ("order_execution", "doc_trace")


def _fwd(name: str, body: dict, *keys: str) -> tuple:
    return (_R + name, "/v1/reports/" + name, body, _sealed(*keys))


_FORWARD = (
    _fwd("order_execution", _auth(type="sale_order"), "type"),
    _fwd("order_execution", _auth(type="purchase_order", ids=[1000000001, 7]), "type", "ids"),
    _fwd(
        "order_execution",
        _auth(
            type="sale_order",
            code="SO0001",
            date_from="2026-01-01",
            date_to="2026-09-30",
            partner="C001",
            only_open=True,
            after="opaque",
            limit=1000,
        ),
        "type",
        "code",
        "date_from",
        "date_to",
        "partner",
        "only_open",
        "after",
        "limit",
    ),
    _fwd("doc_trace", _auth(type="sale_order", id=1000000041), "type", "id"),
    _fwd(
        "doc_trace",
        _auth(type="qm_product_reject", id=128, depth=1, direction="up", max_nodes=200),
        "type",
        "id",
        "depth",
        "direction",
        "max_nodes",
    ),
)

_REJECTS = (
    (_R + "order_execution", _auth()),
    (_R + "order_execution", _auth(type="dispatch")),
    (_R + "order_execution", _auth(type="sale_order", ids=[])),
    (_R + "order_execution", _auth(type="sale_order", ids=list(range(1, 102)))),
    (_R + "order_execution", _auth(type="sale_order", ids=[0])),
    (_R + "order_execution", _auth(type="sale_order", ids=["1"])),
    (_R + "order_execution", _auth(type="sale_order", ids=[2147483648])),
    (_R + "order_execution", _auth(type="sale_order", ids=[1], code="SO1")),
    (_R + "order_execution", _auth(type="sale_order", ids=[1], partner="C001")),
    (_R + "order_execution", _auth(type="sale_order", code="")),
    (_R + "order_execution", _auth(type="sale_order", code="x" * 31)),
    (_R + "order_execution", _auth(type="sale_order", partner="a\tb")),
    (_R + "order_execution", _auth(type="sale_order", date_from="2026-9-1")),
    (_R + "order_execution", _auth(type="sale_order", date_to="2026-02-30")),
    (_R + "order_execution", _auth(type="sale_order", date_from="2026-09-02", date_to="2026-09-01")),
    (_R + "order_execution", _auth(type="sale_order", only_open="true")),
    (_R + "order_execution", _auth(type="sale_order", limit=0)),
    (_R + "order_execution", _auth(type="sale_order", limit=1001)),
    (_R + "order_execution", _auth(type="sale_order", after="")),
    (_R + "order_execution", _auth(type="sale_order", id=1)),
    (_R + "doc_trace", _auth(type="sale_order")),
    (_R + "doc_trace", _auth(id=1)),
    (_R + "doc_trace", _auth(type="transfer", id=1)),
    (_R + "doc_trace", _auth(type="sale_order", id=0)),
    (_R + "doc_trace", _auth(type="sale_order", id="1")),
    (_R + "doc_trace", _auth(type="sale_order", id=1, depth=0)),
    (_R + "doc_trace", _auth(type="sale_order", id=1, depth=4)),
    (_R + "doc_trace", _auth(type="sale_order", id=1, direction="left")),
    (_R + "doc_trace", _auth(type="sale_order", id=1, max_nodes=201)),
    (_R + "doc_trace", _auth(type="sale_order", id=1, ids=[1])),
)

_MINIMAL = {
    "order_execution": _auth(type="purchase_order"),
    "doc_trace": _auth(type="dispatch", id=5),
}


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _FORWARD)
def test_trace_reports_forward_exact_sealed_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
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


def test_ids_and_after_pass_through_unchanged() -> None:
    fake = FakeBridge()
    ok = _client(fake).post(_R + "order_execution", json=_auth(type="sale_order", ids=[3, 1, 3], after="MTAx_MQ"))
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert sent["ids"] == [3, 1, 3]
    assert sent["after"] == "MTAx_MQ"


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_trace_reports_invalid_body_does_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    raw = json.dumps(body, allow_nan=True)
    denied = _client(fake).post(path, content=raw, headers={"content-type": "application/json"})
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _TraceBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        if path == "/v1/reports/doc_trace":
            nodes = [
                {"type": "sale_order", "id": 1, "code": "SO1", "date": "2026-09-01", "state": "verified", "level": 0},
                {"type": "dispatch", "id": 2, "code": "FH1", "date": "2026-09-02", "state": "closed", "level": 1},
            ]
            edge = {"from_type": "sale_order", "from_id": 1, "to_type": "dispatch", "to_id": 2, "lines": 3}
            return {"ok": True, "type": "sale_order", "id": 1, "nodes": nodes, "edges": [edge], "omitted": 2, "x": 1}
        row = {"id": 1, "line_id": 11, "qty": 5.5, "out_qty": 2.0, "received_amount": 12.34, "open": True, "y": 2}
        return {"ok": True, "type": "sale_order", "only_open": True, "items": [row], "next": "abc"}


def test_trace_responses_keep_bridge_fields() -> None:
    client = _client(_TraceBridge())
    trace = client.post(_R + "doc_trace", json=_auth(type="sale_order", id=1)).json()
    assert trace["edges"][0]["lines"] == 3
    assert trace["nodes"][1]["level"] == 1
    assert trace["omitted"] == 2
    assert trace["x"] == 1
    execution = client.post(_R + "order_execution", json=_auth(type="sale_order", only_open=True)).json()
    assert execution["items"][0]["received_amount"] == 12.34
    assert execution["items"][0]["y"] == 2
    assert execution["next"] == "abc"


@pytest.mark.parametrize(("status", "code"), ((404, "not_found"), (403, "no_permission")))
def test_trace_start_errors_pass_through(status: int, code: str) -> None:
    fake = FakeBridge()
    fake.error = ApiError(status, code, "起点单据")
    denied = _client(fake).post(_R + "doc_trace", json=_auth(type="sale_order", id=9))
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


@pytest.mark.parametrize("name", _NAMES)
def test_trace_reports_are_read_only_and_gated(name: str) -> None:
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
        ("order_execution", _auth(type="purchase_order"), "co:reports/order_execution:order_execution#purchase_order"),
        ("doc_trace", _auth(type="arrival", id=42), "co:reports/doc_trace:doc_trace#42"),
    ),
)
def test_trace_report_audit_names_the_report(name: str, body: dict, view: str, capsys) -> None:
    ok = _client().post(_R + name, json=body)
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    assert _audit_line(out, _R + name)["action"] == view


def test_trace_reports_openapi() -> None:
    spec = _spec()
    for name, summary, operation_id in (
        ("order_execution", "订单执行", "coReportOrderExecution"),
        ("doc_trace", "单据追溯", "coReportDocTrace"),
    ):
        operation = spec["paths"][_R + name]["post"]
        assert operation["summary"] == summary
        assert operation["operationId"] == operation_id
        assert operation["tags"] == ["报表"]
        assert "权限：只读" in operation["description"]
    assert set(_enum(spec, "ReportOrderExecIn", "type")) == {"sale_order", "purchase_order"}
    assert set(_enum(spec, "ReportDocTraceIn", "type")) == set(TRACE_KINDS)
    assert set(_enum(spec, "ReportDocTraceIn", "direction")) == {"both", "up", "down"}
