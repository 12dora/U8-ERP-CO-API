"""查询参数 fields / compact 接到每条 /v1/co POST 路由上；x-u8co-access 标在每个操作上。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from tests.test_co_update_close_gen import _spec
from u8co_api.co_access import ACCESS
from u8co_api.co_call import shapeable
from u8co_api.co_routes import POST_ROUTES

_LOAD = "/v1/co/vouchers/load"
_LOAD_BODY = _auth(type="sale_order", id=1)
# 出参容器是带必填字段的类型化模型的路由：拒绝 fields / compact。
_TYPED = {
    "/v1/co/vouchers/close",
    "/v1/co/arap/writeoff",
    "/v1/co/arap/writeoff/cancel",
    "/v1/co/arap/voucher",
}


class LoadBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        if path == "/v1/vouchers/load":
            self.calls.append((path, dict(payload)))
            return {
                "ok": True,
                "type": "sale_order",
                "id": 1,
                "code": "0000000001",
                "head": {"cCode": "0000000001", "cCusCode": "C001", "cMemo": " "},
                "lines": [{"cInvCode": "A", "iQuantity": "1", "cFree1": ""}],
                "state": {"verified": False, "verifier": "", "verified_at": ""},
            }
        return super().call(path, payload)


def test_fields_projects_head_and_lines_but_keeps_the_envelope() -> None:
    fake = LoadBridge()
    ok = _client(fake).post(_LOAD + "?fields=ccuscode,lines.cinvcode", json=_LOAD_BODY)
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["head"] == {"cCusCode": "C001"}
    assert data["lines"] == [{"cInvCode": "A"}]
    assert (data["ok"], data["type"], data["id"], data["code"]) == (True, "sale_order", 1, "0000000001")
    assert data["state"]["verified"] is False


def test_compact_drops_empty_values_inside_containers_only() -> None:
    fake = LoadBridge()
    ok = _client(fake).post(_LOAD + "?compact=true", json=_LOAD_BODY)
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["head"] == {"cCode": "0000000001", "cCusCode": "C001"}
    assert data["lines"] == [{"cInvCode": "A", "iQuantity": "1"}]
    assert data["state"]["verifier"] == ""


def test_no_query_keeps_the_response_unchanged() -> None:
    fake = LoadBridge()
    ok = _client(fake).post(_LOAD, json=_LOAD_BODY)
    assert ok.json()["head"]["cMemo"] == " "


def test_fields_on_lists_and_archives() -> None:
    fake = FakeBridge()
    client = _client(fake)
    listed = client.post("/v1/co/vouchers/list?fields=code", json=_auth(type="ar_receipt"))
    assert listed.status_code == 200, listed.text
    assert listed.json()["items"] == [{"id": 1, "code": "0000000001"}]  # items 总保留 id、code、ok、error
    assert listed.json()["next"] == 1
    got = client.post("/v1/co/archives/get?fields=fields.name", json=_auth(archive="customer", code="C900001"))
    assert got.status_code == 200, got.text
    assert got.json()["fields"] == {"name": "客户甲"}


@pytest.mark.parametrize("query", ["fields=", "fields=a,,b", "fields=bad-name", "fields=head.a.b"])
def test_bad_fields_are_400_before_the_bridge(query: str) -> None:
    fake = LoadBridge()
    denied = _client(fake).post(f"{_LOAD}?{query}", json=_LOAD_BODY)
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "fields"
    assert fake.calls == []


@pytest.mark.parametrize(("query", "field"), [("compact=maybe", "compact"), ("fields=" + "a" * 4001, "fields")])
def test_query_validation_names_the_bare_parameter(query: str, field: str) -> None:
    fake = LoadBridge()
    denied = _client(fake).post(f"{_LOAD}?{query}", json=_LOAD_BODY)
    assert denied.status_code == 400
    error = denied.json()["error"]
    assert error["field"] == field
    assert error["message"] == f"请求参数无效：{field}"
    assert fake.calls == []


def test_typed_routes_reject_fields_and_compact() -> None:
    assert {route.path for route in POST_ROUTES if not shapeable(route.out_model)} == _TYPED
    fake = FakeBridge()
    client = _client(fake)
    body = _auth(type="sale_order", id=1, action="close")
    denied = client.post("/v1/co/vouchers/close?fields=line_id", json=body)
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "fields"
    assert denied.json()["error"]["message"] == "该接口不支持 fields"
    denied = client.post("/v1/co/vouchers/close?compact=true", json=body)
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "compact"
    assert fake.calls == []
    assert client.post("/v1/co/vouchers/close?compact=false", json=body).status_code == 200


def test_every_operation_has_access_and_every_post_has_the_query_parameters() -> None:
    spec = _spec()
    for route in POST_ROUTES:
        operation = spec["paths"][route.path]["post"]
        assert operation["x-u8co-access"] == ACCESS[route.action], route.path
        names = {item["name"] for item in operation.get("parameters", []) if item.get("in") == "query"}
        assert {"fields", "compact"} <= names, route.path
    assert spec["paths"]["/v1/co/health"]["get"]["x-u8co-access"] == "read"
    assert spec["paths"]["/v1/co/meta"]["get"]["x-u8co-access"] == "read"
    methods = [op for item in spec["paths"].values() for op in item.values() if isinstance(op, dict)]
    # 经营管理路由标 mgmt（co_access.MGMT）；perm/evaluate 标 perm_evaluate。
    assert all(op.get("x-u8co-access") in ("read", "write", "mgmt", "perm_evaluate") for op in methods)
