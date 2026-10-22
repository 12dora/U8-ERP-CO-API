"""客户收货地址新增 / 删除 /v1/co/archives/customer_address_add、customer_address_delete：
请求校验、转发给桥的字段、响应放行、审计和 OpenAPI。假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_update_close_gen import _spec
from u8co_api.errors import ApiError

_ADD = "/v1/co/archives/customer_address_add"
_DELETE = "/v1/co/archives/customer_address_delete"
_CODE = "C900001"
_FIELDS = {"name": "示例车间二", "unit": "张三", "default": True}
_EXPECT = {"name": "示例车间二", "unit": "张三", "default": True}
_ROW = {"code": "02", "name": "示例车间二", "unit": "张三", "default": True}


class _RowsBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "validate", "code": payload["code"], "before": []}
        added = path.endswith("_add")
        return {
            "ok": True,
            "code": payload["code"],
            "address": payload["address"],
            "changed": True,
            "before": [] if added else [_ROW],
            "after": [_ROW] if added else [],
            "note": "extra keys pass through",
        }


def _add(**over) -> dict:
    body = _auth(code=_CODE, address="02", fields=dict(_FIELDS))
    body.update(over)
    return body


def _drop(**over) -> dict:
    body = _auth(code=_CODE, address="02", expect=dict(_EXPECT))
    body.update(over)
    return body


def test_add_forwards_and_returns_both_images() -> None:
    fake = _RowsBridge()
    ok = _client(fake).post(_ADD, json=_add())
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == "/v1/archives/customer_address_add"
    assert (sent["code"], sent["address"]) == (_CODE, "02")
    assert sent["fields"] == _FIELDS
    body = ok.json()
    assert (body["ok"], body["changed"], body["address"]) == (True, True, "02")
    assert body["before"] == []
    assert body["after"] == [_ROW]
    assert body["note"] == "extra keys pass through"


def test_add_name_only_keeps_defaults_out_of_the_body() -> None:
    fake = _RowsBridge()
    ok = _client(fake).post(_ADD, json=_add(fields={"name": "示例车间二"}))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["fields"] == {"name": "示例车间二"}


def test_add_empty_unit_and_false_default_survive() -> None:
    fake = _RowsBridge()
    fields = {"name": "示例车间二", "unit": "", "default": False}
    assert _client(fake).post(_ADD, json=_add(fields=fields)).status_code == 200
    assert fake.calls[-1][1]["fields"] == fields


def test_delete_forwards_and_returns_both_images() -> None:
    fake = _RowsBridge()
    ok = _client(fake).post(_DELETE, json=_drop())
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == "/v1/archives/customer_address_delete"
    assert sent["expect"] == _EXPECT
    body = ok.json()
    assert (body["changed"], body["before"], body["after"]) == (True, [_ROW], [])


def test_delete_accepts_empty_current_values() -> None:
    fake = _RowsBridge()
    expect = {"name": "", "unit": "", "default": False}
    assert _client(fake).post(_DELETE, json=_drop(expect=expect)).status_code == 200
    assert fake.calls[-1][1]["expect"] == expect


def test_sealed_keys_of_both_ops() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_ADD, json=_add())
        client.post(_DELETE, json=_drop())
        (m1, p1, raw1), (m2, p2, raw2) = server.httpd.hits[-2:]
    assert (m1, p1) == ("POST", "/u8co/v1/archives/customer_address_add")
    assert (m2, p2) == ("POST", "/u8co/v1/archives/customer_address_delete")
    assert set(json.loads(raw1)) == _sealed("code", "address", "fields", "caller")
    assert set(json.loads(raw2)) == _sealed("code", "address", "expect", "caller")
    assert _SECRET not in raw1.decode("utf-8") + raw2.decode("utf-8")


_ADD_REJECTS = (
    _add(fields={}),
    _add(fields={"unit": "张三"}),
    _add(fields={"name": ""}),
    _add(fields={"name": "   "}),
    _add(fields={"name": "三" * 256}),
    _add(fields={"name": "三\n车间"}),
    _add(fields={"name": None}),
    _add(fields={"name": 1}),
    _add(fields={"name": "x", "unit": "张" * 256}),
    _add(fields={"name": "x", "unit": None}),
    _add(fields={"name": "x", "default": "true"}),
    _add(fields={"name": "x", "default": 1}),
    _add(fields={"name": "x", "code": "03"}),
    _add(fields={"name": "x", "bdefault": True}),
    _add(address=""),
    _add(address=" 02"),
    _add(address="02 "),
    _add(address="A" * 31),
    _add(address="0\n2"),
    _add(code=""),
    _add(code="C" * 31),
    _add(expect={"name": "x"}),
    _add(archive="customer"),
    _auth(address="02", fields={"name": "x"}),
    _auth(code=_CODE, fields={"name": "x"}),
    _auth(code=_CODE, address="02"),
)

_DELETE_REJECTS = (
    _drop(expect={}),
    _drop(expect={"name": "x", "unit": ""}),
    _drop(expect={"name": "x", "default": False}),
    _drop(expect={"unit": "", "default": False}),
    _drop(expect={"name": "x", "unit": "", "default": False, "code": "02"}),
    _drop(expect={"name": 1, "unit": "", "default": False}),
    _drop(expect={"name": "x", "unit": None, "default": False}),
    _drop(expect={"name": "x", "unit": "", "default": "false"}),
    _drop(expect={"name": "x", "unit": "", "default": 0}),
    _drop(expect={"name": "x" * 256, "unit": "", "default": False}),
    _drop(expect={"name": "x\n", "unit": "", "default": False}),
    _drop(address=""),
    _drop(address="A" * 31),
    _drop(address="0\n2"),
    _drop(code=""),
    _drop(fields={"name": "x"}),
    _auth(address="02", expect=dict(_EXPECT)),
    _auth(code=_CODE, expect=dict(_EXPECT)),
    _auth(code=_CODE, address="02"),
)


@pytest.mark.parametrize("body", _ADD_REJECTS)
def test_invalid_add_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_ADD, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize("body", _DELETE_REJECTS)
def test_invalid_delete_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_DELETE, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize(("path", "body", "op"), ((_ADD, _add(), "add"), (_DELETE, _drop(), "delete")))
def test_audit_names_the_customer(capsys, path: str, body: dict, op: str) -> None:
    ok = _client(_RowsBridge()).post(path, json=body)
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    line = audit_line(out, path)
    assert line["action"].endswith("#customer:C900001")
    assert (line["type"], line["op"]) == ("archives", f"customer_address_{op}")


_ERRORS = (
    (404, "not_found"),
    (404, "address_not_found"),
    (409, "state_mismatch"),
    (409, "address_code_taken"),
    (409, "address_name_taken"),
    (409, "address_in_use"),
    (409, "address_is_default"),
    (504, "outcome_unknown"),
)


@pytest.mark.parametrize(("status", "code"), _ERRORS)
@pytest.mark.parametrize(("path", "body"), ((_ADD, _add()), (_DELETE, _drop())))
def test_bridge_errors_pass_through(path: str, body: dict, status: int, code: str) -> None:
    fake = _RowsBridge()
    fake.error = ApiError(status, code, "桥的中文说明")
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


@pytest.mark.parametrize(("path", "body"), ((_ADD, _add(dry_run=True)), (_DELETE, _drop(dry_run=True))))
def test_dry_run_is_forwarded_as_validate(path: str, body: dict) -> None:
    fake = _RowsBridge()
    ok = _client(fake).post(path, json=body)
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    data = ok.json()
    assert (data["dry_run"], data["mode"]) == (True, "validate")


def test_openapi_lists_both_writes() -> None:
    paths = _spec()["paths"]
    add = paths[_ADD]["post"]
    assert add["summary"] == "新增客户收货地址"
    assert (add["operationId"], add["tags"]) == ("coArchiveCustomerAddressAdd", ["基础档案"])
    delete = paths[_DELETE]["post"]
    assert (delete["summary"], delete["operationId"]) == ("删除客户收货地址", "coArchiveCustomerAddressDelete")
    for op, words in (
        (add, ("CusDeliverAdd", "AS011", "customer_address_add", "address_code_taken", "address_name_taken")),
        (delete, ("customer_address_delete", "address_in_use", "address_is_default", "address_not_found", "expect")),
    ):
        assert "权限：写" in op["description"]
        for word in words:
            assert word in op["description"]
