"""Read-only archives: get / list forwarding, write rejection and the extra response fields."""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_READ_ONLY = ("account", "unit", "unit_group", "settle_style", "voucher_sign", "currency", "bank", "project")
_ARC = "/v1/co/archives/"


def _code(archive: str) -> str:
    if archive in ("bank", "settle_style"):
        return "01"
    return "98:01" if archive == "project" else "1001"


# 契约 B7：bank、project 可新增、修改、删除（project 删除见 test_co_arc_bank_project）；其余只读。
# unit 可写（EAI），见 test_co_arc_item_write；unit_group、settle_style 之后也可写，见 test_co_arc_class_write。
# voucher_sign、currency 也可写（见 test_co_arc_gl_write）；account、trade_class 仍只读。
_STILL_READ_ONLY = ("account", "trade_class")


_FORWARD = (
    *((_ARC + "get", _auth(archive=a, code=_code(a)), _sealed("archive", "code")) for a in _READ_ONLY),
    (
        _ARC + "list",
        _auth(archive="project", project_class="ZF", code_prefix="ZF:", after="ZF:01", limit=10),
        _sealed("archive", "project_class", "code_prefix", "after", "limit"),
    ),
    (_ARC + "list", _auth(archive="account", changed_since="25131155"), _sealed("archive", "changed_since")),
    (_ARC + "get", _auth(archive="project", code="9:" + "x" * 60), _sealed("archive", "code")),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_read_only_archives_forward(path: str, body: dict, keys: set[str]) -> None:
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
    *((_ARC + "delete", _auth(archive=a, code=_code(a))) for a in _STILL_READ_ONLY),
    *(
        (_ARC + op, _auth(archive=a, code=_code(a), fields={"name": "x"}))
        for a in _STILL_READ_ONLY
        for op in ("create", "update")
    ),
    (_ARC + "get", _auth(archive="project", code="98")),
    (_ARC + "get", _auth(archive="project", code=":01")),
    (_ARC + "get", _auth(archive="project", code="987:01")),
    (_ARC + "get", _auth(archive="project", code="98:" + "x" * 61)),
    (_ARC + "get", _auth(archive="account", code="1" * 61)),
    (_ARC + "list", _auth(archive="customer", project_class="98")),
    (_ARC + "list", _auth(archive="project", project_class="987")),
    (_ARC + "list", _auth(archive="project", project_class="9-")),
    (_ARC + "list", _auth(archive="project", after="01")),
    (_ARC + "list", _auth(archive="project", changed_since="1")),
    (_ARC + "list", _auth(archive="voucher_sign", changed_since="1")),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_read_only_archive_rejects_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _RoBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if path.endswith("/get"):
            return {
                "ok": True,
                "archive": payload["archive"],
                "code": payload["code"],
                "fields": {"ccode": "1001", "ccode_name": "库存现金", "bend": "1"},
                "ufts": "25328063",
                "fiscal_year": 2026,
                "project_class": None,
            }
        item = {"code": "98:01", "name": "现金", "class_code": "0101", "project_class": "98", "closed": False}
        item["ufts"] = None
        return {"ok": True, "archive": "project", "items": [item], "next": None, "watermark": None}


def test_read_only_responses_keep_extra_fields() -> None:
    client = _client(_RoBridge())
    got = client.post(_ARC + "get", json=_auth(archive="account", code="1001"))
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["fields"]["ccode_name"] == "库存现金"
    assert (body["ufts"], body["fiscal_year"]) == ("25328063", 2026)
    listed = client.post(_ARC + "list", json=_auth(archive="project", project_class="98"))
    assert listed.status_code == 200, listed.text
    item = listed.json()["items"][0]
    assert (item["code"], item["project_class"], item["closed"]) == ("98:01", "98", False)
    assert listed.json().get("watermark") is None


def test_openapi_lists_read_only_kinds_for_reads_only() -> None:
    spec = _spec()
    for kind in _READ_ONLY:
        assert kind in _enum(spec, "ArcGetIn", "archive")
        assert kind in _enum(spec, "ArcListIn", "archive")
    for kind in _STILL_READ_ONLY:
        assert kind not in _enum(spec, "ArcKeyIn", "archive")
        assert kind not in _enum(spec, "ArcCreateIn", "archive")
    assert "project_class" in spec["components"]["schemas"]["ArcListIn"]["properties"]
