"""Response caps of the CO bridge client: 8 MiB for success, 64 KiB for errors."""

from __future__ import annotations

import json

import pytest
from tests.test_co_client import SECRET, _fields, _Running
from u8co_api.co_client import CoBridge
from u8co_api.errors import ApiError

_MIB8 = 8 * 1024 * 1024


def test_large_success_up_to_8_mib_is_parsed() -> None:
    pad = "x" * (_MIB8 - 64)
    payload = json.dumps({"ok": True, "pad": pad}).encode()
    assert 65536 < len(payload) <= _MIB8
    with _Running(200, payload) as bridge:
        body = CoBridge(bridge.base_url, SECRET, timeout=5).call("/v1/vouchers/list", _fields())
    assert body["ok"] is True
    assert len(body["pad"]) == len(pad)


def test_error_over_64_kib_is_bad_response_even_as_json() -> None:
    payload = json.dumps({"code": "u8_rejected", "message": "x" * 70000}).encode()
    with _Running(409, payload) as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).call("/v1/login-check", _fields())
    assert caught.value.status == 502
    assert caught.value.code == "bad_response"


def test_small_json_error_keeps_its_code() -> None:
    payload = json.dumps({"code": "u8_rejected", "message": "单据已审核"}).encode()
    with _Running(409, payload) as bridge:
        with pytest.raises(ApiError) as caught:
            CoBridge(bridge.base_url, SECRET, timeout=5).call("/v1/login-check", _fields())
    assert caught.value.status == 409
    assert caught.value.message == "单据已审核"
