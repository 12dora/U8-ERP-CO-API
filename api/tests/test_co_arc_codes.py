"""Read-only archives: forwarding, per-kind code lengths, two-part codes and changed_since rules."""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_ARC = "/v1/co/archives/"
_SINGLE = ("position", "rd_style", "purchase_type", "sale_type", "district_class", "trade_class", "aa_bank")
_PAIR = {
    "customer_address": "C001:01",
    "user_define": "1002:快递",
    "customer_inventory": "C001:INV0010",
}
_KINDS = (*_SINGLE, *_PAIR)
# 以下档案可写（EAI），见 test_co_arc_item_write / test_co_arc_class_write。
_WRITABLE_KINDS = ("position", "user_define", "customer_inventory", "rd_style", "purchase_type", "sale_type", "district_class", "aa_bank")
_RO_KINDS = tuple(a for a in _KINDS if a not in _WRITABLE_KINDS)


def _code(archive: str) -> str:
    return _PAIR.get(archive, "01")


_FORWARD = (
    *((_ARC + "get", _auth(archive=a, code=_code(a)), _sealed("archive", "code")) for a in _KINDS),
    (_ARC + "get", _auth(archive="position", code="9" * 20), _sealed("archive", "code")),
    (_ARC + "get", _auth(archive="user_define", code="1002:" + "x" * 400), _sealed("archive", "code")),
    (_ARC + "get", _auth(archive="user_define", code="1002:a:b"), _sealed("archive", "code")),
    (_ARC + "get", _auth(archive="customer_inventory", code="C" * 20 + ":" + "9" * 60), _sealed("archive", "code")),
    (
        _ARC + "list",
        _auth(archive="customer_address", code_prefix="C001:", after="C001:01", limit=5),
        _sealed("archive", "code_prefix", "after", "limit"),
    ),
    (_ARC + "list", _auth(archive="trade_class", changed_since="25131155"), _sealed("archive", "changed_since")),
    (_ARC + "list", _auth(archive="customer_inventory", changed_since="7"), _sealed("archive", "changed_since")),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_read_archives_forward(path: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        _method, seen, raw = server.httpd.hits[-1]
    assert seen == "/u8co/v1" + path.removeprefix("/v1/co")
    sent = json.loads(raw)
    assert set(sent) == write_keys(path, keys)
    assert (sent["archive"], sent.get("code")) == (body["archive"], body.get("code"))


_REJECTS = (
    *((_ARC + "delete", _auth(archive=a, code=_code(a))) for a in _RO_KINDS),
    *((_ARC + op, _auth(archive=a, code=_code(a), fields={"name": "x"})) for a in _RO_KINDS for op in ("create", "update")),
    (_ARC + "get", _auth(archive="position", code="9" * 21)),
    (_ARC + "get", _auth(archive="rd_style", code="123456")),
    (_ARC + "get", _auth(archive="purchase_type", code="123")),
    (_ARC + "get", _auth(archive="sale_type", code="123")),
    (_ARC + "get", _auth(archive="district_class", code="9" * 13)),
    (_ARC + "get", _auth(archive="trade_class", code="9" * 13)),
    (_ARC + "get", _auth(archive="aa_bank", code="123456")),
    (_ARC + "get", _auth(archive="customer_address", code="C001")),
    (_ARC + "get", _auth(archive="customer_address", code=":01")),
    (_ARC + "get", _auth(archive="customer_address", code="C001:")),
    (_ARC + "get", _auth(archive="customer_address", code="C001 :01")),
    (_ARC + "get", _auth(archive="customer_address", code="C001: 01")),
    (_ARC + "get", _auth(archive="customer_address", code="C" * 21 + ":01")),
    (_ARC + "get", _auth(archive="customer_address", code="C001:" + "0" * 31)),
    (_ARC + "get", _auth(archive="user_define", code="1" * 11 + ":x")),
    (_ARC + "get", _auth(archive="user_define", code="1002:" + "x" * 401)),
    (_ARC + "get", _auth(archive="customer_inventory", code="C001:" + "9" * 61)),
    (_ARC + "list", _auth(archive="customer_inventory", after="C001")),
    (_ARC + "list", _auth(archive="customer_address", changed_since="1")),
    (_ARC + "list", _auth(archive="position", code_prefix="9" * 21)),
    (_ARC + "list", _auth(archive="user_define", code_prefix="x" * 412)),
    (_ARC + "list", _auth(archive="sale_type", project_class="98")),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_read_archive_rejects_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _PairBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        item = {"code": "C001:01", "name": "收货地址", "class_code": "C001", "ufts": None}
        return {"ok": True, "archive": "customer_address", "items": [item], "next": "C001:01", "watermark": None}


def test_pair_list_keeps_class_code_and_next() -> None:
    client = _client(_PairBridge())
    listed = client.post(_ARC + "list", json=_auth(archive="customer_address", limit=1))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert (body["items"][0]["code"], body["items"][0]["class_code"]) == ("C001:01", "C001")
    assert body["next"] == "C001:01"


def test_openapi_lists_read_kinds_for_reads_only() -> None:
    spec = _spec()
    for kind in _KINDS:
        assert kind in _enum(spec, "ArcGetIn", "archive")
        assert kind in _enum(spec, "ArcListIn", "archive")
        writable = kind in _WRITABLE_KINDS
        assert (kind in _enum(spec, "ArcKeyIn", "archive")) is writable
        assert (kind in _enum(spec, "ArcCreateIn", "archive")) is writable
