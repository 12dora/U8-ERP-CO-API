"""Contract B7: bank create/update/delete and project create/update/delete forwarding,
and the checks made before the bridge."""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_ARC = "/v1/co/archives/"
_BANK = {"name": "测试开户行", "account": "999999999999", "cbankcode": "01", "ccurrencyname": "人民币", "flag": False}
_PROJECT = {"name": "测试项目", "citemccode": "01", "bclose": False}

_FORWARD = (
    (_ARC + "create", _auth(archive="bank", code="99", fields=_BANK), _sealed("archive", "code", "fields")),
    (_ARC + "update", _auth(archive="bank", code="99", fields={"name": "改名"}), _sealed("archive", "code", "fields")),
    (_ARC + "delete", _auth(archive="bank", code="99"), _sealed("archive", "code")),
    (_ARC + "create", _auth(archive="project", code="00:9901", fields=_PROJECT), _sealed("archive", "code", "fields")),
    (
        _ARC + "update",
        _auth(archive="project", code="00:9901", fields={"bclose": True}),
        _sealed("archive", "code", "fields"),
    ),
    (_ARC + "delete", _auth(archive="project", code="00:9901"), _sealed("archive", "code")),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_bank_and_project_writes_forward(path: str, body: dict, keys: set[str]) -> None:
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
    (_ARC + "delete", _auth(archive="project", code="9901")),
    (_ARC + "create", _auth(archive="bank", code="9999", fields=_BANK)),
    (_ARC + "delete", _auth(archive="bank", code="9999")),
    (_ARC + "create", _auth(archive="project", code="9901", fields=_PROJECT)),
    (_ARC + "create", _auth(archive="project", code="00:9901", fields={"name": "x"})),
    (_ARC + "create", _auth(archive="project", code="00:9901", fields={**_PROJECT, "cDirection": "借"})),
    (_ARC + "create", _auth(archive="project", code="00:9901", fields=_PROJECT, template="00:01")),
    (_ARC + "update", _auth(archive="project", code="00:9901", fields={"bclose": "yes"})),
    (_ARC + "update", _auth(archive="project", code="00:9901", fields={"code": "00:02"})),
    # 币种的名称就是编码，不能放进 fields（币种可写，见 test_co_arc_gl_write）。
    (_ARC + "create", _auth(archive="currency", code="美元", fields={"name": "x"})),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_bank_and_project_rejects_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_openapi_lists_bank_and_project_for_writes() -> None:
    spec = _spec()
    for schema in ("ArcCreateIn", "ArcUpdateIn"):
        assert {"bank", "project"} <= set(_enum(spec, schema, "archive"))
        assert "trade_class" not in _enum(spec, schema, "archive")
    assert "bank" in _enum(spec, "ArcKeyIn", "archive")
    assert "project" in _enum(spec, "ArcKeyIn", "archive")
