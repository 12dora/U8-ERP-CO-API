"""客户字段 /v1/co/archives/customer_fields：请求校验、转发给桥的字段、响应放行、审计和 OpenAPI。
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

_PATH = "/v1/co/archives/customer_fields"
_BRIDGE = "/v1/archives/customer_fields"
_CODE = "C900001"
_FIELDS = {"abbrname": "示例简称", "district": "33", "end_date": "2026-10-20"}
_EXPECT = {"abbrname": "示例客户有限公司", "district": "", "end_date": ""}


class _FieldsBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        before = {"abbrname": "示例客户有限公司", "district": "", "end_date": ""}
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "validate", "code": payload["code"], "before": before}
        return {
            "ok": True,
            "code": payload["code"],
            "changed": True,
            "before": before,
            "after": {**before, **payload["fields"]},
        }


def _body(**over) -> dict:
    body = _auth(code=_CODE, fields=dict(_FIELDS))
    body.update(over)
    return body


def test_customer_fields_forwards_and_keeps_empty_strings() -> None:
    fake = _FieldsBridge()
    ok = _client(fake).post(_PATH, json=_body(expect=dict(_EXPECT)))
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert sent["code"] == _CODE
    assert sent["fields"] == _FIELDS
    assert sent["expect"] == _EXPECT
    assert "dry_run" not in sent
    body = ok.json()
    assert body["ok"] is True
    assert body["changed"] is True
    assert body["before"] == _EXPECT
    assert body["after"] == _FIELDS


def test_expect_is_optional() -> None:
    fake = _FieldsBridge()
    ok = _client(fake).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    assert "expect" not in fake.calls[-1][1]


def test_clearing_values_are_accepted() -> None:
    fields = {"district": "", "end_date": ""}
    fake = _FieldsBridge()
    ok = _client(fake).post(_PATH, json=_body(fields=fields, expect={"district": "33", "end_date": "2026-10-20"}))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["fields"] == fields


def test_customer_fields_sends_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PATH, json=_body(expect=dict(_EXPECT)))
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(sent) == _sealed("code", "fields", "expect", "caller")
    assert _SECRET not in raw.decode("utf-8")


_REJECTS = (
    _body(fields={}),
    _body(fields={"cusname": "x"}),
    _body(fields={"abbrname": "x", "bank": "y"}),
    _body(fields={"abbrname": ""}),
    _body(fields={"abbrname": "   "}),
    _body(fields={"abbrname": "甲" * 61}),
    _body(fields={"abbrname": "甲\n乙"}),
    _body(fields={"abbrname": None}),
    _body(fields={"district": "1" * 13}),
    _body(fields={"district": " 33"}),
    _body(fields={"district": 33}),
    _body(fields={"end_date": "2026-13-01"}),
    _body(fields={"end_date": "2026-02-30"}),
    _body(fields={"end_date": "2026/10/20"}),
    _body(fields={"end_date": "20261020"}),
    _body(fields={"end_date": "2026-10-20 08:00"}),
    _body(fields="abbrname"),
    _body(expect={"district": ""}, fields={"abbrname": "x"}),
    _body(expect={"abbrname": "x" * 61}),
    _body(expect={"end_date": "2026/10/20"}),
    _body(expect={"abbrname": None}),
    _body(expect=[]),
    _body(code=""),
    _body(code="C" * 31),
    _auth(fields=dict(_FIELDS)),
    _auth(code=_CODE),
    _body(archive="customer"),
    _body(person="EX040001"),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_audit_names_the_customer(capsys) -> None:
    ok = _client(_FieldsBridge()).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    line = audit_line(out, _PATH)
    assert line["action"].endswith("#customer:C900001")
    assert (line["type"], line["op"]) == ("archives", "customer_fields")


@pytest.mark.parametrize(
    ("status", "code"),
    (
        (404, "not_found"),
        (409, "state_mismatch"),
        (409, "district_invalid"),
        (400, "bad_request"),
        (504, "outcome_unknown"),
    ),
)
def test_bridge_errors_pass_through(status: int, code: str) -> None:
    fake = _FieldsBridge()
    fake.error = ApiError(status, code, "桥的中文说明")
    denied = _client(fake).post(_PATH, json=_body())
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_dry_run_is_forwarded_as_validate() -> None:
    fake = _FieldsBridge()
    ok = _client(fake).post(_PATH, json=_body(dry_run=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    data = ok.json()
    assert data["dry_run"] is True
    assert data["mode"] == "validate"
    assert data["code"] == _CODE


def test_openapi_lists_the_write() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "修改客户简称、地区分类、停用日期"
    assert operation["operationId"] == "coArchiveCustomerFields"
    assert operation["tags"] == ["基础档案"]
    description = operation["description"]
    assert "权限：写" in description
    for word in ("cCusAbbName", "cDCCode", "dEndDate", "AS011", "customer_fields", "district_invalid", "expect"):
        assert word in description
