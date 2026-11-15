"""原因码档案（reason，桥 ArcReason）：新增、修改、删除、原样转给桥，编码最长 10，OpenAPI 列出 reason。"""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_ARC = "/v1/co/archives/"
_NEW = {"name": "检测指标不合格", "Reasontype": 1, "ReasonMemo": "说明"}

_FORWARD = (
    (_ARC + "create", _auth(archive="reason", code="T9", fields=_NEW), _sealed("archive", "code", "fields")),
    (_ARC + "update", _auth(archive="reason", code="T9", fields={"name": "改名"}), _sealed("archive", "code", "fields")),
    (_ARC + "delete", _auth(archive="reason", code="T9"), _sealed("archive", "code")),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_reason_forwards(path: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        _method, seen, raw = server.httpd.hits[-1]
    assert seen == "/u8co/v1" + path.removeprefix("/v1/co")
    sent = json.loads(raw)
    assert set(sent) == write_keys(path, keys)
    assert (sent["archive"], sent["code"]) == ("reason", body["code"])
    if "fields" in body:
        assert sent["fields"] == body["fields"]


@pytest.mark.parametrize(
    ("path", "body"),
    (
        (_ARC + "create", _auth(archive="reason", code="T" * 11, fields=_NEW)),
        (_ARC + "delete", _auth(archive="reason", code="T" * 11)),
        (_ARC + "get", _auth(archive="reason", code="T" * 11)),
        (_ARC + "update", _auth(archive="reason", code="T9", fields={"code": "T8"})),
    ),
)
def test_reason_rejects_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_openapi_lists_reason() -> None:
    spec = _spec()
    for schema in ("ArcCreateIn", "ArcUpdateIn", "ArcKeyIn", "ArcGetIn", "ArcListIn"):
        assert "reason" in set(_enum(spec, schema, "archive")), schema
