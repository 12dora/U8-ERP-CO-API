"""桥的写入限额（write_quota）与接口登录上限（u8_license_hold）在 API 层的映射。不访问网络。"""

from __future__ import annotations

import pytest
from u8co_api.co_bridge import to_api_error
from u8co_api.errors import default_hint, is_retryable

_QUOTA = "已超过账套写入限额"
_HOLD = "接口登录已达上限，请稍后重试"


def _quota(detail: object) -> dict:
    body: dict = {"ok": False, "code": "write_quota", "message": _QUOTA}
    if detail is not None:
        body["detail"] = detail
    return body


def test_write_quota_uses_bridge_wait_as_retry_after() -> None:
    error = to_api_error(429, _quota({"retry_after_seconds": 17}))
    assert (error.status, error.code, error.message) == (429, "write_quota", _QUOTA)
    assert error.retry_after == 17
    assert error.detail == {"retry_after_seconds": 17}
    assert is_retryable("write_quota")
    assert default_hint(429, "write_quota") is not None


@pytest.mark.parametrize("detail", (None, {}, {"retry_after_seconds": 0}, {"retry_after_seconds": "5"},
                                    {"retry_after_seconds": True}, {"retry_after_seconds": 90000}, []))
def test_write_quota_falls_back_to_default_wait(detail: object) -> None:
    assert to_api_error(429, _quota(detail)).retry_after == 60


def test_license_hold_is_retryable_503() -> None:
    error = to_api_error(503, {"ok": False, "code": "u8_license_hold", "message": _HOLD})
    assert (error.status, error.code, error.message, error.retry_after) == (503, "u8_license_hold", _HOLD, 30)
    assert is_retryable("u8_license_hold")


def test_other_codes_ignore_detail_wait() -> None:
    body = {"ok": False, "code": "busy", "message": "忙", "detail": {"retry_after_seconds": 99}}
    assert to_api_error(429, body).retry_after == 5
