"""archives/list extras: keys_only forwarding and the end_date / disabled response fields."""

from __future__ import annotations

import json

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec

_LIST = "/v1/co/archives/list"


@pytest.mark.parametrize("archive", ["customer", "person", "voucher_sign", "exchange_rate"])
def test_keys_only_forwards_to_bridge(archive: str) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(_LIST, json=_auth(archive=archive, keys_only=True, limit=500))
        assert response.status_code == 200, response.text
        _method, seen, raw = server.httpd.hits[-1]
    assert seen == "/u8co/v1/archives/list"
    sent = json.loads(raw)
    assert set(sent) == _sealed("archive", "keys_only", "limit")
    assert sent["keys_only"] is True


@pytest.mark.parametrize("value", ["true", 1, 0, "yes"])
def test_keys_only_must_be_boolean(value: object) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_LIST, json=_auth(archive="customer", keys_only=value))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _EndBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if payload.get("keys_only"):
            items = [{"code": "C001", "ufts": "25328063"}]
        else:
            ended = {"code": "C001", "name": "张三商行", "class_code": "01", "ufts": "25328063"}
            ended.update(end_date="2024-12-31", disabled=True)
            active = {"code": "C002", "name": "李四", "class_code": "01", "ufts": "25328070"}
            active.update(end_date=None, disabled=False)
            items = [ended, active]
        return {"ok": True, "archive": "customer", "items": items, "next": None, "watermark": "25328099"}


def test_list_keeps_end_date_and_disabled() -> None:
    client = _client(_EndBridge())
    listed = client.post(_LIST, json=_auth(archive="customer"))
    assert listed.status_code == 200, listed.text
    ended, active = listed.json()["items"]
    assert (ended["end_date"], ended["disabled"]) == ("2024-12-31", True)
    assert active["disabled"] is False
    assert "end_date" not in active


def test_keys_only_response_has_code_and_ufts_only() -> None:
    client = _client(_EndBridge())
    listed = client.post(_LIST, json=_auth(archive="customer", keys_only=True))
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == [{"code": "C001", "ufts": "25328063"}]


def test_openapi_documents_extras() -> None:
    schemas = _spec()["components"]["schemas"]
    assert schemas["ArcListIn"]["properties"]["keys_only"]
    item = schemas["ArcListItem"]["properties"]
    assert "end_date" in item
    assert "customer" in item["disabled"]["description"]
