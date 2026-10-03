"""Archive fa_card (固定资产卡片): code length, fa-only list filters, forwarding and response fields.
可新增、撤销本期新增（写入见 test_co_fa_write），不能修改。"""

from __future__ import annotations

import json

import pytest
from tests.support import write_keys
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _enum, _spec

_ARC = "/v1/co/archives/"
_FA = "fa_card"

_FORWARD = (
    (_ARC + "get", _auth(archive=_FA, code="FA-9001"), _sealed("archive", "code")),
    (_ARC + "get", _auth(archive=_FA, code="C" * 20), _sealed("archive", "code")),
    (
        _ARC + "list",
        _auth(archive=_FA, type_code="20", dept_code="D901", include_disposed=True, after="FA-9000", limit=5),
        _sealed("archive", "type_code", "dept_code", "include_disposed", "after", "limit"),
    ),
    (_ARC + "list", _auth(archive=_FA, include_disposed=False), _sealed("archive", "include_disposed")),
    (_ARC + "list", _auth(archive=_FA, code_prefix="FA-", name_like="机"), _sealed("archive", "code_prefix", "name_like")),
)


@pytest.mark.parametrize(("path", "body", "keys"), _FORWARD)
def test_fa_card_forwards(path: str, body: dict, keys: set[str]) -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        _method, seen, raw = server.httpd.hits[-1]
    assert seen == "/u8co/v1" + path.removeprefix("/v1/co")
    sent = json.loads(raw)
    assert set(sent) == write_keys(path, keys)
    assert (sent["archive"], sent.get("include_disposed")) == (body["archive"], body.get("include_disposed"))


_REJECTS = (
    *((_ARC + op, _auth(archive=_FA, code="FA-9001", fields={"sAssetName": "x"})) for op in ("create", "update")),
    (_ARC + "get", _auth(archive=_FA, code="C" * 21)),
    (_ARC + "get", _auth(archive=_FA, code=" FA-9001")),
    (_ARC + "list", _auth(archive=_FA, after="C" * 21)),
    (_ARC + "list", _auth(archive=_FA, code_prefix="C" * 21)),
    (_ARC + "list", _auth(archive=_FA, changed_since="24877043")),
    (_ARC + "list", _auth(archive=_FA, type_code="T" * 21)),
    (_ARC + "list", _auth(archive=_FA, type_code=" 20")),
    (_ARC + "list", _auth(archive=_FA, dept_code="D" * 13)),
    (_ARC + "list", _auth(archive=_FA, include_disposed="true")),
    (_ARC + "list", _auth(archive=_FA, include_disposed=1)),
    (_ARC + "list", _auth(archive=_FA, currency="美元")),
    (_ARC + "list", _auth(archive="account", type_code="20")),
    (_ARC + "list", _auth(archive="department", dept_code="D901")),
    (_ARC + "list", _auth(archive="exchange_rate", include_disposed=False)),
)


@pytest.mark.parametrize(("path", "body"), _REJECTS)
def test_fa_card_rejects_before_bridge(path: str, body: dict) -> None:
    fake = FakeBridge()
    client = _client(fake)
    denied = client.post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def _card() -> dict:
    return {
        "code": "FA-9001",
        "name": "引风机",
        "asset_num": "A900001",
        "spec": "NO.11",
        "type_code": "20",
        "type_name": "生产设备",
        "status": "在用",
        "origin": "直接购入",
        "depreciation_method": "平均年限法(二)",
        "start_date": "2026-01-01",
        "entry_date": "2026-01-01",
        "useful_life_months": 120,
        "original_value": 10000.00,
        "accumulated_depreciation": 1000.00,
        "impairment": 0.0,
        "net_value": 9000.00,
        "disposed": True,
        "disposed_date": "2026-04-30",
        "depts": [{"code": "D901", "name": "示例车间", "ratio": 1}],
        "keeper": None,
        "location": None,
    }


class _FaBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        head = {"ok": True, "archive": _FA, "fiscal_year": 2026, "as_of": "2026-09-30", "depr_period": 8}
        if path.endswith("/get"):
            return dict(head, code="FA-9001", fields=_card())
        return dict(head, items=[_card()], next="FA-9001", watermark=None)


def test_fa_card_list_keeps_card_fields() -> None:
    client = _client(_FaBridge())
    listed = client.post(_ARC + "list", json=_auth(archive=_FA, include_disposed=True, limit=1))
    assert listed.status_code == 200, listed.text
    body = listed.json()
    item = body["items"][0]
    assert (item["code"], item["accumulated_depreciation"], item["net_value"]) == ("FA-9001", 1000.00, 9000.00)
    assert (item["disposed"], item["depts"][0]["code"], body["as_of"], body["depr_period"]) == (True, "D901", "2026-09-30", 8)
    assert (body["next"], body.get("watermark"), body["fiscal_year"]) == ("FA-9001", None, 2026)
    got = client.post(_ARC + "get", json=_auth(archive=_FA, code="FA-9001"))
    assert got.status_code == 200, got.text
    assert got.json()["fields"]["depts"] == [{"code": "D901", "name": "示例车间", "ratio": 1}]


def test_openapi_lists_fa_card_for_reads_create_and_delete() -> None:
    spec = _spec()
    assert _FA in _enum(spec, "ArcGetIn", "archive")
    assert _FA in _enum(spec, "ArcListIn", "archive")
    assert _FA in _enum(spec, "ArcKeyIn", "archive")  # 撤销本期新增
    assert _FA in _enum(spec, "ArcCreateIn", "archive")
    assert _FA not in _enum(spec, "ArcUpdateIn", "archive")
