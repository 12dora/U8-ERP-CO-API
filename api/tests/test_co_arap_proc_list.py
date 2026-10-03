"""/v1/co/arap/process/list（事件源）：转发字段、提前拒绝、只读权限、响应放行和 OpenAPI。假桥在本机。"""

from __future__ import annotations

import json

import pytest
from tests.support import base_claims
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec
from u8co_api.co_access import ACCESS, READ

_PATH = "/v1/co/arap/process/list"
_BRIDGE = "/v1/arap/process/list"

_FORWARD = (
    (_auth(flag="AR"), _sealed("flag")),
    (
        _auth(flag="AP", changed_since="30025", after=30100, limit=500, keys_only=True, open_only=False),
        _sealed("flag", "changed_since", "after", "limit", "keys_only", "open_only"),
    ),
    (_auth(flag="AR", changed_since=0), _sealed("flag", "changed_since")),
    (
        _auth(flag="AR", digest=True, fiscal_year=2026, periods=[9, 10], after="OVAfSFhBUjAwMDE", limit=1),
        _sealed("flag", "digest", "fiscal_year", "periods", "after", "limit"),
    ),
    (_auth(flag="AP", digest=True), _sealed("flag", "digest")),
)

_REJECTS = (
    _auth(),
    _auth(flag="ar"),
    _auth(flag="GL"),
    _auth(flag="AR", changed_since="12a"),
    _auth(flag="AR", changed_since=-1),
    _auth(flag="AR", changed_since="2147483648"),
    _auth(flag="AR", changed_since=1.5),
    _auth(flag="AR", changed_since="\u0661\u0662"),
    _auth(flag="AR", after="5"),
    _auth(flag="AR", after=-1),
    _auth(flag="AR", limit=0),
    _auth(flag="AR", limit=501),
    _auth(flag="AR", keys_only="true"),
    _auth(flag="AR", fiscal_year=2026),
    _auth(flag="AR", periods=[9]),
    _auth(flag="AR", digest=True, changed_since="1"),
    _auth(flag="AR", digest=True, keys_only=True),
    _auth(flag="AR", digest=True, open_only=True),
    _auth(flag="AR", digest=True, after=5),
    _auth(flag="AR", digest=True, fiscal_year=1999),
    _auth(flag="AR", digest=True, periods=[]),
    _auth(flag="AR", digest=True, periods=[13]),
    _auth(flag="AR", digest=True, periods=[9, 9]),
    _auth(flag="AR", digest=True, periods=["9"]),
    _auth(flag="AR", side="ar"),
)


@pytest.mark.parametrize(("body", "keys"), _FORWARD)
def test_proc_list_forwards_exact_sealed_keys(body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(_PATH, json=body)
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert method == "POST"
    assert seen == "/u8co" + _BRIDGE
    assert set(sent) == keys
    assert _SECRET.encode() not in raw
    for name in ("changed_since", "after", "periods", "digest"):
        if name in body:
            assert sent[name] == body[name]


@pytest.mark.parametrize("body", _REJECTS)
def test_proc_list_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


class _ListBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        super().call(path, payload)
        periods = [{"year": 2026, "period": 9}, {"year": 2026, "period": 10}]
        base = {"ok": True, "flag": "AR", "open_periods": periods, "last_closed": {"year": 2026, "period": 8}}
        if payload.get("digest"):
            batch = {
                "flag": "AR",
                "style": "9P",
                "code": "HXAR0000000000001",
                "min_id": 30101,
                "max_id": 30102,
                "pz": None,
                "sum_d_f": "0.00",
                "sum_c_f": "1200.00",
                "rows": 2,
                "fiscal_year": 2026,
                "partners": ["C001"],
            }
            return {**base, "digest": True, "periods": periods, "items": [batch], "next": None}
        row = {"id": 30101, "flag": "AR", "style": "9P", "code": "HXAR0000000000001", "debit_f": "0.00", "extra": 1}
        return {**base, "items": [row], "next": 30101, "watermark": "30525", "ident": "30526"}


def test_proc_list_response_keeps_bridge_fields() -> None:
    body = _client(_ListBridge()).post(_PATH, json=_auth(flag="AR", changed_since="30000")).json()
    assert (body["watermark"], body["ident"], body["next"]) == ("30525", "30526", 30101)
    assert body["items"][0]["extra"] == 1
    assert body["items"][0]["debit_f"] == "0.00"
    assert body["open_periods"][0] == {"year": 2026, "period": 9}
    assert body["last_closed"] == {"year": 2026, "period": 8}


def test_proc_list_digest_response() -> None:
    body = _client(_ListBridge()).post(_PATH, json=_auth(flag="AR", digest=True)).json()
    assert body["digest"] is True
    assert body["periods"][1] == {"year": 2026, "period": 10}
    batch = body["items"][0]
    assert (batch["min_id"], batch["rows"], batch["partners"]) == (30101, 2, ["C001"])
    assert (batch["pz"], batch["fiscal_year"]) == (None, 2026)
    # 响应去掉值为 null 的顶层字段：最后一页没有 next。
    assert "next" not in body


def test_proc_list_is_read_only_and_gated() -> None:
    body = _auth(flag="AP")
    assert ACCESS["co:arap/process/list"] == READ
    fake = FakeBridge()
    assert _client(fake, co_enabled=False).post(_PATH, json=body).status_code == 404
    assert _client(fake, claims=base_claims()).post(_PATH, json=body).status_code == 403
    outside = _client(fake).post(_PATH, json=dict(body, acc="001"))
    assert outside.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []
    reader = _client(fake, claims=base_claims(u8co_read=True)).post(_PATH, json=body)
    assert reader.status_code == 200, reader.text
    assert fake.calls[-1][0] == _BRIDGE
    assert fake.calls[-1][1]["flag"] == "AP"


def test_proc_list_openapi() -> None:
    spec = _spec()
    operation = spec["paths"][_PATH]["post"]
    assert operation["summary"] == "应收应付处理记录"
    assert operation["operationId"] == "coArapProcessList"
    assert operation["tags"] == ["应收应付处理"]
    assert operation["x-u8co-access"] == "read"
    assert "权限：只读" in operation["description"]
    assert "IDENT_CURRENT" in operation["description"]
    assert set(_enum(spec, "CoArapProcListIn", "flag")) == {"AR", "AP"}
