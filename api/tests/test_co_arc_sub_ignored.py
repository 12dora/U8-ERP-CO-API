"""502 u8_fields_ignored: the archive was saved but U8 did not write some Inventory_Sub columns; the bridge detail
(fields -> sent / stored) is passed through, unlike other 5xx errors."""

from __future__ import annotations

from u8co_api.co_bridge import to_api_error

_DETAIL = {"fields": {"bbatchproperty1": {"sent": "1", "stored": "0"}}}


def test_fields_ignored_keeps_status_code_and_detail() -> None:
    error = to_api_error(502, {"ok": False, "code": "u8_fields_ignored", "message": "档案已保存，但 U8 未写入以下字段：bbatchproperty1", "detail": _DETAIL})
    assert (error.status, error.code) == (502, "u8_fields_ignored")
    assert error.detail == _DETAIL
    assert "bbatchproperty1" in error.message


def test_other_server_errors_still_drop_detail() -> None:
    error = to_api_error(502, {"ok": False, "code": "bad_response", "message": "x", "detail": _DETAIL})
    assert error.detail is None
