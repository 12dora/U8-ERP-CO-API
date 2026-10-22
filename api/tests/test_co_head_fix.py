"""历史单据表头修正 /v1/co/vouchers/head_fix：请求校验、转发给桥的字段、响应放行、审计和 OpenAPI。
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

_PATH = "/v1/co/vouchers/head_fix"
_BRIDGE = "/v1/vouchers/head_fix"
_FIELDS = {"addr_code": "02", "person_code": "P900002"}
_EXPECT = {"addr_code": "01", "person_code": "P900001"}
_UNDO = {
    "addr_code": {"rdrecord32": [900000002], "SaleBillVouch": [900000003]},
    "person_code": {"rdrecord32": [900000002], "SaleBillVouch": [900000003], "Ar_Detail": [11, 12]},
}
_DOWN = [
    {"table": "rdrecord32", "id": 900000002, "code": "XS0001", "action": "updated"},
    {"table": "SaleBillVouch", "id": 900000003, "code": "SO0001", "action": "skipped", "reason": "shared"},
    {"table": "Ar_Detail", "invoice": "SO0002", "rows": 4, "action": "updated"},
]


class _FixBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "validate", "type": "dispatch", "id": payload["id"]}
        return {
            "ok": True,
            "type": "dispatch",
            "id": payload["id"],
            "code": "FH0001",
            "changed": True,
            "before": dict(payload["expect"]),
            "after": dict(payload["fields"]),
            "downstream": list(_DOWN),
            "undo": _UNDO if payload.get("undo") is None else {},
        }


def _body(**over) -> dict:
    body = _auth(type="dispatch", id=1000000001, fields=dict(_FIELDS), expect=dict(_EXPECT))
    body.update(over)
    return body


def test_head_fix_forwards_and_returns_downstream() -> None:
    fake = _FixBridge()
    ok = _client(fake).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert (sent["type"], sent["id"]) == ("dispatch", 1000000001)
    assert sent["fields"] == _FIELDS
    assert sent["expect"] == _EXPECT
    body = ok.json()
    assert (body["ok"], body["changed"], body["code"]) == (True, True, "FH0001")
    assert body["before"] == _EXPECT
    assert body["after"] == _FIELDS
    assert body["downstream"] == _DOWN


def test_undo_is_forwarded_and_returned() -> None:
    fake = _FixBridge()
    ok = _client(fake).post(_PATH, json=_body(undo=_UNDO))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["undo"] == _UNDO
    assert ok.json()["undo"] == {}
    plain = _client(fake).post(_PATH, json=_body())
    assert "undo" not in fake.calls[-1][1]
    assert plain.json()["undo"] == _UNDO


def test_undo_may_cover_only_some_fields_and_tables() -> None:
    fake = _FixBridge()
    undo = {"person_code": {"Ar_Detail": [5]}}
    assert _client(fake).post(_PATH, json=_body(undo=undo)).status_code == 200
    assert fake.calls[-1][1]["undo"] == undo
    assert _client(fake).post(_PATH, json=_body(undo={})).status_code == 200
    assert fake.calls[-1][1]["undo"] == {}


def test_already_new_reason_passes_through() -> None:
    class _Bridge(_FixBridge):
        def call(self, path: str, payload: dict) -> dict:
            data = super().call(path, payload)
            data["downstream"] = [{"table": "rdrecord32", "id": 1, "action": "skipped", "reason": "already_new"}]
            return data

    ok = _client(_Bridge()).post(_PATH, json=_body())
    assert ok.json()["downstream"][0]["reason"] == "already_new"


def test_single_field_is_enough() -> None:
    fake = _FixBridge()
    body = _body(fields={"person_code": "P900002"}, expect={"person_code": "P900001"})
    assert _client(fake).post(_PATH, json=body).status_code == 200
    assert fake.calls[-1][1]["fields"] == {"person_code": "P900002"}


def test_addr_code_may_be_cleared_and_expect_may_be_empty() -> None:
    fake = _FixBridge()
    # 回滚已补填的编码：addr_code 写空串清空；expect 空串表示当前为空。
    clear = _body(fields={"addr_code": ""}, expect={"addr_code": "02"})
    assert _client(fake).post(_PATH, json=clear).status_code == 200
    assert fake.calls[-1][1]["fields"] == {"addr_code": ""}
    fill = _body(fields=dict(_FIELDS), expect={"addr_code": "", "person_code": ""})
    assert _client(fake).post(_PATH, json=fill).status_code == 200
    assert fake.calls[-1][1]["expect"] == {"addr_code": "", "person_code": ""}


def test_sealed_keys() -> None:
    with _Up() as server:
        _wired(server.base_url).post(_PATH, json=_body())
        method, seen, raw = server.httpd.hits[-1]
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(json.loads(raw)) == _sealed("type", "id", "fields", "expect", "caller")
    assert _SECRET not in raw.decode("utf-8")


_REJECTS = (
    _body(type="sale_order"),
    _body(type="dispatch_red"),
    _body(type=None),
    _body(id=0),
    _body(id=-1),
    _body(id=2147483648),
    _body(fields={}, expect={}),
    _body(fields={"cMemo": "x"}, expect={"cMemo": "y"}),
    _body(fields={"addr_code": "02", "department": "D901"}, expect={"addr_code": "01", "department": "D902"}),
    _body(fields={"person_code": ""}, expect={"person_code": "P900001"}),
    _body(fields={"person_code": "P" * 21}, expect={"person_code": "P900001"}),
    _body(fields={"person_code": " P900002"}, expect={"person_code": "P900001"}),
    _body(fields={"person_code": "P900002 "}, expect={"person_code": "P900001"}),
    _body(fields={"person_code": "P9\n2"}, expect={"person_code": "P900001"}),
    _body(fields={"addr_code": "A" * 31}, expect={"addr_code": "01"}),
    _body(fields={"addr_code": " 02"}, expect={"addr_code": "01"}),
    _body(fields={"addr_code": "02 "}, expect={"addr_code": "01"}),
    _body(fields={"addr_code": "0\n2"}, expect={"addr_code": "01"}),
    _body(fields={"addr_code": 2}, expect={"addr_code": "01"}),
    _body(fields={"addr_code": None}, expect={"addr_code": "01"}),
    _body(fields={"addr_code": "02"}, expect={}),
    _body(fields={"addr_code": "02", "person_code": "P900002"}, expect={"addr_code": "01"}),
    _body(fields={"addr_code": "02"}, expect={"addr_code": "01", "person_code": "P900001"}),
    _body(fields={"addr_code": "02"}, expect={"person_code": "P900001"}),
    _body(fields={"addr_code": "02"}, expect={"addr_code": 1}),
    _body(fields={"addr_code": "02"}, expect={"addr_code": None}),
    _body(fields={"addr_code": "02"}, expect={"addr_code": "A" * 31}),
    _body(fields={"addr_code": "02"}, expect={"addr_code": "0\n1"}),
    _body(undo={"person_code": {"rdrecord32": [1]}}, fields={"addr_code": "02"}, expect={"addr_code": "01"}),
    _body(undo={"cMemo": {"rdrecord32": [1]}}),
    _body(undo={"addr_code": {"Ar_Detail": [1]}}),
    _body(undo={"addr_code": {"SaleBillVouch": [1], "rdrecord16": [1]}}),
    _body(undo={"addr_code": {"rdrecord32": [0]}}),
    _body(undo={"addr_code": {"rdrecord32": [-1]}}),
    _body(undo={"addr_code": {"rdrecord32": [2147483648]}}),
    _body(undo={"addr_code": {"rdrecord32": ["1"]}}),
    _body(undo={"addr_code": {"rdrecord32": [True]}}),
    _body(undo={"addr_code": {"rdrecord32": [1.5]}}),
    _body(undo={"addr_code": {"rdrecord32": [1, 1]}}),
    _body(undo={"addr_code": {"rdrecord32": list(range(1, 502))}}),
    _body(undo={"addr_code": {"rdrecord32": 1}}),
    _body(undo={"addr_code": [1]}),
    _body(undo=[1]),
    _body(head={"cMemo": "x"}),
    _body(lines=[{"a": 1}]),
    _auth(id=1, fields=dict(_FIELDS), expect=dict(_EXPECT)),
    _auth(type="dispatch", fields=dict(_FIELDS), expect=dict(_EXPECT)),
    _auth(type="dispatch", id=1, expect=dict(_EXPECT)),
    _auth(type="dispatch", id=1, fields=dict(_FIELDS)),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_undo_limit_is_500_per_list() -> None:
    fake = _FixBridge()
    undo = {"addr_code": {"rdrecord32": list(range(1, 501)), "SaleBillVouch": list(range(1, 501))}}
    assert _client(fake).post(_PATH, json=_body(undo=undo)).status_code == 200


def test_undo_errors_report_the_field_path() -> None:
    client = _client(FakeBridge())
    denied = client.post(_PATH, json=_body(undo={"addr_code": {"rdrecord32": [1, 1]}}))
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "undo"
    denied = client.post(
        _PATH,
        json=_body(undo={"person_code": {"rdrecord32": [1]}}, fields={"addr_code": "02"}, expect={"addr_code": "01"}),
    )
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "undo"


def test_unknown_key_name_is_reported() -> None:
    denied = _client(FakeBridge()).post(_PATH, json=_body(fields={"cMemo": "x"}, expect={"cMemo": "y"}))
    assert denied.status_code == 400
    assert "请求参数无效：fields" in denied.text


def test_audit_names_the_dispatch(capsys) -> None:
    ok = _client(_FixBridge()).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    line = audit_line(out, _PATH)
    assert line["action"].endswith("#1000000001")
    assert (line["type"], line["op"]) == ("dispatch", "head_fix")


@pytest.mark.parametrize(
    ("status", "code"),
    (
        (404, "not_found"),
        (409, "state_mismatch"),
        (409, "address_not_found"),
        (409, "person_invalid"),
        (409, "person_department_mismatch"),
        (504, "outcome_unknown"),
    ),
)
def test_bridge_errors_pass_through(status: int, code: str) -> None:
    fake = _FixBridge()
    fake.error = ApiError(status, code, "桥的中文说明")
    denied = _client(fake).post(_PATH, json=_body())
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_dry_run_is_forwarded_as_validate() -> None:
    fake = _FixBridge()
    ok = _client(fake).post(_PATH, json=_body(dry_run=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    data = ok.json()
    assert (data["dry_run"], data["mode"]) == (True, "validate")


def test_openapi_lists_the_write() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "修正历史发货单表头"
    assert operation["operationId"] == "coVoucherHeadFix"
    description = operation["description"]
    assert "权限：写" in description
    for word in (
        "addr_code",
        "person_code",
        "expect",
        "downstream",
        "address_not_found",
        "person_invalid",
        "person_department_mismatch",
        "state_mismatch",
        "shared",
        "differs",
        "already_new",
        "undo",
        "person_empty",
        "too_many",
        "Ar_Detail",
    ):
        assert word in description
