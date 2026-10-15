"""库龄分析 /v1/co/reports/stock_age：请求模型的取值校验、转发的字段、提前拒绝、权限和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.support import base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS, READ
from u8co_api.co_models_reports_stock_age import STOCK_AGE_DOC, STOCK_AGE_SUMMARY, StockAgeIn, StockAgeOut

_PATH = "/v1/co/reports/stock_age"
_LOGIN = {"acc": "801", "operator": "op001", "password": "x"}
_FULL = {
    "idle_days": 365,
    "buckets": [7, 14],
    "wh": ["01", "02"],
    "inv": ["9909001"],
    "inv_class": "0101",
    "by_wh": False,
    "sort": "age",
    "limit": 2000,
}


def _model(**extra) -> StockAgeIn:
    return StockAgeIn(**_LOGIN, **extra)


def test_model_defaults_are_left_to_the_bridge() -> None:
    model = _model()
    for field in _FULL:
        assert getattr(model, field) is None


def test_model_accepts_every_field() -> None:
    model = _model(**_FULL)
    assert model.buckets == [7, 14]
    assert model.sort == "age"
    assert model.by_wh is False


@pytest.mark.parametrize(
    "extra",
    (
        {"idle_days": 0},
        {"idle_days": 3651},
        {"idle_days": "180"},
        {"idle_days": True},
        {"buckets": []},
        {"buckets": [1, 2, 3, 4, 5, 6, 7]},
        {"buckets": [30, 30]},
        {"buckets": [90, 30]},
        {"buckets": [0, 30]},
        {"buckets": [3651]},
        {"buckets": ["30"]},
        {"buckets": 30},
        {"wh": []},
        {"wh": ["1"] * 21},
        {"wh": ["01"] * 1 + [" "]},
        {"wh": "01"},
        {"inv": []},
        {"inv": ["a\tb"]},
        {"inv_class": "01%"},
        {"by_wh": "true"},
        {"by_wh": 1},
        {"sort": "qty"},
        {"limit": 0},
        {"limit": 2001},
        {"date_from": "2026-09-01"},
        {"after": "x"},
        {"include_unverified": True},
    ),
)
def test_model_rejects_bad_values_and_unknown_fields(extra: dict) -> None:
    with pytest.raises(ValidationError):
        _model(**extra)


def test_model_limits_are_inclusive() -> None:
    assert _model(idle_days=1, limit=1, buckets=[1]).idle_days == 1
    assert _model(idle_days=3650, limit=2000, buckets=[1, 2, 3, 4, 5, 3650]).buckets[-1] == 3650
    assert len(_model(wh=["1"] * 20, inv=["2"] * 20).wh) == 20


def test_out_keeps_unknown_bridge_fields() -> None:
    out = StockAgeOut(ok=True, items=[{"inv_code": "9909001", "x": 1}], total={"items": 1, "amount": None}, extra=3)
    dumped = out.model_dump()
    assert dumped["items"][0]["x"] == 1
    assert dumped["extra"] == 3


def test_doc_says_masked_amounts_are_not_ranked() -> None:
    assert "库龄" in STOCK_AGE_DOC
    assert "按库龄" in STOCK_AGE_DOC


def test_doc_constants_are_chinese_text() -> None:
    assert STOCK_AGE_SUMMARY == "库龄分析"
    assert "库龄" in STOCK_AGE_DOC
    assert "呆滞" in STOCK_AGE_DOC


def test_stock_age_forwards_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        for body, keys in (
            (_auth(), _sealed()),
            (_auth(**_FULL), _sealed(*_FULL)),
        ):
            response = client.post(_PATH, json=body)
            assert response.status_code == 200, response.text
            method, seen, raw = server.httpd.hits[-1]
            assert method == "POST"
            assert seen == "/u8co/v1/reports/stock_age"
            assert set(json.loads(raw)) == keys
            assert _SECRET.encode() not in raw


@pytest.mark.parametrize(
    "extra",
    ({"idle_days": 0}, {"buckets": [30, 30]}, {"sort": "qty"}, {"limit": 2001}, {"date_from": "2026-09-01"}),
)
def test_stock_age_invalid_body_does_not_call(extra: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=_auth(**extra))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _AgeBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        row = {"inv_code": "9909001", "qty": 10.0, "last_in": None, "age_days": None, "idle": True, "amount": None, "x": 1}
        return {
            "ok": True,
            "as_of": "2026-09-27",
            "cost_month": "2026-08",
            "items": [row],
            "buckets": [{"from_days": 0, "to_days": 30, "items": 0, "amount": 0.0}],
            "total": {"items": 1, "amount": None},
            "masked_fields": ["amount"],
        }


def test_stock_age_response_keeps_bridge_fields() -> None:
    body = _client(_AgeBridge()).post(_PATH, json=_auth()).json()
    assert body["cost_month"] == "2026-08"
    assert body["items"][0]["x"] == 1
    assert body["items"][0]["idle"] is True
    assert body["buckets"][0]["to_days"] == 30
    assert body["masked_fields"] == ["amount"]


def test_stock_age_is_read_only_and_gated() -> None:
    assert ACCESS["co:reports/stock_age"] == READ
    body = _auth()
    fake = FakeBridge()
    assert _client(fake, co_enabled=False).post(_PATH, json=body).status_code == 404
    assert _client(fake, claims=base_claims()).post(_PATH, json=body).status_code == 403
    outside = _client(fake).post(_PATH, json=dict(body, acc="001"))
    assert outside.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=body)
    assert reader.status_code == 200, reader.text
    assert fake.calls[-1][0] == "/v1/reports/stock_age"


def test_stock_age_openapi_entry() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == STOCK_AGE_SUMMARY
    assert operation["operationId"] == "coReportStockAge"
    assert operation["tags"] == ["报表"]
    assert "权限：只读" in operation["description"]
