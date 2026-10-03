"""Archive kinds unit_group, settle_style, rd_style, purchase_type, sale_type, district_class and aa_bank
become writable through EAI — forwarding of create/update/delete and the checks made before the bridge.
currency and voucher_sign are writable (see test_co_arc_gl_write); account stays read-only."""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_ARC = "/v1/co/archives/"
_CLASS_KINDS = ("unit_group", "settle_style", "rd_style", "purchase_type", "sale_type", "district_class", "aa_bank")
_CODES = {
    "unit_group": "G01",
    "settle_style": "ZZ9",
    "rd_style": "ZZ901",
    "purchase_type": "ZZ",
    "sale_type": "ZZ",
    "district_class": "Z" * 12,
    "aa_bank": "ZZ001",
}
_NEW = {
    "unit_group": {"name": "测试组", "type": 1},
    "settle_style": {"name": "测试结算", "flag": False},
    "rd_style": {"name": "测试收发", "rsflag": 1},
    "purchase_type": {"name": "测试采购", "rstype_code": "101"},
    "sale_type": {"name": "测试销售"},
    "district_class": {"name": "测试地区"},
    "aa_bank": {"name": "测试银行"},
}

_FORWARD = (
    *((_ARC + "create", _auth(archive=a, code=_CODES[a], fields=_NEW[a]), _sealed("archive", "code", "fields")) for a in _CLASS_KINDS),
    *(
        (_ARC + "update", _auth(archive=a, code=_CODES[a], fields={"name": "改名"}), _sealed("archive", "code", "fields"))
        for a in _CLASS_KINDS
    ),
    *((_ARC + "delete", _auth(archive=a, code=_CODES[a]), _sealed("archive", "code")) for a in _CLASS_KINDS),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_class_archive_writes_forward(path: str, body: dict, keys: set[str]) -> None:
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
    # 编码长度按表列：结算方式 3、收发类别 5、采购 / 销售类型 2、地区分类 12、银行档案 5。
    (_ARC + "create", _auth(archive="settle_style", code="1234", fields={"name": "x"})),
    (_ARC + "delete", _auth(archive="rd_style", code="123456")),
    (_ARC + "delete", _auth(archive="purchase_type", code="123")),
    (_ARC + "update", _auth(archive="district_class", code="9" * 13, fields={"name": "x"})),
    (_ARC + "delete", _auth(archive="aa_bank", code="123456")),
    # 不能在 fields 里写 code。
    (_ARC + "create", _auth(archive="unit_group", code="ZZ01", fields={"name": "x", "type": 0, "CODE": "ZZ02"})),
    (_ARC + "update", _auth(archive="aa_bank", code="ZZ001", fields={"code": "ZZ002"})),
    # 仍然只读的档案。
    (_ARC + "create", _auth(archive="trade_class", code="09", fields={"name": "x"})),
    # 总账档案的格式（见 test_co_arc_gl_write）：凭证类别只能改 type_name、科目仍只读、
    # 币种符号最长 4 且不能修改、币种名称最长 8。
    (_ARC + "update", _auth(archive="voucher_sign", code="记", fields={"name": "x"})),
    (_ARC + "delete", _auth(archive="account", code="1001")),
    (_ARC + "create", _auth(archive="currency", code="测试币", fields={"code": "ZZ123"})),
    (_ARC + "update", _auth(archive="currency", code="测试币", fields={"code": "ZZ1"})),
    (_ARC + "delete", _auth(archive="currency", code="九" * 9)),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_class_archive_rejects_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_openapi_lists_class_kinds_for_writes() -> None:
    spec = _spec()
    for schema in ("ArcCreateIn", "ArcUpdateIn", "ArcKeyIn"):
        kinds = set(_enum(spec, schema, "archive"))
        assert set(_CLASS_KINDS) <= kinds, schema
        assert not {"trade_class", "customer_address"} & kinds, schema
