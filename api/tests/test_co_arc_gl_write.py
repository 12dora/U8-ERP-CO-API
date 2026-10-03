"""currency and voucher_sign become writable (create through U8PzInsert's EAI components, update and delete
through guarded SQL in bridge ArcGl) — forwarding and the format checks made before the bridge. account stays read-only."""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_ARC = "/v1/co/archives/"
_GL = ("currency", "voucher_sign")
_CODES = {"currency": "测试币", "voucher_sign": "CO"}
_NEW = {
    "currency": {"code": "TST", "caltype": 1, "precision": 4, "error": 0.0001},
    "voucher_sign": {"type_name": "CO测试类别", "order_code": 9},
}
_CHANGE = {
    "currency": {"precision": 2},
    "voucher_sign": {"type_name": "CO测试类别改"},
}

_FORWARD = (
    *((_ARC + "create", _auth(archive=a, code=_CODES[a], fields=_NEW[a]), _sealed("archive", "code", "fields")) for a in _GL),
    *((_ARC + "update", _auth(archive=a, code=_CODES[a], fields=_CHANGE[a]), _sealed("archive", "code", "fields")) for a in _GL),
    *((_ARC + "delete", _auth(archive=a, code=_CODES[a]), _sealed("archive", "code")) for a in _GL),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_gl_archive_writes_forward(path: str, body: dict, keys: set[str]) -> None:
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
    # 币种：新增必须给币种符号 code（最长 4）；符号不能修改；名称就是编码，不能放进 fields；名称最长 8。
    (_ARC + "create", _auth(archive="currency", code="测试币", fields={"caltype": 1})),
    (_ARC + "create", _auth(archive="currency", code="测试币", fields={"code": "  "})),
    (_ARC + "create", _auth(archive="currency", code="测试币", fields={"code": "ABCDE"})),
    (_ARC + "update", _auth(archive="currency", code="测试币", fields={"CODE": "T2"})),
    (_ARC + "update", _auth(archive="currency", code="测试币", fields={"Name": "新名"})),
    (_ARC + "delete", _auth(archive="currency", code="九" * 9)),
    # 凭证类别：类别字最长 2、新增必须给 type_name、修改只收 type_name、类别字不能放进 fields。
    (_ARC + "create", _auth(archive="voucher_sign", code="COX", fields={"type_name": "x"})),
    (_ARC + "create", _auth(archive="voucher_sign", code="CO", fields={"order_code": 9})),
    (_ARC + "create", _auth(archive="voucher_sign", code="CO", fields={"type_name": "x", "type": "CO"})),
    (_ARC + "create", _auth(archive="voucher_sign", code="CO", fields={"type_name": "  "})),
    (_ARC + "update", _auth(archive="voucher_sign", code="CO", fields={"order_code": 3})),
    (_ARC + "update", _auth(archive="voucher_sign", code="CO", fields={"type_name": "x" * 31})),
    # 科目仍只读（ICode 实测不可用、写入年度无法确定）；两类都不收 template。
    (_ARC + "create", _auth(archive="account", code="113101", fields={"name": "x"})),
    (_ARC + "update", _auth(archive="account", code="113101", fields={"name": "x"})),
    (_ARC + "delete", _auth(archive="account", code="113101")),
    (_ARC + "create", _auth(archive="voucher_sign", code="CO", fields={"type_name": "x"}, template="转")),
    (_ARC + "create", _auth(archive="currency", code="测试币", fields={"code": "T"}, template="美元")),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_gl_archive_rejects_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_openapi_lists_gl_kinds_for_writes() -> None:
    spec = _spec()
    for schema in ("ArcCreateIn", "ArcUpdateIn", "ArcKeyIn"):
        kinds = set(_enum(spec, schema, "archive"))
        assert set(_GL) <= kinds, schema
        assert not {"account", "trade_class", "customer_address"} & kinds, schema  # fa_card 可新增、撤销
