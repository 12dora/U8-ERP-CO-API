"""销售订单合同号回写 /v1/co/vouchers/so_contract：请求校验、转发给桥的字段、响应放行、幂等键、错误映射、
审计和 OpenAPI。假桥，不访问网络。
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

_PATH = "/v1/co/vouchers/so_contract"
_BRIDGE = "/v1/vouchers/so_contract"
_ID = 1000000001
_NO = "HT202611001"
_CUSTOMER_NO = "KH-2026-088"
_BLANK = {"contract_no": None, "customer_contract_no": None}
_KEY = "socontract-1"


class _SoBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "validate", "type": "sale_order", "id": payload["id"]}
        return {
            "ok": True,
            "type": "sale_order",
            "id": payload["id"],
            "code": "SO0001",
            "changed": True,
            "current": {
                "contract_no": payload["contract_no"],
                "customer_contract_no": payload.get("customer_contract_no"),
            },
            "ufts": "0x00000000000A1B2C",
        }


def _body(**over) -> dict:
    body = _auth(type="sale_order", id=_ID, contract_no=_NO, expect=dict(_BLANK))
    body.update(over)
    return body


def test_so_contract_forwards_and_keeps_null_current() -> None:
    fake = _SoBridge()
    ok = _client(fake).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    path, sent = fake.calls[-1]
    assert path == _BRIDGE
    assert (sent["type"], sent["id"], sent["contract_no"]) == ("sale_order", _ID, _NO)
    body = ok.json()
    assert (body["ok"], body["changed"], body["code"]) == (True, True, "SO0001")
    assert body["current"] == {"contract_no": _NO, "customer_contract_no": None}
    assert body["ufts"] == "0x00000000000A1B2C"


def test_customer_contract_no_is_forwarded_only_when_given() -> None:
    fake = _SoBridge()
    plain = _client(fake).post(_PATH, json=_body())
    assert plain.status_code == 200, plain.text
    assert "customer_contract_no" not in fake.calls[-1][1]
    explicit_null = _client(fake).post(_PATH, json=_body(customer_contract_no=None))
    assert explicit_null.status_code == 200, explicit_null.text
    assert "customer_contract_no" not in fake.calls[-1][1]
    moved = _client(fake).post(
        _PATH,
        json=_body(customer_contract_no=_CUSTOMER_NO, expect={"contract_no": _CUSTOMER_NO, "customer_contract_no": ""}),
    )
    assert moved.status_code == 200, moved.text
    sent = fake.calls[-1][1]
    assert sent["customer_contract_no"] == _CUSTOMER_NO
    assert sent["expect"] == {"contract_no": "KH-2026-088", "customer_contract_no": ""}
    assert moved.json()["current"]["customer_contract_no"] == _CUSTOMER_NO


def test_expect_null_reaches_the_bridge_as_empty_string() -> None:
    # API 转发时会去掉值为 null 的键；expect 的 null 转成空串，两个键始终都在（空与 null 等价）。
    fake = _SoBridge()
    assert _client(fake).post(_PATH, json=_body()).status_code == 200
    assert fake.calls[-1][1]["expect"] == {"contract_no": "", "customer_contract_no": ""}


def test_expect_keeps_raw_values_for_comparison() -> None:
    fake = _SoBridge()
    expect = {"contract_no": " 无 ", "customer_contract_no": "无"}
    assert _client(fake).post(_PATH, json=_body(expect=expect)).status_code == 200
    assert fake.calls[-1][1]["expect"] == expect


def test_year_is_a_login_field_like_other_voucher_routes() -> None:
    fake = _SoBridge()
    assert _client(fake).post(_PATH, json=_body(year="2025")).status_code == 200
    assert fake.calls[-1][1]["year"] == "2025"


def test_sealed_keys() -> None:
    with _Up() as server:
        _wired(server.base_url).post(_PATH, json=_body())
        method, seen, raw = server.httpd.hits[-1]
    assert (method, seen) == ("POST", "/u8co" + _BRIDGE)
    assert set(json.loads(raw)) == _sealed("type", "id", "contract_no", "expect", "caller")
    assert _SECRET not in raw.decode("utf-8")


_REJECTS = (
    _body(type="dispatch"),
    _body(type="purchase_order"),
    _body(type=None),
    _body(id=0),
    _body(id=-1),
    _body(id=2147483648),
    _body(contract_no=""),
    _body(contract_no="   "),
    _body(contract_no="无"),
    _body(contract_no=" HT1"),
    _body(contract_no="HT1 "),
    _body(contract_no="HT\n1"),
    _body(contract_no="J" * 121),
    _body(contract_no=1),
    _body(contract_no=None),
    _body(customer_contract_no=""),
    _body(customer_contract_no="无"),
    _body(customer_contract_no=" KH1"),
    _body(customer_contract_no="KH\t1"),
    _body(customer_contract_no="K" * 121),
    _body(customer_contract_no=5),
    _body(expect={}),
    _body(expect={"contract_no": None}),
    _body(expect={"customer_contract_no": None}),
    _body(expect={"contract_no": None, "customer_contract_no": None, "cMemo": "x"}),
    _body(expect={"contract_no": 1, "customer_contract_no": None}),
    _body(expect={"contract_no": None, "customer_contract_no": "C" * 121}),
    _body(expect={"contract_no": "J\n1", "customer_contract_no": None}),
    _body(expect=None),
    _body(expect="HT1"),
    _body(head={"cdefine1": "x"}),
    _body(fields={"cDefine1": "x"}),
    _body(dry_run="true"),
    _auth(id=1, contract_no=_NO, expect=dict(_BLANK)),
    _auth(type="sale_order", contract_no=_NO, expect=dict(_BLANK)),
    _auth(type="sale_order", id=1, expect=dict(_BLANK)),
    _auth(type="sale_order", id=1, contract_no=_NO),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_invalid_body_does_not_call(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=body)
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_validation_names_the_field() -> None:
    client = _client(FakeBridge())
    for over, word in (
        ({"contract_no": "无"}, "contract_no"),
        ({"customer_contract_no": "无"}, "customer_contract_no"),
    ):
        denied = client.post(_PATH, json=_body(**over))
        assert denied.status_code == 400
        assert f"请求参数无效：{word}" in denied.text


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


def test_dry_run_is_forwarded_as_validate() -> None:
    fake = _SoBridge()
    ok = _client(fake).post(_PATH, json=_body(dry_run=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    data = ok.json()
    assert (data["dry_run"], data["mode"]) == (True, "validate")


def test_already_current_returns_changed_false() -> None:
    class _Same(_SoBridge):
        def call(self, path: str, payload: dict) -> dict:
            data = super().call(path, payload)
            data["changed"] = False
            return data

    ok = _client(_Same()).post(_PATH, json=_body(expect={"contract_no": _NO, "customer_contract_no": ""}))
    assert ok.status_code == 200, ok.text
    assert ok.json()["changed"] is False


def test_audit_names_the_sale_order(capsys) -> None:
    ok = _client(_SoBridge()).post(_PATH, json=_body())
    assert ok.status_code == 200, ok.text
    out = capsys.readouterr().out
    assert _SECRET not in out
    line = audit_line(out, _PATH)
    assert line["action"].endswith("#1000000001")
    assert (line["type"], line["op"]) == ("sale_order", "so_contract")


@pytest.mark.parametrize(
    ("status", "code"),
    (
        (400, "bad_request"),
        (404, "not_found"),
        (409, "state_mismatch"),
        (504, "outcome_unknown"),
    ),
)
def test_bridge_errors_pass_through(status: int, code: str) -> None:
    fake = _SoBridge()
    fake.error = ApiError(status, code, "桥的中文说明")
    denied = _client(fake).post(_PATH, json=_body())
    assert denied.status_code == status
    assert denied.json()["error"]["code"] == code


def test_state_mismatch_current_reaches_the_caller() -> None:
    bridge = {
        "ok": False,
        "code": "state_mismatch",
        "message": "合同号当前不是空",
        "detail": {"current": {"contract_no": "KH-2026-088", "customer_contract_no": None}},
    }
    fake = _SoBridge()
    fake.error = to_api_error(409, bridge)
    denied = _client(fake).post(_PATH, json=_body())
    assert denied.status_code == 409
    wire = denied.json()["error"]
    assert (wire["code"], wire["message"]) == ("state_mismatch", "合同号当前不是空")
    assert wire["detail"] == {"current": {"contract_no": "KH-2026-088", "customer_contract_no": None}}


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


def test_openapi_lists_the_write() -> None:
    operation = _spec()["paths"][_PATH]["post"]
    assert operation["summary"] == "回写销售订单合同号"
    assert operation["operationId"] == "coVoucherSoContract"
    description = operation["description"]
    assert "权限：写" in description
    for word in (
        "cDefine1",
        "cDefine2",
        "contract_no",
        "customer_contract_no",
        "expect",
        "state_mismatch",
        "bad_request",
        "detail.reason",
        "outcome_unknown",
        "ufts",
        "Idempotency-Key",
        "so_contract",
        "detail.current",
    ):
        assert word in description
