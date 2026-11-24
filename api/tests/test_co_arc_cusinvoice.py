"""客户开票单位 archives/customer_invoice_company_add、customer_invoice_company_delete、customer_invoice_company：
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

_ADD = "/v1/co/archives/customer_invoice_company_add"
_DELETE = "/v1/co/archives/customer_invoice_company_delete"
_DEFAULT = "/v1/co/archives/customer_invoice_company"
_CODE = "C900001:C900002"
_ROWS = [{"code": "C900001", "bdefault": False}, {"code": "C900002", "bdefault": True}]


class _InvoiceBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "validate", "code": payload["code"], "before": []}
        return {
            "ok": True,
            "code": payload["code"],
            "changed": True,
            "before": [],
            "after": _ROWS,
            "note": "extra keys pass through",
        }


def _add(**over) -> dict:
    body = _auth(code=_CODE)
    body.update(over)
    return body


def _row(**over) -> dict:
    body = _auth(code=_CODE, expect={"bdefault": False})
    body.update(over)
    return body


_WRITES = (
    (_ADD, _add, "customer_invoice_company_add", "新增客户开票单位", "coArchiveCustomerInvoiceCompanyAdd"),
    (_DELETE, _row, "customer_invoice_company_delete", "删除客户开票单位", "coArchiveCustomerInvoiceCompanyDelete"),
    (_DEFAULT, _row, "customer_invoice_company", "设置客户默认开票单位", "coArchiveCustomerInvoiceCompany"),
)


@pytest.mark.parametrize(("path", "make", "op"), [(path, make, op) for path, make, op, _s, _i in _WRITES])
def test_forwards_and_returns_both_images(path: str, make, op: str) -> None:
    fake = _InvoiceBridge()
    ok = _client(fake).post(path, json=make())
    assert ok.status_code == 200, ok.text
    sent_path, sent = fake.calls[-1]
    assert sent_path == f"/v1/archives/{op}"
    assert sent["code"] == _CODE
    body = ok.json()
    assert (body["ok"], body["changed"], body["code"]) == (True, True, _CODE)
    assert (body["before"], body["after"]) == ([], _ROWS)
    assert body["note"] == "extra keys pass through"


def test_add_keeps_defaults_out_of_the_body_and_forwards_explicit_values() -> None:
    fake = _InvoiceBridge()
    assert _client(fake).post(_ADD, json=_add()).status_code == 200
    assert "default" not in fake.calls[-1][1] and "expect" not in fake.calls[-1][1]
    body = _add(default=True, expect={"customer_ufts": "7891234"})
    assert _client(fake).post(_ADD, json=body).status_code == 200
    assert fake.calls[-1][1]["default"] is True
    assert fake.calls[-1][1]["expect"] == {"customer_ufts": "7891234"}


@pytest.mark.parametrize("path", (_DELETE, _DEFAULT))
def test_expect_with_ufts_is_forwarded(path: str) -> None:
    fake = _InvoiceBridge()
    expect = {"bdefault": True, "customer_ufts": "7891234"}
    assert _client(fake).post(path, json=_row(expect=expect)).status_code == 200
    assert fake.calls[-1][1]["expect"] == expect


def test_sealed_keys_of_all_ops() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_ADD, json=_add())
        client.post(_DELETE, json=_row())
        client.post(_DEFAULT, json=_row())
        (m1, p1, raw1), (m2, p2, raw2), (m3, p3, raw3) = server.httpd.hits[-3:]
    assert [(m1, p1), (m2, p2), (m3, p3)] == [
        ("POST", "/u8co/v1/archives/customer_invoice_company_add"),
        ("POST", "/u8co/v1/archives/customer_invoice_company_delete"),
        ("POST", "/u8co/v1/archives/customer_invoice_company"),
    ]
    assert set(json.loads(raw1)) == _sealed("code", "caller", "caller_trust")
    assert set(json.loads(raw2)) == _sealed("code", "expect", "caller", "caller_trust")
    assert set(json.loads(raw3)) == _sealed("code", "expect", "caller", "caller_trust")
    assert _SECRET not in (raw1 + raw2 + raw3).decode("utf-8")


_CODE_REJECTS = ("", "C900001", "C900001:", ":C900002", "C900001: C900002", "C900001 :C900002", "C9\n:C2")
_ADD_REJECTS = (
    *(_add(code=code) for code in _CODE_REJECTS),
    _add(code="C" * 21 + ":C900002"),
    _add(code="C900001:" + "C" * 21),
    _add(default="true"),
    _add(default=1),
    _add(default=None),
    _add(expect={"bdefault": False}),
    _add(expect={"customer_ufts": "12a"}),
    _add(expect={"customer_ufts": ""}),
    _add(expect={"customer_ufts": 5}),
    _add(expect={"customer_ufts": "9" * 20}),
    _add(expect={"other": "x"}),
    _add(archive="customer"),
    _auth(),
)
_ROW_REJECTS = (
    *(_row(code=code) for code in _CODE_REJECTS),
    _row(code="C" * 21 + ":C900002"),
    _row(expect={}),
    _row(expect={"customer_ufts": "55"}),
    _row(expect={"bdefault": "false"}),
    _row(expect={"bdefault": 0}),
    _row(expect={"bdefault": None}),
    _row(expect={"bdefault": False, "customer_ufts": "12a"}),
    _row(expect={"bdefault": False, "name": "x"}),
    _row(default=True),
    _auth(code=_CODE),
    _auth(expect={"bdefault": False}),
)


@pytest.mark.parametrize("body", _ADD_REJECTS)
def test_invalid_add_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_ADD, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize("path", (_DELETE, _DEFAULT))
@pytest.mark.parametrize("body", _ROW_REJECTS)
def test_invalid_row_ops_do_not_call(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize(("path", "make", "op"), [(path, make, op) for path, make, op, _s, _i in _WRITES])
def test_audit_names_the_customer(capsys, path: str, make, op: str) -> None:
    ok = _client(_InvoiceBridge()).post(path, json=make())
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    line = audit_line(out, path)
    assert line["action"].endswith("#customer:C900001")
    assert (line["type"], line["op"]) == ("archives", op)


_ERRORS = (
    (404, "not_found"),
    (404, "invoice_company_not_found"),
    (409, "state_mismatch"),
    (409, "invoice_company_invalid"),
    (409, "invoice_company_taken"),
    (409, "invoice_company_is_default"),
    (504, "outcome_unknown"),
)


@pytest.mark.parametrize(("status", "code"), _ERRORS)
@pytest.mark.parametrize(("path", "make"), [(path, make) for path, make, _o, _s, _i in _WRITES])
def test_bridge_errors_pass_through(path: str, make, status: int, code: str) -> None:
    fake = _InvoiceBridge()
    fake.error = ApiError(status, code, "桥的中文说明")
    denied = _client(fake).post(path, json=make())
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


@pytest.mark.parametrize(("path", "make"), [(path, make) for path, make, _o, _s, _i in _WRITES])
def test_dry_run_is_forwarded_as_validate(path: str, make) -> None:
    fake = _InvoiceBridge()
    ok = _client(fake).post(path, json=make(dry_run=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    data = ok.json()
    assert (data["dry_run"], data["mode"]) == (True, "validate")


def test_openapi_lists_the_writes() -> None:
    paths = _spec()["paths"]
    words = {
        _ADD: ("sa_invoicecustomers", "AS011", "customer_invoice_company_add", "invoice_company_taken", "pubufts"),
        _DELETE: (
            "customer_invoice_company_delete",
            "invoice_company_is_default",
            "invoice_company_not_found",
            "bdefault",
        ),
        _DEFAULT: ("customer_invoice_company", "bdefault", "invoice_company_invalid", "customer_ufts"),
    }
    for path, _make, _op, summary, operation_id in _WRITES:
        post = paths[path]["post"]
        assert (post["summary"], post["operationId"], post["tags"]) == (summary, operation_id, ["基础档案"])
        assert "权限：写" in post["description"]
        for word in words[path]:
            assert word in post["description"]
