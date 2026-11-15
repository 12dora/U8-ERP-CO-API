"""销售订单表头自定义项写入 /v1/co/vouchers/so_defines：请求校验、转发给桥的字段、cascade 列表放行、幂等键、
错误映射、审计和 OpenAPI。假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.support import audit_line
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_update_close_gen import _spec
from u8co_api.co_bridge import to_api_error
from u8co_api.errors import ApiError

_PATH = "/v1/co/vouchers/so_defines"
_BRIDGE = "/v1/vouchers/so_defines"
_ID = 1000000001
_KEY = "sodefines-1"
_CASCADE = [
    {
        "type": "dispatch",
        "id": 1000000011,
        "code": "FH0001",
        "result": "updated",
        "before": {"cdefine1": None, "cdefine2": "文本B"},
    },
    {
        "type": "sale_invoice",
        "id": 1000000021,
        "code": "FP0001",
        "result": "skipped",
        "reason": "mixed_sources",
        "before": {"cdefine1": "DEMO-002", "cdefine2": None},
    },
]


class _SoBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        extra = {"cascade": [dict(item) for item in _CASCADE]} if payload.get("cascade") is True else {}
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "validate", "type": "sale_order", "id": payload["id"], **extra}
        return {
            "ok": True,
            "type": "sale_order",
            "id": payload["id"],
            "code": "SO0001",
            "changed": True,
            "current": dict(payload["fields"]),
            "ufts": "0x00000000000A1B2C",
            **extra,
        }


def _body(**over) -> dict:
    body = _auth(type="sale_order", id=_ID, fields={"cdefine1": "DEMO-001"}, expect={"cdefine1": None})
    body.update(over)
    return body


def _two(**over) -> dict:
    values = {
        "fields": {"cdefine1": "DEMO-001", "cdefine2": "文本A"},
        "expect": {"cdefine1": None, "cdefine2": "文本B"},
    }
    values.update(over)
    return _body(**values)


# ---- 转发 ----


def test_forwards_fields_and_expect_null_as_empty_string() -> None:
    fake = _SoBridge()
    ok = _client(fake).post(_PATH, json=_two())
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert (sent["type"], sent["id"]) == ("sale_order", _ID)
    assert sent["fields"] == {"cdefine1": "DEMO-001", "cdefine2": "文本A"}
    assert sent["expect"] == {"cdefine1": "", "cdefine2": "文本B"}
    assert "cascade" not in sent and "empty_values" not in sent
    body = ok.json()
    assert (body["ok"], body["changed"], body["code"]) == (True, True, "SO0001")
    assert body["current"] == {"cdefine1": "DEMO-001", "cdefine2": "文本A"}
    assert body["ufts"] == "0x00000000000A1B2C"
    assert "cascade" not in body


def test_keys_are_normalised_to_lowercase() -> None:
    fake = _SoBridge()
    body = _body(fields={"cDefine3": "1.5", "CDEFINE16": "2"}, expect={"CDefine3": "", "cdefine16": None})
    assert _client(fake).post(_PATH, json=body).status_code == 200
    sent = fake.calls[-1][1]
    assert sent["fields"] == {"cdefine3": "1.5", "cdefine16": "2"}
    assert sent["expect"] == {"cdefine3": "", "cdefine16": ""}


def test_expect_keeps_raw_values_for_comparison() -> None:
    fake = _SoBridge()
    body = _body(expect={"cdefine1": " 文本B "}, empty_values=["-"])
    assert _client(fake).post(_PATH, json=body).status_code == 200
    assert fake.calls[-1][1]["expect"] == {"cdefine1": " 文本B "}


def test_empty_values_are_forwarded_only_when_given() -> None:
    fake = _SoBridge()
    assert _client(fake).post(_PATH, json=_body(empty_values=None)).status_code == 200
    assert "empty_values" not in fake.calls[-1][1]
    assert _client(fake).post(_PATH, json=_body(empty_values=["-", "N/A"])).status_code == 200
    assert fake.calls[-1][1]["empty_values"] == ["-", "N/A"]
    assert _client(fake).post(_PATH, json=_body(empty_values=[])).status_code == 200
    assert "empty_values" not in fake.calls[-1][1]


def test_cascade_is_forwarded_only_when_true_and_the_list_reaches_the_caller() -> None:
    fake = _SoBridge()
    ok = _client(fake).post(_PATH, json=_two(cascade=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["cascade"] is True
    assert ok.json()["cascade"] == _CASCADE
    assert _client(fake).post(_PATH, json=_body(cascade=False)).status_code == 200
    assert "cascade" not in fake.calls[-1][1]


def test_year_is_a_login_field_like_other_voucher_routes() -> None:
    fake = _SoBridge()
    assert _client(fake).post(_PATH, json=_body(year="2025")).status_code == 200
    assert fake.calls[-1][1]["year"] == "2025"


def test_sealed_keys() -> None:
    with _Up() as server:
        _wired(server.base_url).post(_PATH, json=_body())
        method, seen, raw = server.httpd.hits[-1]
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(json.loads(raw)) == _sealed("type", "id", "fields", "expect", "caller", "caller_trust")
    assert _SECRET not in raw.decode("utf-8")


def test_sealed_keys_with_cascade_and_empty_values() -> None:
    with _Up() as server:
        _wired(server.base_url).post(_PATH, json=_body(cascade=True, empty_values=["-"]))
        _method, _seen, raw = server.httpd.hits[-1]
    keys = ("type", "id", "fields", "expect", "empty_values", "cascade", "caller", "caller_trust")
    assert set(json.loads(raw)) == _sealed(*keys)


# ---- 校验 ----

_SIXTEEN = {f"cdefine{n}": "文本A" for n in range(1, 17)}
_REJECTS = (
    ("type", _body(type="dispatch")),
    ("type", _body(type=None)),
    ("id", _body(id=0)),
    ("id", _body(id=2147483648)),
    ("fields", _body(fields={}, expect={})),
    ("fields.cdefine17", _body(fields={"cdefine17": "x"}, expect={"cdefine17": None})),
    ("fields.cdefine0", _body(fields={"cdefine0": "x"}, expect={"cdefine0": None})),
    ("fields.cdefine01", _body(fields={"cdefine01": "x"}, expect={"cdefine01": None})),
    ("fields.chdefine1", _body(fields={"chdefine1": "x"}, expect={"chdefine1": None})),
    ("fields.cmemo", _body(fields={"cMemo": "x"}, expect={"cMemo": None})),
    ("fields.cdefine1", _body(fields={"cdefine1": "x", "CDEFINE1": "y"}, expect={"cdefine1": None})),
    ("fields.cdefine1", _body(fields={**_SIXTEEN, "cDefine1": "x"}, expect=dict.fromkeys(_SIXTEEN))),
    ("fields.cdefine1", _body(fields={"cdefine1": "-"}, empty_values=["-"])),
    ("fields.cdefine1", _body(fields={"cdefine1": "-"}, empty_values=[" - "])),
    ("fields.cdefine17", _body(fields={"cdefine17": 123}, expect={"cdefine17": None})),
    ("fields.cdefine1", _body(fields={"cDefine1": ""}, expect={"CDEFINE1": None})),
    ("fields.cdefine1", _body(fields={"cdefine1": ""})),
    ("fields.cdefine1", _body(fields={"cdefine1": "   "})),
    ("fields.cdefine1", _body(fields={"cdefine1": " DEMO-001"})),
    ("fields.cdefine1", _body(fields={"cdefine1": "DEMO-001 "})),
    ("fields.cdefine1", _body(fields={"cdefine1": "DEMO\n001"})),
    ("fields.cdefine1", _body(fields={"cdefine1": "J" * 121})),
    ("fields.cdefine1", _body(fields={"cdefine1": 1})),
    ("fields.cdefine1", _body(fields={"cdefine1": None})),
    ("fields", _body(fields=None)),
    ("fields", _body(fields=["cdefine1"])),
    ("expect.cdefine2", _two(expect={"cdefine1": None})),
    ("expect.cdefine1", _body(expect={})),
    ("expect.cdefine2", _body(expect={"cdefine1": None, "cdefine2": None})),
    ("expect.cdefine1", _body(expect={"cdefine1": None, "CDEFINE1": None})),
    ("expect.cdefine99", _body(expect={"cdefine99": None})),
    ("expect", _body(expect=None)),
    ("expect", _body(expect="DEMO-001")),
    ("expect.cdefine1", _body(expect={"cdefine1": 1})),
    ("expect.cdefine1", _body(expect={"CDefine1": "C" * 121})),
    ("expect.cdefine1", _body(expect={"cdefine1": "C" * 121})),
    ("expect.cdefine1", _body(expect={"cdefine1": "DEMO\n001"})),
    ("empty_values", _body(empty_values="-")),
    ("empty_values", _body(empty_values=[str(n) for n in range(9)])),
    ("empty_values.0", _body(empty_values=[""])),
    ("empty_values.0", _body(empty_values=["   "])),
    ("empty_values.0", _body(empty_values=["E" * 21])),
    ("empty_values.0", _body(empty_values=["a\tb"])),
    ("empty_values.0", _body(empty_values=[1])),
    ("cascade", _body(cascade="true")),
    ("cascade", _body(cascade=1)),
    ("dry_run", _body(dry_run="true")),
    (None, _body(head={"cdefine1": "x"})),
    (None, _body(replace=True)),
    (None, _body(mode="pair")),
    (None, _auth(type="sale_order", id=1, expect={"cdefine1": None})),
    (None, _auth(type="sale_order", id=1, fields={"cdefine1": "DEMO-001"})),
    (None, _auth(id=1, fields={"cdefine1": "DEMO-001"}, expect={"cdefine1": None})),
)


@pytest.mark.parametrize(("field", "body"), _REJECTS)
def test_invalid_body_does_not_call(field: str | None, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    error = denied.json()["error"]
    assert error["code"] == "bad_request"
    if field is not None:
        assert error["field"] == field, error
    assert fake.calls == []


def test_sixteen_columns_are_accepted() -> None:
    fake = _SoBridge()
    ok = _client(fake).post(_PATH, json=_body(fields=dict(_SIXTEEN), expect=dict.fromkeys(_SIXTEEN)))
    assert ok.status_code == 200, ok.text
    assert set(fake.calls[-1][1]["fields"]) == set(_SIXTEEN)


def test_key_errors_name_the_lowercase_column() -> None:
    client = _client(FakeBridge())
    unknown = client.post(_PATH, json=_body(fields={"CDEFINE17": "x"}, expect={"cdefine17": None})).json()["error"]
    assert unknown["field"] == "fields.cdefine17"
    assert "不是允许的列" in unknown["message"]
    token = client.post(_PATH, json=_body(fields={"cdefine1": "-"}, empty_values=["-"])).json()["error"]
    assert "empty_values" in token["message"]
    missing = client.post(_PATH, json=_body(expect={"cdefine2": None})).json()["error"]
    assert missing["field"] == "expect.cdefine1"


def test_an_empty_token_with_spaces_is_accepted() -> None:
    fake = _SoBridge()
    assert _client(fake).post(_PATH, json=_body(empty_values=[" " * 30 + "-"])).status_code == 200
    assert _client(fake).post(_PATH, json=_body(empty_values=[" " * 130 + "-" + " " * 10])).status_code == 200
    assert _client(fake).post(_PATH, json=_body(fields={"cdefine1": "-x"}, empty_values=["-"])).status_code == 200


# ---- 幂等键、预演 ----


def test_idempotency_key_is_forwarded() -> None:
    fake = _SoBridge()
    ok = _client(fake).post(_PATH, json=_body(), headers={"Idempotency-Key": _KEY})
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert sent["idempotency_key"] == _KEY
    assert sent["caller"] == "tool:tool-a"
    plain = _client(fake).post(_PATH, json=_body())
    assert plain.status_code == 200, plain.text
    assert "idempotency_key" not in fake.calls[-1][1]


def test_dry_run_with_a_key_is_refused() -> None:
    fake = _SoBridge()
    denied = _client(fake).post(_PATH, json=_body(dry_run=True), headers={"Idempotency-Key": _KEY})
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_dry_run_is_forwarded_as_validate_and_keeps_the_cascade_list() -> None:
    fake = _SoBridge()
    ok = _client(fake).post(_PATH, json=_two(dry_run=True, cascade=True))
    assert ok.status_code == 200, ok.text
    sent = fake.calls[-1][1]
    assert (sent["dry_run"], sent["cascade"]) == (True, True)
    data = ok.json()
    assert (data["dry_run"], data["mode"]) == (True, "validate")
    assert data["cascade"] == _CASCADE


def test_already_current_returns_changed_false() -> None:
    class _Same(_SoBridge):
        def call(self, path: str, payload: dict) -> dict:
            data = super().call(path, payload)
            data["changed"] = False
            return data

    ok = _client(_Same()).post(_PATH, json=_body(expect={"cdefine1": "DEMO-001"}))
    assert ok.status_code == 200, ok.text
    assert ok.json()["changed"] is False


def test_audit_names_the_sale_order(capsys) -> None:
    ok = _client(_SoBridge()).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    line = audit_line(out, _PATH)
    assert line["action"].endswith("#1000000001")
    assert (line["type"], line["op"]) == ("sale_order", "so_defines")


# ---- 错误 ----


@pytest.mark.parametrize(
    ("status", "code"),
    (
        (400, "bad_request"),
        (404, "not_found"),
        (409, "state_mismatch"),
        (409, "too_many_downstream"),
        (504, "outcome_unknown"),
    ),
)
def test_bridge_errors_pass_through(status: int, code: str) -> None:
    fake = _SoBridge()
    fake.error = ApiError(status, code, "桥的中文说明")
    denied = _client(fake).post(_PATH, json=_body())
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_bridge_field_reaches_the_caller() -> None:
    bridge = {"ok": False, "code": "bad_request", "message": "列宽不够", "field": "fields.cdefine2"}
    fake = _SoBridge()
    fake.error = to_api_error(400, bridge)
    denied = _client(fake).post(_PATH, json=_two())
    assert denied.status_code == 400
    assert denied.json()["error"]["field"] == "fields.cdefine2"


def test_state_mismatch_current_reaches_the_caller() -> None:
    current = {"cdefine1": "DEMO-002", "cdefine2": None}
    bridge = {"ok": False, "code": "state_mismatch", "message": "表头与 expect 不符", "detail": {"current": current}}
    fake = _SoBridge()
    fake.error = to_api_error(409, bridge)
    denied = _client(fake).post(_PATH, json=_two())
    assert denied.status_code == 409
    wire = denied.json()["error"]
    assert (wire["code"], wire["message"]) == ("state_mismatch", "表头与 expect 不符")
    assert wire["detail"] == {"current": current}


def test_concurrent_modification_has_no_detail() -> None:
    fake = _SoBridge()
    fake.error = to_api_error(409, {"ok": False, "code": "state_mismatch", "message": "订单刚被别人修改，请重试"})
    denied = _client(fake).post(_PATH, json=_body())
    assert denied.status_code == 409
    assert denied.json()["error"].get("detail") is None


def test_locked_by_another_user_is_state_mismatch_with_reason() -> None:
    bridge = {
        "ok": False,
        "code": "state_mismatch",
        "message": "销售订单已被他人锁定",
        "detail": {"reason": "locked", "locker": "张三"},
    }
    fake = _SoBridge()
    fake.error = to_api_error(409, bridge)
    denied = _client(fake).post(_PATH, json=_body())
    assert denied.status_code == 409
    wire = denied.json()["error"]
    assert wire["code"] == "state_mismatch"
    assert wire["detail"] == {"reason": "locked", "locker": "张三"}


# ---- OpenAPI ----


def test_openapi_lists_the_write() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "写销售订单表头自定义项"
    assert operation["operationId"] == "coVoucherSoDefines"
    description = operation["description"]
    assert "权限：写" in description
    for word in (
        "cdefine1",
        "cdefine16",
        "fields",
        "expect",
        "empty_values",
        "cascade",
        "mixed_sources",
        "too_many_downstream",
        "state_mismatch",
        "bad_request",
        "detail.reason",
        "detail.current",
        "outcome_unknown",
        "ufts",
        "Idempotency-Key",
        "so_defines",
    ):
        assert word in description
