"""客户、供应商的银行账户、客户联系人（customer_contact）；供应商联系人（vendor_contact）也可写。

两段编码 <客户或供应商编码>:<账号或联系人编码>，一次写一行；联系人新增的编码写成 <客户或供应商编码>:（U8 自动编号）。
转发到桥的请求体，以及在桥之前就拒绝的格式错误。
"""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_ARC = "/v1/co/archives/"
_KINDS = ("customer_bank", "vendor_bank", "customer_contact", "vendor_contact")
_ALL = _KINDS
_CODES = {
    "customer_bank": "ZZC01:6222000000000001",
    "vendor_bank": "ZZV01:6222000000000002",
    "customer_contact": "ZZC01:ZZL01",
    "vendor_contact": "ZZV01:ZZL02",
}
_NEW = {
    "customer_bank": {"branch": "测试银行某支行", "account_name": "测试户名", "default": True},
    "vendor_bank": {"branch": "测试银行某支行", "bank_code": "00002"},
    "customer_contact": {"name": "张三", "sex": "男", "mobile": "13800000000"},
    "vendor_contact": {"name": "李四", "mobile": "13800000000", "be_main_linker": 0},
}
_CREATE_CODES = {**_CODES, "customer_contact": "ZZC01:", "vendor_contact": "ZZV01:"}
_EDIT = {
    "customer_bank": {"default": 1},
    "vendor_bank": {"account_name": "改名"},
    "customer_contact": {
        "email": "a@example.com",
        "marriage": "未婚",
        "birthday": "1990-01-02",
        "be_main_linker": False,
    },
    "vendor_contact": {"memo": "改", "sex": "女", "self_define1": "x"},
}

_FORWARD = (
    *(
        (_ARC + "create", _auth(archive=a, code=_CREATE_CODES[a], fields=_NEW[a]), _sealed("archive", "code", "fields"))
        for a in _KINDS
    ),
    *(
        (_ARC + "update", _auth(archive=a, code=_CODES[a], fields=_EDIT[a]), _sealed("archive", "code", "fields"))
        for a in _KINDS
    ),
    *((_ARC + "delete", _auth(archive=a, code=_CODES[a]), _sealed("archive", "code")) for a in _KINDS),
    *((_ARC + "get", _auth(archive=a, code=_CODES[a]), _sealed("archive", "code")) for a in _ALL),
    (_ARC + "list", _auth(archive="vendor_contact", code_prefix="ZZV01:"), _sealed("archive", "code_prefix")),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_partner_archives_forward(path: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        _method, seen, raw = server.httpd.hits[-1]
    assert seen == "/u8co/v1" + path.removeprefix("/v1/co")
    sent = json.loads(raw)
    assert set(sent) == write_keys(path, keys)
    # list 没有 code（只带 code_prefix）。
    assert (sent["archive"], sent.get("code")) == (body["archive"], body.get("code"))
    if "fields" in body:
        assert sent["fields"] == body["fields"]


def test_partner_bank_list_rejects_changed_since() -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(_ARC + "list", json=_auth(archive="customer_bank", changed_since="1"))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


_REJECTS = (
    # 编码必须是两段：客户编码最长 20，账号最长 50，联系人编码最长 30。
    (_ARC + "delete", _auth(archive="customer_bank", code="ZZC01")),
    (_ARC + "delete", _auth(archive="vendor_bank", code="ZZV01:" + "9" * 51)),
    (_ARC + "delete", _auth(archive="customer_contact", code="C" * 21 + ":L1")),
    (_ARC + "get", _auth(archive="vendor_contact", code="ZZV01:" + "L" * 31)),
    # 供应商联系人：同样 U8 自动编号；没有 position、favorite。
    (_ARC + "create", _auth(archive="vendor_contact", code="ZZV01:L1", fields={"name": "x"})),
    (_ARC + "update", _auth(archive="vendor_contact", code="ZZV01:", fields={"memo": "x"})),
    (_ARC + "update", _auth(archive="vendor_contact", code="ZZV01:L1", fields={"position": "x"})),
    (_ARC + "create", _auth(archive="vendor_contact", code="ZZV01:", fields={"name": "x", "favorite": "y"})),
    # 客户联系人：新增的第二段必须留空（U8 自动编号），修改、删除必须给联系人编码；其他档案的第二段不能空。
    (_ARC + "create", _auth(archive="customer_contact", code="ZZC01:L1", fields={"name": "x"})),
    (_ARC + "update", _auth(archive="customer_contact", code="ZZC01:", fields={"memo": "x"})),
    (_ARC + "create", _auth(archive="customer_bank", code="ZZC01:", fields={"branch": "x"})),
    # 银行账户：新增必须给 branch；未知标签；default 只收布尔或 0 / 1；所属银行编码最长 5。
    (_ARC + "create", _auth(archive="customer_bank", code="ZZC01:1", fields={"account_name": "x"})),
    (_ARC + "create", _auth(archive="customer_bank", code="ZZC01:1", fields={"branch": "x", "cAccountNum": "2"})),
    (_ARC + "update", _auth(archive="vendor_bank", code="ZZV01:1", fields={"default": "yes"})),
    (_ARC + "update", _auth(archive="vendor_bank", code="ZZV01:1", fields={"bank_code": "123456"})),
    (_ARC + "update", _auth(archive="vendor_bank", code="ZZV01:1", fields={"branch": "  "})),
    (_ARC + "create", _auth(archive="customer_bank", code="ZZC01:1", fields={"branch": "x"}, template="ZZC02:2")),
    # 联系人：新增必须给 name；性别、婚姻状况只收文字；生日格式；不能在 fields 写 code。
    (_ARC + "create", _auth(archive="customer_contact", code="ZZC01:", fields={"sex": "男"})),
    (_ARC + "create", _auth(archive="customer_contact", code="ZZC01:", fields={"name": "x", "sex": "M"})),
    (_ARC + "update", _auth(archive="customer_contact", code="ZZC01:L1", fields={"marriage": 1})),
    (_ARC + "update", _auth(archive="customer_contact", code="ZZC01:L1", fields={"birthday": "2026-02-30"})),
    (_ARC + "update", _auth(archive="customer_contact", code="ZZC01:L1", fields={"code": "L2"})),
    (_ARC + "update", _auth(archive="customer_contact", code="ZZC01:L1", fields={"cContactName": "x"})),
    # 文字长度按 Crm_Contact 列宽：名称 50、邮编 20；名称前后不能有空格。
    (_ARC + "create", _auth(archive="customer_contact", code="ZZC01:", fields={"name": "名" * 51})),
    (_ARC + "update", _auth(archive="customer_contact", code="ZZC01:L1", fields={"postcode": "9" * 21})),
    (_ARC + "create", _auth(archive="customer_contact", code="ZZC01:", fields={"name": " 张三"})),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_partner_archives_reject_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_openapi_lists_partner_archives() -> None:
    spec = _spec()
    for schema in ("ArcCreateIn", "ArcUpdateIn", "ArcKeyIn"):
        kinds = set(_enum(spec, schema, "archive"))
        assert set(_KINDS) <= kinds, schema
    for schema in ("ArcGetIn", "ArcListIn"):
        assert set(_ALL) <= set(_enum(spec, schema, "archive")), schema
