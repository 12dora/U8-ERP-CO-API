"""Archive exchange_rate (汇率): code forms, currency/fiscal_year filters, forwarding, response fields; writes."""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_ARC = "/v1/co/archives/"
_EXCH = "exchange_rate"

_FORWARD = (
    (_ARC + "get", _auth(archive=_EXCH, code="美元:2026:9"), _sealed("archive", "code")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:2026:09"), _sealed("archive", "code")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:2026:9:2026-09-14"), _sealed("archive", "code")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:2026-09-14"), _sealed("archive", "code")),
    (_ARC + "get", _auth(archive=_EXCH, code="X" * 8 + ":2026:12:" + "d" * 10), _sealed("archive", "code")),
    (
        _ARC + "list",
        _auth(archive=_EXCH, currency="美元", fiscal_year=2026, after="美元:2026:5", limit=5),
        _sealed("archive", "currency", "fiscal_year", "after", "limit"),
    ),
    (_ARC + "list", _auth(archive=_EXCH, changed_since="24877043"), _sealed("archive", "changed_since")),
    (_ARC + "list", _auth(archive=_EXCH, code_prefix="美元:2026:"), _sealed("archive", "code_prefix")),
    # 写入：固定汇率按期间（rate、adjust_rate），浮动汇率按日（rate）。
    (
        _ARC + "create",
        _auth(archive=_EXCH, code="美元:2026:10", fields={"rate": 7.1}),
        _sealed("archive", "code", "fields"),
    ),
    (
        _ARC + "create",
        _auth(archive=_EXCH, code="美元:2026:10:2026-10-05", fields={"rate": "7.12"}),
        _sealed("archive", "code", "fields"),
    ),
    (
        _ARC + "update",
        _auth(archive=_EXCH, code="美元:2026:10", fields={"rate": 7.2, "adjust_rate": 7.3}),
        _sealed("archive", "code", "fields"),
    ),
    (_ARC + "delete", _auth(archive=_EXCH, code="美元:2026:10"), _sealed("archive", "code")),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_exchange_rate_forwards(path: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        _method, seen, raw = server.httpd.hits[-1]
    assert seen == "/u8co/v1" + path.removeprefix("/v1/co")
    sent = json.loads(raw)
    assert set(sent) == write_keys(path, keys)
    assert (sent["archive"], sent.get("code"), sent.get("fiscal_year")) == (
        body["archive"],
        body.get("code"),
        body.get("fiscal_year"),
    )


_REJECTS = (
    *((_ARC + op, _auth(archive=_EXCH, code="美元:2026:9", fields={"nflat": 7})) for op in ("create", "update")),
    # 写入：新增一次一种汇率；浮动汇率没有调整汇率；汇率大于 0；编码不能是按日期读取的写法；
    # 新增浮动汇率的日在期间内；不收 template。
    (_ARC + "create", _auth(archive=_EXCH, code="美元:2026:9", fields={"rate": 7, "adjust_rate": 7})),
    (_ARC + "create", _auth(archive=_EXCH, code="美元:2026:9", fields={})),
    (_ARC + "update", _auth(archive=_EXCH, code="美元:2026:9:2026-09-14", fields={"adjust_rate": 7})),
    (_ARC + "update", _auth(archive=_EXCH, code="美元:2026:9", fields={"rate": 0})),
    (_ARC + "update", _auth(archive=_EXCH, code="美元:2026:9", fields={"rate": True})),
    (_ARC + "create", _auth(archive=_EXCH, code="美元:2026-09-14", fields={"rate": 7})),
    (_ARC + "create", _auth(archive=_EXCH, code="美元:2026:9:2026-10-14", fields={"rate": 7})),
    (_ARC + "create", _auth(archive=_EXCH, code="美元:2026:9", fields={"rate": 7}, template="美元:2026:8")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:2026")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:2026:13")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:2026:0")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:1899:1")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:26:1")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:2026-02-30")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:2026/09/14")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元 :2026:9")),
    (_ARC + "get", _auth(archive=_EXCH, code="X" * 9 + ":2026:9")),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:2026:9:" + "d" * 11)),
    (_ARC + "get", _auth(archive=_EXCH, code="美元:2026:9:1:2")),
    (_ARC + "list", _auth(archive=_EXCH, after="美元:2026-09-14")),
    (_ARC + "list", _auth(archive=_EXCH, fiscal_year=26)),
    (_ARC + "list", _auth(archive=_EXCH, fiscal_year="2026")),
    (_ARC + "list", _auth(archive=_EXCH, currency="X" * 9)),
    (_ARC + "list", _auth(archive=_EXCH, currency=" 美元")),
    (_ARC + "list", _auth(archive=_EXCH, code_prefix="x" * 28)),
    (_ARC + "list", _auth(archive=_EXCH, project_class="98")),
    (_ARC + "list", _auth(archive="currency", currency="美元")),
    (_ARC + "list", _auth(archive="account", fiscal_year=2026)),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_exchange_rate_rejects_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _ExchBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        item = {
            "code": "美元:2026:8",
            "name": "美元",
            "currency": "美元",
            "year": 2026,
            "period": 8,
            "day": None,
            "rate": 7.1070,
            "adjust_rate": 7.1000,
            "mode": "fixed",
            "ufts": "24877045",
        }
        return {
            "ok": True,
            "archive": _EXCH,
            "items": [item],
            "next": "美元:2026:8",
            "watermark": "24877050",
            "fiscal_year": 2026,
            "rate_mode": "fixed",
        }


def test_exchange_rate_list_keeps_rate_fields() -> None:
    client = _client(_ExchBridge())
    listed = client.post(_ARC + "list", json=_auth(archive=_EXCH, limit=1))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    item = body["items"][0]
    assert (item["code"], item["period"], item["rate"], item["adjust_rate"]) == ("美元:2026:8", 8, 7.1070, 7.1000)
    assert (item["mode"], item.get("day"), body["rate_mode"], body["fiscal_year"]) == ("fixed", None, "fixed", 2026)
    assert body["next"] == "美元:2026:8"


def test_openapi_lists_exchange_rate_for_reads_and_writes() -> None:
    spec = _spec()
    for schema in ("ArcGetIn", "ArcListIn", "ArcKeyIn", "ArcCreateIn", "ArcUpdateIn"):
        assert _EXCH in _enum(spec, schema, "archive"), schema
