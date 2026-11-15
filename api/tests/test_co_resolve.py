"""档案名称解析 /v1/co/archives/resolve：只读，转给桥 /v1/archives/resolve。"""

from __future__ import annotations

import typing

import pytest
from tests.support import base_claims
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from tests.test_co_update_close_gen import _spec
from u8co_api.co_models_ai import ResolveArchive
from u8co_api.co_models_arc_names import ReadArchiveName

_PATH = "/v1/co/archives/resolve"
_EXCLUDED = {
    "customer_address",
    "customer_inventory",
    "customer_bank",
    "vendor_bank",
    "customer_contact",
    "vendor_contact",
    "user_define",
    "exchange_rate",
    "fa_card",
    "equipment",  # 设备台账不进解析
}
_EXACT = {
    "archive": "customer",
    "q": "甲",
    "status": "exact",
    "match": {"code": "C900001", "name": "甲公司", "match": "abbr"},
    "candidates": [{"code": "C900001", "name": "甲公司", "match": "abbr", "abbr": "甲"}],
    "more": False,
}


class ResolveBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        if path == "/v1/archives/resolve":
            self.calls.append((path, dict(payload)))
            return {"ok": True, "results": [_EXACT]}
        return super().call(path, payload)


def _body(**extra) -> dict:
    return _auth(items=[{"archive": "customer", "q": "甲"}], **extra)


def test_resolve_forwards_items_and_returns_the_bridge_results() -> None:
    fake = ResolveBridge()
    ok = _client(fake).post(_PATH, json=_body(limit=3, include_disabled=True))
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"ok": True, "results": [_EXACT]}
    path, payload = fake.calls[0]
    assert path == "/v1/archives/resolve"
    assert payload["items"] == [{"archive": "customer", "q": "甲"}]
    assert payload["limit"] == 3
    assert payload["include_disabled"] is True


def test_resolve_defaults_and_trims_q() -> None:
    fake = ResolveBridge()
    ok = _client(fake).post(_PATH, json=_auth(items=[{"archive": "inventory", "q": "  示例存货X1 "}]))
    assert ok.status_code == 200, ok.text
    payload = fake.calls[0][1]
    assert payload["items"] == [{"archive": "inventory", "q": "示例存货X1"}]
    assert payload["limit"] == 5
    assert payload["include_disabled"] is False


def test_read_only_caller_may_resolve() -> None:
    fake = ResolveBridge()
    ok = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text


@pytest.mark.parametrize(
    ("body", "field"),
    [
        (_auth(items=[]), "items"),
        (_auth(items=[{"archive": "customer", "q": "a"}] * 21), "items"),
        (_auth(items=[{"archive": "customer_address", "q": "a"}]), "items.0.archive"),
        (_auth(items=[{"archive": "exchange_rate", "q": "a"}]), "items.0.archive"),
        (_auth(items=[{"archive": "fa_card", "q": "a"}]), "items.0.archive"),
        (_auth(items=[{"archive": "customer", "q": "   "}]), "items.0.q"),
        (_auth(items=[{"archive": "customer", "q": "a" * 101}]), "items.0.q"),
        (_auth(items=[{"archive": "customer", "q": "a", "x": 1}]), "items.0.x"),
        (_body(limit=0), "limit"),
        (_body(limit=21), "limit"),
        (_body(include_disabled="yes"), "include_disabled"),
    ],
)
def test_resolve_rejects_bad_requests_before_the_bridge(body: dict, field: str) -> None:
    fake = ResolveBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == field
    assert fake.calls == []


def test_resolve_archives_are_the_listable_single_key_kinds() -> None:
    assert set(typing.get_args(ResolveArchive)) == set(typing.get_args(ReadArchiveName)) - _EXCLUDED


def test_openapi_describes_resolve_as_a_read_route() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["operationId"] == "coArchiveResolve"
    assert operation["x-u8co-access"] == "read"
    assert "权限：只读" in operation["description"]
    assert "x-u8co-dry-run" not in operation
