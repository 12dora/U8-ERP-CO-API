"""Archive kinds position, unit, user_define and customer_inventory become writable through EAI —
forwarding of create/update/delete and the checks made before the bridge. user_define and
customer_inventory have no update in U8, so the API refuses those updates before the bridge."""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_ARC = "/v1/co/archives/"
_ITEM_KINDS = ("position", "unit", "user_define", "customer_inventory")
_UPD7 = ("position", "unit")
_CODES = {
    "position": "ZZ01",
    "unit": "ZZ260928",
    "user_define": "1002:" + "快" * 400,
    "customer_inventory": "C" * 20 + ":" + "9" * 60,
}
_NEW = {
    "position": {"name": "测试货位", "warehouse_code": "01"},
    "unit": {"name": "测试单位", "group_code": "02", "changerate": 25},
    "user_define": {"alias": "KD"},
    "customer_inventory": {"ccusinvname": "客户叫法", "ccusinvcode": "X-1"},
}

_FORWARD = (
    *((_ARC + "create", _auth(archive=a, code=_CODES[a], fields=_NEW[a]), _sealed("archive", "code", "fields")) for a in _ITEM_KINDS),
    *((_ARC + "update", _auth(archive=a, code=_CODES[a], fields={"barcode": "B1"}), _sealed("archive", "code", "fields")) for a in _UPD7),
    *((_ARC + "delete", _auth(archive=a, code=_CODES[a]), _sealed("archive", "code")) for a in _ITEM_KINDS),
    (
        _ARC + "create",
        _auth(archive="unit", code="ZZ02", fields=_NEW["unit"], template="0202"),
        _sealed("archive", "code", "fields", "template"),
    ),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_item_archive_writes_forward(path: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        _method, seen, raw = server.httpd.hits[-1]
    assert seen == "/u8co/v1" + path.removeprefix("/v1/co")
    sent = json.loads(raw)
    assert set(sent) == write_keys(path, keys)
    assert (sent["archive"], sent["code"]) == (body["archive"], body["code"])
    if "fields" in body:
        assert sent["fields"] == body["fields"]


_REJECTS = (
    # 编码长度、两段格式与读取相同。
    (_ARC + "delete", _auth(archive="position", code="9" * 21)),
    (_ARC + "delete", _auth(archive="unit", code="9" * 36)),
    (_ARC + "delete", _auth(archive="user_define", code="1002")),
    (_ARC + "delete", _auth(archive="customer_inventory", code="C001:" + "9" * 61)),
    (_ARC + "create", _auth(archive="user_define", code="1" * 11 + ":x", fields={"alias": "x"})),
    (_ARC + "delete", _auth(archive="customer_inventory", code="C001: 01")),
    # U8 不提供修改的两类档案：API 按参数无效拒绝，不转给桥。
    (_ARC + "update", _auth(archive="user_define", code="1002:快递", fields={"alias": "x"})),
    (_ARC + "update", _auth(archive="customer_inventory", code="C001:01", fields={"ccusinvname": "x"})),
    # 两列主键的档案不收 template。
    (_ARC + "create", _auth(archive="user_define", code="1002:快递", fields={"alias": "x"}, template="1002:顺丰")),
    (
        _ARC + "create",
        _auth(archive="customer_inventory", code="C001:01", fields=_NEW["customer_inventory"], template="C001:02"),
    ),
    # 编码不能放进 fields；修改至少一个字段。
    (_ARC + "update", _auth(archive="position", code="ZZ01", fields={"code": "ZZ02"})),
    (_ARC + "update", _auth(archive="unit", code="ZZ01", fields={"name": None})),
    # 仍然只读的档案。
    (_ARC + "create", _auth(archive="trade_class", code="09", fields={"name": "x"})),
    (_ARC + "delete", _auth(archive="customer_address", code="C001:01")),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_item_archive_rejects_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_openapi_lists_item_kinds_for_writes() -> None:
    spec = _spec()
    for schema in ("ArcCreateIn", "ArcKeyIn"):
        assert set(_ITEM_KINDS) <= set(_enum(spec, schema, "archive"))
        assert "trade_class" not in _enum(spec, schema, "archive")
    update = set(_enum(spec, "ArcUpdateIn", "archive"))
    assert set(_UPD7) <= update
    assert not {"user_define", "customer_inventory", "trade_class"} & update
