"""客户专管业务员 /v1/co/archives/customer_person：请求校验、转发给桥的字段、响应放行、审计和 OpenAPI。
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

_PATH = "/v1/co/archives/customer_person"
_BRIDGE = "/v1/archives/customer_person"
_CODE = "C900001"
_PERSON = "EX040001"


class _PersonBridge(FakeBridge):
    note: str | None = None

    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            return {
                "ok": True,
                "dry_run": True,
                "mode": "validate",
                "code": payload["code"],
                "before": None,
                "dept_before": None,
                "dept_note": self.note,
            }
        return {
            "ok": True,
            "code": payload["code"],
            "before": None,
            "after": payload["person"],
            "dept_before": None,
            "dept_after": None if self.note else "D901",
            "dept_note": self.note,
        }


def _body(**over) -> dict:
    body = _auth(code=_CODE, person=_PERSON)
    body.update(over)
    return body


def test_customer_person_forwards_and_keeps_null_before() -> None:
    fake = _PersonBridge()
    ok = _client(fake).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert (sent["code"], sent["person"]) == (_CODE, _PERSON)
    assert "expect" not in sent
    assert "archive" not in sent
    body = ok.json()
    assert body["ok"] is True
    assert body["code"] == _CODE
    assert body["before"] is None
    assert body["after"] == _PERSON
    assert body["dept_before"] is None
    assert body["dept_after"] == "D901"
    assert "dept_note" in body
    assert body["dept_note"] is None


def test_dept_note_is_forwarded() -> None:
    fake = _PersonBridge()
    fake.note = "分管部门不是末级：D901"
    ok = _client(fake).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["dept_note"] == "分管部门不是末级：D901"
    assert body["dept_after"] is None


def test_expect_empty_string_is_forwarded() -> None:
    fake = _PersonBridge()
    ok = _client(fake).post(_PATH, json=_body(expect=""))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["expect"] == ""


def test_expect_value_is_forwarded() -> None:
    fake = _PersonBridge()
    ok = _client(fake).post(_PATH, json=_body(expect="EX040002"))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["expect"] == "EX040002"


def test_customer_person_sends_exact_sealed_keys() -> None:
    with _Up() as server:
        client = _wired(server.base_url)
        client.post(_PATH, json=_body())
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(sent) == _sealed("code", "person", "caller")
    assert _SECRET not in raw.decode("utf-8")


_REJECTS = (
    _body(person=""),
    _body(person="E" * 21),
    _body(code=""),
    _body(code="C" * 31),
    _body(expect="E" * 21),
    _body(expect=" EX040001"),
    _body(expect="EX040001 "),
    _auth(person=_PERSON),
    _auth(code=_CODE),
    _body(archive="customer"),
    _body(fields={"spec_operator": _PERSON}),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_audit_names_the_customer(capsys) -> None:
    ok = _client(_PersonBridge()).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    line = audit_line(out, _PATH)
    assert line["action"].endswith("#customer:C900001")
    assert (line["type"], line["op"]) == ("archives", "customer_person")


def test_bridge_conflict_passes_through() -> None:
    fake = _PersonBridge()
    fake.error = ApiError(409, "state_mismatch", "expect 与当前值不符")
    denied = _client(fake).post(_PATH, json=_body(expect="EX040002"))
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "state_mismatch"


def test_not_found_passes_through() -> None:
    fake = _PersonBridge()
    fake.error = ApiError(404, "not_found", "客户不存在")
    denied = _client(fake).post(_PATH, json=_body())
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "not_found"


def test_outcome_unknown_passes_through() -> None:
    fake = _PersonBridge()
    fake.error = ApiError(504, "outcome_unknown", "写后回读不一致")
    denied = _client(fake).post(_PATH, json=_body())
    assert denied.status_code == 504
    assert denied.json()["error"]["code"] == "outcome_unknown"


def test_dry_run_is_forwarded_as_validate() -> None:
    fake = _PersonBridge()
    ok = _client(fake).post(_PATH, json=_body(dry_run=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    data = ok.json()
    assert data["dry_run"] is True
    assert data["mode"] == "validate"
    assert data["code"] == _CODE
    assert data["before"] is None
    assert "dept_note" in data


def test_openapi_lists_the_write() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "修改客户专管业务员"
    assert operation["operationId"] == "coArchiveCustomerPerson"
    assert operation["tags"] == ["基础档案"]
    assert "权限：写" in operation["description"]
    assert "cCusPPerson" in operation["description"]
    assert "AS011" in operation["description"]
    assert "customer_person" in operation["description"]
    assert "Customer_Auth" in operation["description"]
    assert "dept_note" in operation["description"]
