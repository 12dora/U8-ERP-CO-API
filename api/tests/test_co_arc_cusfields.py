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
from u8co_api.co_bridge import to_api_error
from u8co_api.co_models_arc_cusfields import FIELD_KEYS
from u8co_api.errors import ApiError

_PATH = "/v1/co/archives/customer_fields"
_BRIDGE = "/v1/archives/customer_fields"
_CODE = "C900001"
_FIELDS = {"abbrname": "示例简称", "district": "33", "end_date": "2026-10-20", "sa_protocol": "A001"}
_EXPECT = {"abbrname": "示例客户有限公司", "district": "", "end_date": "", "sa_protocol": ""}


class _FieldsBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        before = dict(_EXPECT)
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


def test_sa_protocol_alone_and_clearing_are_forwarded() -> None:
    for fields, expect in (({"sa_protocol": "A001"}, {"sa_protocol": ""}), ({"sa_protocol": ""}, {"sa_protocol": "A001"})):
        fake = _FieldsBridge()
        ok = _client(fake).post(_PATH, json=_body(fields=fields, expect=expect))
        assert ok.status_code == 200, ok.text
        _path, sent = fake.calls[-1]
        assert sent["fields"] == fields
        assert sent["expect"] == expect
        assert set(ok.json()["before"]) == {"abbrname", "district", "end_date", "sa_protocol"}


def test_name_and_tax_no_are_forwarded() -> None:
    assert FIELD_KEYS == ("abbrname", "district", "end_date", "sa_protocol", "name", "tax_no")
    cases = (
        ({"name": "示例客户有限公司"}, {"name": "旧名称"}),
        ({"name": " 示例客户 ", "tax_no": "EXAMPLE-TAX-NO-001"}, {"name": "", "tax_no": ""}),
        ({"tax_no": ""}, {"tax_no": "EXAMPLE-TAX-NO-001"}),
        ({"name": "名" * 98, "tax_no": "9" * 50}, None),
    )
    for fields, expect in cases:
        fake = _FieldsBridge()
        body = _body(fields=fields) if expect is None else _body(fields=fields, expect=expect)
        ok = _client(fake).post(_PATH, json=body)
        assert ok.status_code == 200, ok.text
        _path, sent = fake.calls[-1]
        assert sent["fields"] == fields  # 首尾空白原样转给桥，由桥去掉
        assert sent.get("expect") == expect


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
    _body(fields={"sa_protocol": "A" * 21}),
    _body(fields={"sa_protocol": " A001"}),
    _body(fields={"sa_protocol": "A001 "}),
    _body(fields={"sa_protocol": "A\n001"}),
    _body(fields={"sa_protocol": 1}),
    _body(fields={"sa_protocol": None}),
    _body(fields={"end_date": "2026-13-01"}),
    _body(fields={"end_date": "2026-02-30"}),
    _body(fields={"end_date": "2026/10/20"}),
    _body(fields={"end_date": "20261020"}),
    _body(fields={"end_date": "2026-10-20 08:00"}),
    _body(fields={"name": ""}),
    _body(fields={"name": "  "}),
    _body(fields={"name": "名" * 99}),
    _body(fields={"name": "示例\n客户"}),
    _body(fields={"name": None}),
    _body(fields={"Name": "示例"}),
    _body(fields={"tax_no": "9" * 51}),
    _body(fields={"tax_no": "91\t00"}),
    _body(fields={"tax_no": 91}),
    _body(fields={"name": "示例"}, expect={"name": "名" * 99}),
    _body(fields={"name": "示例"}, expect={"tax_no": ""}),
    _body(fields={"tax_no": "91"}, expect={"tax_no": "9" * 51}),
    _body(fields="abbrname"),
    _body(expect={"district": ""}, fields={"abbrname": "x"}),
    _body(expect={"abbrname": "x" * 61}),
    _body(expect={"end_date": "2026/10/20"}),
    _body(fields={"sa_protocol": "A001"}, expect={"sa_protocol": "A" * 21}),
    _body(fields={"sa_protocol": "A001"}, expect={"sa_protocol": " A001"}),
    _body(fields={"sa_protocol": "A001"}, expect={"sa_protocol": "A001 "}),
    _body(fields={"sa_protocol": "A001"}, expect={"sa_protocol": "A\n001"}),
    _body(fields={"district": "33"}, expect={"district": "33 "}),
    _body(fields={"district": "33"}, expect={"district": "3\t3"}),
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
        (409, "sa_protocol_invalid"),
        (409, "abbrname_taken"),
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


def test_state_mismatch_current_reaches_the_caller() -> None:
    current = {"name": "示例客户有限公司", "tax_no": ""}
    bridge = {
        "ok": False,
        "code": "state_mismatch",
        "message": "与 expect 不符：name 当前是示例客户有限公司",
        "detail": {"current": current},
    }
    error = to_api_error(409, bridge)
    assert error.detail == {"current": current}
    fake = _FieldsBridge()
    fake.error = error
    denied = _client(fake).post(_PATH, json=_body(fields={"name": "新名称", "tax_no": "91"}, expect={"name": "旧名称"}))
    assert denied.status_code == 409
    wire = denied.json()["error"]
    assert (wire["code"], wire["message"]) == ("state_mismatch", "与 expect 不符：name 当前是示例客户有限公司")
    assert wire["detail"]["current"] == current


def test_abbrname_taken_keeps_status_and_field() -> None:
    bridge = {"ok": False, "code": "abbrname_taken", "message": "简称已被其他客户使用：示例简称", "field": "fields.abbrname"}
    error = to_api_error(409, bridge)
    assert (error.status, error.code, error.field) == (409, "abbrname_taken", "fields.abbrname")
    assert error.detail is None
    fake = _FieldsBridge()
    fake.error = error
    denied = _client(fake).post(_PATH, json=_body(fields={"abbrname": "示例简称"}))
    assert denied.status_code == 409
    wire = denied.json()["error"]
    assert (wire["code"], wire["field"]) == ("abbrname_taken", "fields.abbrname")


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
    assert operation["summary"] == "修改客户名称、简称、税号、地区分类、停用日期、销售默认收付款协议"
    assert operation["operationId"] == "coArchiveCustomerFields"
    assert operation["tags"] == ["基础档案"]
    description = operation["description"]
    assert "权限：写" in description
    words = ("cCusAbbName", "cDCCode", "dEndDate", "cCusSAProtocol", "cCusName", "cCusRegCode", "AS011", "customer_fields")
    for word in (*words, "district_invalid", "sa_protocol_invalid", "abbrname_taken", "detail.current", "expect"):
        assert word in description
