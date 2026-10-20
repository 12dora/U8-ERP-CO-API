"""客户收货地址 /v1/co/archives/customer_address：请求校验、转发给桥的字段、响应放行、审计和 OpenAPI。
假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_update_close_gen import _spec
from u8co_api.errors import ApiError

_PATH = "/v1/co/archives/customer_address"
_BRIDGE = "/v1/archives/customer_address"
_CODE = "C900001"
_FIELDS = {"code": "01", "name": "示例车间", "unit": "张三", "default": True}
_EXPECT = {"name": "3车间", "unit": "", "default": False}
_ROW = {"code": "1", "name": "3车间", "unit": "", "default": False}


class _AddressBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "validate", "code": payload["code"], "before": [_ROW]}
        new = payload["fields"].get("code", payload["address"])
        after = {"code": new, "name": "示例车间", "unit": "张三", "default": True}
        return {
            "ok": True,
            "code": payload["code"],
            "address": payload["address"],
            "new_address": new,
            "changed": True,
            "before": [_ROW],
            "after": [after],
        }


def _body(**over) -> dict:
    body = _auth(code=_CODE, address="1", fields=dict(_FIELDS), expect=dict(_EXPECT))
    body.update(over)
    return body


def test_customer_address_forwards_and_returns_both_images() -> None:
    fake = _AddressBridge()
    ok = _client(fake).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert (sent["code"], sent["address"]) == (_CODE, "1")
    assert sent["fields"] == _FIELDS
    assert sent["expect"] == _EXPECT
    body = ok.json()
    assert body["ok"] is True
    assert (body["address"], body["new_address"], body["changed"]) == ("1", "01", True)
    assert body["before"] == [_ROW]
    assert body["after"][0]["default"] is True


def test_empty_strings_and_false_survive() -> None:
    fake = _AddressBridge()
    fields = {"name": "", "unit": "", "default": False}
    expect = {"name": "示例车间", "unit": "张三", "default": True}
    ok = _client(fake).post(_PATH, json=_body(fields=fields, expect=expect))
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert sent["fields"] == fields
    assert sent["expect"] == expect


def test_code_only_needs_no_expect() -> None:
    fake = _AddressBridge()
    body = _auth(code=_CODE, address="1", fields={"code": "01"})
    ok = _client(fake).post(_PATH, json=body)
    assert ok.status_code == 200, ok.text
    assert "expect" not in fake.calls[-1][1]


def test_customer_address_sends_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PATH, json=_body())
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(sent) == _sealed("code", "address", "fields", "expect", "caller")
    assert _SECRET not in raw.decode("utf-8")


_REJECTS = (
    _body(fields={}),
    _body(fields={"cusname": "x"}),
    _body(fields={"bdefault": True}),
    _body(fields={"code": ""}, expect=None),
    _body(fields={"code": " 01"}, expect=None),
    _body(fields={"code": "01 "}, expect=None),
    _body(fields={"code": "A" * 31}, expect=None),
    _body(fields={"code": 1}, expect=None),
    _body(fields={"code": "0\n1"}, expect=None),
    _body(fields={"name": "三" * 256}, expect={"name": "x"}),
    _body(fields={"name": "三\n车间"}, expect={"name": "x"}),
    _body(fields={"unit": "张" * 256}, expect={"unit": "x"}),
    _body(fields={"unit": None}, expect={"unit": "x"}),
    _body(fields={"default": "true"}, expect={"default": False}),
    _body(fields={"default": 1}, expect={"default": False}),
    _body(fields={"name": "示例车间"}, expect=None),
    _body(fields={"name": "示例车间"}, expect={}),
    _body(fields={"name": "示例车间", "unit": "张三"}, expect={"name": "3车间"}),
    _body(fields={"default": True}, expect={"name": "3车间"}),
    _body(fields={"code": "01"}, expect={"name": "3车间"}),
    _body(fields={"name": "示例车间"}, expect={"name": "3车间", "code": "1"}),
    _body(fields={"name": "示例车间"}, expect={"name": 1}),
    _body(fields={"default": True}, expect={"default": "false"}),
    _body(fields={"name": "示例车间"}, expect={"name": "x" * 256}),
    _body(address=""),
    _body(address="A" * 31),
    _body(address="1\n"),
    _body(code=""),
    _body(code="C" * 31),
    _auth(address="1", fields={"name": "x"}, expect={"name": "y"}),
    _auth(code=_CODE, fields={"name": "x"}, expect={"name": "y"}),
    _auth(code=_CODE, address="1", expect={"name": "y"}),
    _body(archive="customer"),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_audit_names_the_customer(capsys) -> None:
    ok = _client(_AddressBridge()).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    line = audit_line(out, _PATH)
    assert line["action"].endswith("#customer:C900001")
    assert (line["type"], line["op"]) == ("archives", "customer_address")


@pytest.mark.parametrize(
    ("status", "code"),
    (
        (404, "not_found"),
        (404, "address_not_found"),
        (409, "state_mismatch"),
        (409, "address_code_taken"),
        (409, "address_in_use"),
        (504, "outcome_unknown"),
    ),
)
def test_bridge_errors_pass_through(status: int, code: str) -> None:
    fake = _AddressBridge()
    fake.error = ApiError(status, code, "桥的中文说明")
    denied = _client(fake).post(_PATH, json=_body())
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_dry_run_is_forwarded_as_validate() -> None:
    fake = _AddressBridge()
    ok = _client(fake).post(_PATH, json=_body(dry_run=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    data = ok.json()
    assert data["dry_run"] is True
    assert data["mode"] == "validate"
    assert data["before"] == [_ROW]


def test_openapi_lists_the_write() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "修改客户收货地址"
    assert operation["operationId"] == "coArchiveCustomerAddress"
    assert operation["tags"] == ["基础档案"]
    description = operation["description"]
    assert "权限：写" in description
    for word in ("CusDeliverAdd", "AS011", "customer_address", "address_in_use", "address_code_taken", "expect"):
        assert word in description
