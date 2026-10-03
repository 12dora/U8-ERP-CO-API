"""Read-only archives operator (U8 操作员) and role (角色): code length, no ufts, forwarding and response fields."""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_ARC = "/v1/co/archives/"
_UA = ("operator", "role")

_FORWARD = (
    *((_ARC + "get", _auth(archive=kind, code="U" * 20), _sealed("archive", "code")) for kind in _UA),
    *(
        (
            _ARC + "list",
            _auth(archive=kind, code_prefix="A", name_like="王", after="A01", limit=5),
            _sealed("archive", "code_prefix", "name_like", "after", "limit"),
        )
        for kind in _UA
    ),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_operator_and_role_forward(path: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        _method, seen, raw = server.httpd.hits[-1]
    assert seen == "/u8co/v1" + path.removeprefix("/v1/co")
    sent = json.loads(raw)
    assert set(sent) == write_keys(path, keys)
    assert sent["archive"] == body["archive"]


_REJECTS = (
    *((_ARC + "delete", _auth(archive=kind, code="A01")) for kind in _UA),
    *((_ARC + "create", _auth(archive=kind, code="A01", fields={"name": "x"})) for kind in _UA),
    *((_ARC + "get", _auth(archive=kind, code="U" * 21)) for kind in _UA),
    *((_ARC + "list", _auth(archive=kind, after="U" * 21)) for kind in _UA),
    *((_ARC + "list", _auth(archive=kind, code_prefix="U" * 21)) for kind in _UA),
    *((_ARC + "list", _auth(archive=kind, changed_since="24877043")) for kind in _UA),
    (_ARC + "list", _auth(archive="operator", include_disposed=True)),
    (_ARC + "list", _auth(archive="role", currency="美元")),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_operator_and_role_reject_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def _operator() -> dict:
    return {
        "code": "A01",
        "name": "王小明",
        "dept": "示例车间",
        "state": 0,
        "disabled": False,
        "roles": ["R01"],
        "person_code": "P0001",
        "person_name": "王小明",
    }


class _UaBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        role = {"code": "R01", "name": "会计", "members": ["A01"]}
        item = _operator() if payload.get("archive") == "operator" else role
        if path.endswith("/get"):
            return {"ok": True, "archive": payload["archive"], "code": item["code"], "fields": item}
        return {"ok": True, "archive": payload["archive"], "items": [item], "next": None, "watermark": None}


def test_operator_and_role_keep_fields() -> None:
    client = _client(_UaBridge())
    listed = client.post(_ARC + "list", json=_auth(archive="operator"))
    assert listed.status_code == 200, listed.text
    item = listed.json()["items"][0]
    assert (item["roles"], item["person_code"], item["disabled"], item["state"]) == (["R01"], "P0001", False, 0)
    assert listed.json().get("watermark") is None
    got = client.post(_ARC + "get", json=_auth(archive="role", code="R01"))
    assert got.status_code == 200, got.text
    assert got.json()["fields"]["members"] == ["A01"]


def test_openapi_lists_operator_and_role_for_reads_only() -> None:
    spec = _spec()
    for kind in _UA:
        assert kind in _enum(spec, "ArcGetIn", "archive")
        assert kind in _enum(spec, "ArcListIn", "archive")
        assert kind not in _enum(spec, "ArcKeyIn", "archive")
        assert kind not in _enum(spec, "ArcCreateIn", "archive")
