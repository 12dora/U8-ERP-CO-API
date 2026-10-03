"""结构化错误：retryable、field、hint，以及请求校验错误带出字段路径。假桥，不访问网络。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client, _login
from u8co_api.co_bridge import to_api_error, transport_error
from u8co_api.co_models import ErrorBody, ErrorDetail
from u8co_api.errors import (
    BRIDGE_NOT_FOUND_HINT,
    DEFAULT_HINTS,
    RETRYABLE_CODES,
    ApiError,
    bad_request,
    default_hint,
    is_retryable,
)
from u8co_api.http import validation_field

_RETRYABLE = (
    "busy",
    "busy_timeout",
    "stopping",
    "u8_license_full",
    "rate_limited",
    "unavailable",
    "store_unavailable",
    "ia_timeout",
    "write_policy_unavailable",
    "write_frozen",
    "write_window",
    "write_quota",
    "u8_license_hold",
)
_NOT_RETRYABLE = (
    "outcome_unknown",
    "bad_request",
    "state_mismatch",
    "u8_rejected",
    "login_failed",
    "idempotency_mismatch",
    "internal",
    "com_unavailable",
    "u8_unavailable",
    "no_permission",
    "not_found",
)
_LOAD = "/v1/co/vouchers/load"
_CREATE = "/v1/co/vouchers/create"


def _error(fake: FakeBridge, error: ApiError) -> dict:
    fake.error = error
    response = _client(fake).post("/v1/co/login-check", json=_login())
    assert response.status_code == error.status
    return response.json()["error"]


# ---- retryable ----


def test_retryable_table_is_exact() -> None:
    assert frozenset(_RETRYABLE) == RETRYABLE_CODES


@pytest.mark.parametrize("code", _RETRYABLE)
def test_retryable_codes(code: str) -> None:
    assert is_retryable(code) is True


@pytest.mark.parametrize("code", _NOT_RETRYABLE)
def test_other_codes_are_not_retryable(code: str) -> None:
    assert is_retryable(code) is False


def test_retryable_always_present_on_the_wire() -> None:
    fake = FakeBridge()
    assert _error(fake, ApiError(409, "state_mismatch", "x"))["retryable"] is False
    assert _error(fake, to_api_error(429, {"ok": False, "code": "busy", "message": "排队已满"}))["retryable"] is True
    unknown = _error(fake, ApiError(504, "outcome_unknown", "结果未知"))
    assert unknown["retryable"] is False


def test_transport_errors_retryable_by_code() -> None:
    fake = FakeBridge()
    assert _error(fake, transport_error(OSError("refused")))["retryable"] is True


def test_unauthorized_body_has_retryable_but_no_hint() -> None:
    denied = _client(auth=False).post("/v1/co/login-check", json=_login())
    assert denied.status_code == 401
    error = denied.json()["error"]
    assert error["retryable"] is False
    assert "hint" not in error
    assert "field" not in error


# ---- hint ----


@pytest.mark.parametrize("code", sorted(DEFAULT_HINTS))
def test_default_hints_are_short(code: str) -> None:
    hint = default_hint(409, code)
    assert hint
    assert len(hint) <= 60


def test_default_hint_values() -> None:
    assert default_hint(503, "busy_timeout") == "稍后重试（见 Retry-After）"
    assert default_hint(503, "u8_license_full") == "U8 许可点数已满，60 秒后重试"
    assert default_hint(429, "rate_limited") == "降低调用频率，按 Retry-After 重试"
    assert "不要直接重试" in default_hint(504, "outcome_unknown")
    assert default_hint(403, "no_permission") == default_hint(403, "forbidden")
    assert default_hint(409, "u8_rejected") is None


def test_no_hint_on_401_or_500() -> None:
    assert default_hint(401, "unavailable") is None
    assert default_hint(500, "busy") is None


def test_default_hint_on_the_wire() -> None:
    error = _error(FakeBridge(), ApiError(504, "outcome_unknown", "结果未知"))
    assert error["hint"] == DEFAULT_HINTS["outcome_unknown"]


def test_bridge_hint_wins_over_default() -> None:
    body = {"ok": False, "code": "state_mismatch", "message": "已审核", "hint": "先弃审再修改"}
    error = _error(FakeBridge(), to_api_error(409, body))
    assert error["hint"] == "先弃审再修改"


def test_not_found_hint_only_for_bridge_404() -> None:
    bridged = to_api_error(404, {"ok": False, "code": "not_found", "message": "单据不存在"})
    assert bridged.hint == BRIDGE_NOT_FOUND_HINT
    assert _error(FakeBridge(), bridged)["hint"] == BRIDGE_NOT_FOUND_HINT
    assert to_api_error(404, {"ok": False, "code": "not_found", "message": "m", "hint": "换个编码"}).hint == "换个编码"
    assert default_hint(404, "not_found") is None
    unknown = _client(FakeBridge()).post("/v1/co/no-such-route", json=_login())
    assert unknown.status_code == 404
    assert "hint" not in unknown.json()["error"]
    disabled = _client(FakeBridge(), co_enabled=False).post("/v1/co/login-check", json=_login())
    assert disabled.status_code == 404
    assert "hint" not in disabled.json()["error"]


def test_no_hint_without_bridge_hint_or_default() -> None:
    error = _error(FakeBridge(), to_api_error(409, {"ok": False, "code": "u8_rejected", "message": "U8 拒绝"}))
    assert "hint" not in error
    assert "field" not in error


@pytest.mark.parametrize("hint", (123, "", "   ", "x" * 301, "带\n换行", None, ["a"]))
def test_invalid_bridge_hint_dropped(hint: object) -> None:
    error = to_api_error(409, {"ok": False, "code": "u8_rejected", "message": "m", "hint": hint})
    assert error.hint is None


def test_bridge_hint_trimmed_and_300_allowed() -> None:
    assert to_api_error(409, {"code": "u8_rejected", "message": "m", "hint": " 改数量 "}).hint == "改数量"
    assert to_api_error(409, {"code": "u8_rejected", "message": "m", "hint": "x" * 300}).hint == "x" * 300


def test_bridge_hint_ignored_on_bridge_500_and_401() -> None:
    internal = to_api_error(500, {"ok": False, "code": "internal", "message": "内部错误", "hint": "不该出现"})
    assert internal.status == 502
    assert internal.hint is None
    auth = to_api_error(401, {"ok": False, "code": "unauthorized", "hint": "不该出现"})
    assert auth.hint is None


# ---- field（桥） ----


@pytest.mark.parametrize("field", ("lines", "head.ccuscode", "lines.2.iquantity", "items.0.archive", "a-b_c.9"))
def test_bridge_field_copied_on_400(field: str) -> None:
    error = to_api_error(400, {"ok": False, "code": "bad_request", "message": "缺少字段", "field": field})
    assert error.field == field


@pytest.mark.parametrize("field", ("", "x" * 201, "lines[0]", "head.客户", "a b", 7, None, {"x": 1}))
def test_invalid_bridge_field_dropped(field: object) -> None:
    error = to_api_error(400, {"ok": False, "code": "bad_request", "message": "m", "field": field})
    assert error.field is None


def test_bridge_field_200_chars_allowed() -> None:
    error = to_api_error(400, {"ok": False, "code": "bad_request", "message": "m", "field": "a" * 200})
    assert error.field == "a" * 200


def test_bridge_field_only_for_400() -> None:
    error = to_api_error(409, {"ok": False, "code": "u8_rejected", "message": "m", "field": "lines"})
    assert error.field is None


def test_bridge_field_and_hint_on_the_wire() -> None:
    body = {"ok": False, "code": "bad_request", "message": "缺少字段 lines", "field": "lines", "hint": "至少一行"}
    error = _error(FakeBridge(), to_api_error(400, body))
    assert error == {
        "code": "bad_request",
        "message": "缺少字段 lines",
        "retryable": False,
        "field": "lines",
        "hint": "至少一行",
    }


def test_api_error_field_rendered_only_for_400() -> None:
    fake = FakeBridge()
    assert _error(fake, bad_request("fields 无效", field="fields"))["field"] == "fields"
    assert "field" not in _error(fake, ApiError(409, "state_mismatch", "m", field="lines"))


def test_error_models_accept_the_wire_shape() -> None:
    ErrorBody(error=ErrorDetail(code="busy", message="m", retryable=True, hint="h"))
    ErrorBody.model_validate({"error": {"code": "bad_request", "message": "m", "retryable": False, "field": "lines"}})
    schema = ErrorDetail.model_json_schema()
    assert "retryable" in schema["required"]
    assert {"field", "hint"} <= set(schema["properties"])
    assert "field" not in schema["required"]


# ---- 请求校验 ----


@pytest.mark.parametrize(
    ("loc", "field"),
    (
        (("body", "id"), "id"),
        (("body", "lines", 2, "iquantity"), "lines.2.iquantity"),
        (("body",), None),
        ((), None),
        (("query", "compact"), "compact"),
        (("query", "fields"), "fields"),
        (("query", "other"), "query.other"),
        (("body", "lines", 0, "function-after[check(), dict[str,str]]"), "lines.0"),
        (("body", "head", "客户"), "head"),
        (("body", "x" * 201), None),
        (("body", True), None),
    ),
)
def test_validation_field_from_loc(loc: tuple, field: str | None) -> None:
    assert validation_field([{"loc": loc}]) == field


def test_validation_field_trims_union_member_tags() -> None:
    loc = ("body", "lines", 0, "cInvCode")
    errors = [{"loc": (*loc, tag)} for tag in ("str", "int", "float", "bool")]
    assert validation_field(errors) == "lines.0.cInvCode"


def test_validation_field_keeps_real_date_field() -> None:
    assert validation_field([{"loc": ("body", "date")}]) == "date"
    assert validation_field([{"loc": ("body", "date")}, {"loc": ("body", "year")}]) == "date"


def test_validation_field_bad_input() -> None:
    assert validation_field([]) is None
    assert validation_field(["x"]) is None
    assert validation_field([{"loc": "body.id"}]) is None


def test_request_validation_names_the_field() -> None:
    fake = FakeBridge()
    client = _client(fake)
    response = client.post(_LOAD, json=_login(type="sale_order", id=0))
    assert response.status_code == 400
    assert response.json()["error"] == {
        "code": "bad_request",
        "message": "请求参数无效：id",
        "retryable": False,
        "field": "id",
    }
    assert fake.calls == []


def test_request_validation_unknown_field() -> None:
    body = _login(year="2026", date="2026-09-27")
    body["note"] = "x"
    response = _client().post("/v1/co/login-check", json=body)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["field"] == "note"
    assert error["message"] == "请求参数无效：note"


def test_request_validation_nested_line_index() -> None:
    body = _login(type="sale_order", head={"cCusCode": "C001"}, lines=[{"cInvCode": "A"}, {"cInvCode": {"x": 1}}])
    response = _client().post(_CREATE, json=body)
    assert response.status_code == 400
    assert response.json()["error"]["field"] == "lines.1.cInvCode"


def test_request_validation_without_field_keeps_message() -> None:
    response = _client().post("/v1/co/login-check", content=b"{not json", headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "bad_request"
    assert error == {"code": "bad_request", "message": "请求参数无效", "retryable": False}


def test_json_invalid_position_is_not_a_field() -> None:
    assert validation_field([{"type": "json_invalid", "loc": ("body", 1)}]) is None
