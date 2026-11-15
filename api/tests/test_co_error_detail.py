"""桥错误体里的结构化 detail：4xx（401 除外）原样透传到 error.detail，其余丢掉。假桥，不访问网络。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client, _login
from tests.test_co_update_close_gen import _spec
from u8co_api.co_bridge import to_api_error
from u8co_api.co_models import ErrorBody
from u8co_api.co_routes_ai import translate
from u8co_api.errors import ApiError
from u8co_api.http import api_error_detail

_UNCOSTED = {
    "uncosted": [{"wh": "01", "inv": "A001", "batch": ""}, {"wh": "02", "inv": "B002", "batch": "L1"}],
    "uncosted_total": 2,
}
_BODY = {"ok": False, "code": "state_mismatch", "message": "存货核算 2026 年 9 期：有存货算不出入库成本", "detail": _UNCOSTED}


def _wire(error: ApiError) -> dict:
    fake = FakeBridge()
    fake.error = error
    response = _client(fake).post("/v1/co/login-check", json=_login())
    assert response.status_code == error.status
    return response.json()["error"]


def test_bridge_detail_reaches_the_api_error_body() -> None:
    error = to_api_error(409, _BODY)
    assert error.detail == _UNCOSTED
    wire = _wire(error)
    assert wire["detail"] == _UNCOSTED
    assert (wire["code"], wire["retryable"]) == ("state_mismatch", False)
    ErrorBody.model_validate({"error": wire})


@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_detail_kept_on_other_4xx(status: int) -> None:
    error = to_api_error(status, {"ok": False, "message": "m", "detail": {"k": 1}})
    assert error.detail == {"k": 1}


@pytest.mark.parametrize(("status", "code"), [(500, "internal"), (503, "busy_timeout"), (504, "outcome_unknown")])
def test_detail_dropped_outside_4xx(status: int, code: str) -> None:
    error = to_api_error(status, {"ok": False, "code": code, "message": "m", "detail": {"k": 1}})
    assert error.detail is None
    assert "detail" not in _wire(error)


def test_detail_dropped_on_bridge_401() -> None:
    assert to_api_error(401, {"ok": False, "code": "unauthorized", "detail": {"k": 1}}).detail is None


@pytest.mark.parametrize("detail", [None, {}, [1, 2], "文字", 5, {"x": "y" * 40000}, {"n": float("nan")}])
def test_invalid_or_oversized_detail_dropped(detail: object) -> None:
    error = to_api_error(409, {"ok": False, "code": "u8_rejected", "message": "m", "detail": detail})
    assert error.detail is None
    assert "detail" not in _wire(error)


def test_api_error_detail_ignores_detail_on_5xx() -> None:
    error = ApiError(502, "internal", "内部错误")
    error.detail = {"k": 1}
    assert "detail" not in api_error_detail(error)


def test_idempotency_lookup_translates_detail_too() -> None:
    stored = {"ok": True, "found": True, "state": "error", "status": 409, "response": _BODY}
    out = translate(stored)
    assert out["response"]["error"]["detail"] == _UNCOSTED


def test_openapi_error_model_documents_detail() -> None:
    schemas = _spec()["components"]["schemas"]
    assert "detail" in schemas["ErrorDetail"]["properties"]
