"""Archive kind pay_agreement is writable through controlled SQL in the bridge — forwarding of create/update/delete
and the checks made before the bridge (field names are the table columns, terms are integers, code at most 10)."""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_ARC = "/v1/co/archives/"
_NEW = {"cName": "协议A", "iLZYJ": 0, "iPLNum": "3", "dblZQNum": 30, "cMemo": "文本A", "dEDate": ""}

_FORWARD = (
    (_ARC + "create", _auth(archive="pay_agreement", code="Z01", fields=_NEW), _sealed("archive", "code", "fields")),
    (_ARC + "create", _auth(archive="pay_agreement", code="1" * 10, fields={"cName": "x"}), _sealed("archive", "code", "fields")),
    (
        _ARC + "update",
        _auth(archive="pay_agreement", code="Z01", fields={"dEDate": "2026-12-31", "ILZYJ": 1}),
        _sealed("archive", "code", "fields"),
    ),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"dblzqnum": 45}), _sealed("archive", "code", "fields")),
    (_ARC + "delete", _auth(archive="pay_agreement", code="Z01"), _sealed("archive", "code")),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_agreement_writes_forward(path: str, body: dict, keys: set[str]) -> None:
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
    # 编码最长 10。
    (_ARC + "create", _auth(archive="pay_agreement", code="1" * 11, fields={"cName": "x"})),
    (_ARC + "delete", _auth(archive="pay_agreement", code="1" * 11)),
    # 新增必须给名称；不收 template；标签只收表列名，制单日期、制单人、rowversion 不能写。
    (_ARC + "create", _auth(archive="pay_agreement", code="Z01", fields={"iLZYJ": 0})),
    (_ARC + "create", _auth(archive="pay_agreement", code="Z01", fields={"cName": "x"}, template="001")),
    (_ARC + "create", _auth(archive="pay_agreement", code="Z01", fields={"cName": "x", "cMaker": "op001"})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"dtDate": "2026-11-24"})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"ufts": "1"})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"code": "Z02"})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"name": "x"})),
    # 取值：名称非空白、最长 60；条款是整数；停用日期是 yyyy-mm-dd。
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"cName": "   "})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"cName": "名" * 61})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"cMemo": "x" * 256})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"iPLNum": 3.5})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"dblZQNum": 45.0})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"iZQ": "abc"})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"iMon": True})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"iDay1": 2147483648})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"dEDate": "2026/12/31"})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={"dEDate": "2026-02-30"})),
    (_ARC + "update", _auth(archive="pay_agreement", code="Z01", fields={})),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_agreement_rejects_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_openapi_lists_agreement_for_every_write() -> None:
    spec = _spec()
    for schema in ("ArcCreateIn", "ArcUpdateIn", "ArcKeyIn"):
        assert "pay_agreement" in _enum(spec, schema, "archive")
