"""应收坏账处理（arap/bad_debt：坏账发生 / 收回 / 计提，只对测试账套开放）以及取消处理、处理制单收坏账处理号 HZAR：
转给桥的字段、请求校验、响应放行、403 / 409 放行、预演、审计和 OpenAPI。桥是本地假桥。"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from tests.support import audit_line
from tests.test_co_gate import _SECRET, FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed, _Up, _wired
from tests.test_co_update_close_gen import _spec
from u8co_api.co_models_arap_bad import CoArapBadDebtIn
from u8co_api.co_models_arap_batch import CoArapProcVoucherIn
from u8co_api.errors import ApiError

_BAD = "/v1/co/arap/bad_debt"
_CANCEL = "/v1/co/arap/process/cancel"
_VOUCHER = "/v1/co/arap/process/voucher"
_HZ = "HZAR0000000000014"
_LINE = {"type": "26", "id": "0000000012", "line_id": 1001, "amount": 100.5}
_OCCUR = {"action": "occur", "customer": "C001", "lines": [_LINE, {"type": "R0", "id": "0000000034", "amount": 20}]}
_RECOVER = {"action": "recover", "customer": "C001", "receipt": "0000000056", "amount": 300}


class _BadBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        if self.error is not None:
            raise self.error
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "rollback", "action": "bad_debt", "docs": []}
        if path == "/v1/arap/process/cancel":
            return {"ok": True, "cancel_no": payload["cancel_no"], "style": "9G", "ar_rows": 2, "ap_rows": 0}
        if path == "/v1/arap/process/voucher":
            return {"ok": True, "proc_style": "9F", "out_sign": "JT", "cancel_nos": payload["cancel_nos"]}
        out = {"ok": True, "action": payload["action"], "cancel_no": _HZ, "remain_before": 500, "remain_after": 380}
        if payload["action"] == "provision":
            return dict(out, style="9F", amount=-120, base=38000, rate=0.01, target=380, style_name="计提坏账", method_name="销售收入百分比法")
        row = {"type": "26", "id": "0000000012", "line_id": 1001, "amount": 100.5, "remaining": 0, "memo": "x"}
        return dict(out, style="9G", amount=120.5, rows=[row])


@pytest.mark.parametrize(
    ("body", "keys"),
    [
        (
            dict(_OCCUR, currency="人民币", digest="客户破产", dept="D01", person="P01"),
            ("action", "customer", "lines", "currency", "digest", "dept", "person"),
        ),
        (_RECOVER, ("action", "customer", "receipt", "amount")),
        ({"action": "provision"}, ("action",)),
    ],
)
def test_bad_debt_sends_exact_sealed_keys(body: dict, keys: tuple[str, ...]) -> None:
    with _Up() as server:
        response = _wired(server.base_url).post(_BAD, json=_auth(**body))
        assert response.status_code == 200, response.text
        method, seen, raw = server.httpd.hits[-1]
        sent = json.loads(raw)
    assert (method, seen) == ("POST", "/u8co/v1/arap/bad_debt")
    assert set(sent) == _sealed(*keys, "caller")
    for name in keys:
        assert sent[name] == body[name], name
    assert _SECRET not in raw.decode("utf-8")


def test_occur_returns_rows_and_keeps_extras() -> None:
    ok = _client(_BadBridge()).post(_BAD, json=_auth(**_OCCUR))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert (body["cancel_no"], body["style"], body["amount"]) == (_HZ, "9G", 120.5)
    assert (body["remain_before"], body["remain_after"]) == (500, 380)
    assert body["rows"][0]["memo"] == "x"


def test_provision_returns_the_basis() -> None:
    ok = _client(_BadBridge()).post(_BAD, json=_auth(action="provision"))
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert (body["style"], body["base"], body["rate"], body["target"]) == ("9F", 38000, 0.01, 380)
    assert (body["style_name"], body["method_name"]) == ("计提坏账", "销售收入百分比法")


_REJECTS = (
    {},
    {"action": "write_off"},
    {"action": "occur", "customer": "C001"},
    {"action": "occur", "lines": [_LINE]},
    {"action": "occur", "customer": "C001", "lines": []},
    {"action": "occur", "customer": "C001", "lines": [dict(_LINE, line_id=n) for n in range(1, 52)]},
    {"action": "occur", "customer": "C001", "lines": [dict(_LINE, type="RZ")]},
    {"action": "occur", "customer": "C001", "lines": [dict(_LINE, type="48")]},
    {"action": "occur", "customer": "C001", "lines": [{"type": "R1", "id": "1", "line_id": 5, "amount": 1}]},
    {"action": "occur", "customer": "C001", "lines": [dict(_LINE, amount=0)]},
    {"action": "occur", "customer": "C001", "lines": [dict(_LINE, amount=1.005)]},
    {"action": "occur", "customer": "C001", "lines": [dict(_LINE, amount="5")]},
    {"action": "occur", "customer": "C001", "lines": [_LINE, _LINE]},
    {"action": "occur", "customer": "C001", "lines": [_LINE, {"type": "26", "id": "0000000012", "amount": 1}]},
    {"action": "occur", "customer": "C001", "lines": [dict(_LINE, id=" ")]},
    {"action": "occur", "customer": "C001", "lines": [dict(_LINE, extra=1)]},
    dict(_OCCUR, receipt="0000000056"),
    dict(_OCCUR, digest="a\tb"),
    dict(_OCCUR, customer=" "),
    dict(_OCCUR, dept="D" * 13),
    {"action": "recover", "customer": "C001", "receipt": "0000000056"},
    {"action": "recover", "customer": "C001", "amount": 5},
    {"action": "recover", "receipt": "0000000056", "amount": 5},
    dict(_RECOVER, amount=-1),
    dict(_RECOVER, lines=[_LINE]),
    dict(_RECOVER, dept="D01"),
    dict(_RECOVER, person="P01"),
    {"action": "provision", "customer": "C001"},
    {"action": "provision", "amount": 5},
    {"action": "provision", "digest": "计提"},
    {"action": "provision", "flag": "AR"},
)


@pytest.mark.parametrize("body", _REJECTS)
def test_bad_bodies_are_400_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_BAD, json=_auth(**body))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize("digest", ["", "  "])
def test_empty_digest_means_default_and_is_not_sent(digest: str) -> None:
    with _Up() as server:
        response = _wired(server.base_url).post(_BAD, json=_auth(**dict(_RECOVER, digest=digest)))
        assert response.status_code == 200, response.text
        sent = json.loads(server.httpd.hits[-1][2])
    assert "digest" not in sent
    assert CoArapBadDebtIn.model_validate(_auth(**dict(_OCCUR, digest=digest))).digest is None


def test_dept_allows_twelve_characters() -> None:
    model = CoArapBadDebtIn.model_validate(_auth(**dict(_OCCUR, dept="D" * 12)))
    assert model.dept == "D" * 12


def test_model_messages() -> None:
    with pytest.raises(ValidationError, match="action 为 provision 时不能带 customer"):
        CoArapBadDebtIn.model_validate(_auth(action="provision", customer="C001"))
    with pytest.raises(ValidationError, match="action 为 recover 时必须给 amount"):
        CoArapBadDebtIn.model_validate(_auth(action="recover", customer="C001", receipt="1"))
    with pytest.raises(ValidationError, match="应收单按整单处理，不能带 line_id"):
        CoArapBadDebtIn.model_validate(
            _auth(action="occur", customer="C001", lines=[{"type": "R0", "id": "1", "line_id": 2, "amount": 1}])
        )
    with pytest.raises(ValidationError, match="同一张单据不能既整单又按行处理"):
        whole = {"type": "26", "id": "0000000012", "amount": 1}
        CoArapBadDebtIn.model_validate(_auth(action="occur", customer="C001", lines=[_LINE, whole]))


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (_CANCEL, {"flag": "AR", "cancel_no": _HZ}),
        (_VOUCHER, {"flag": "AR", "cancel_nos": [_HZ, "HZAR0000000000015"], "sign": "转"}),
    ],
)
def test_cancel_and_voucher_take_bad_debt_numbers(path: str, body: dict) -> None:
    fake = _BadBridge()
    ok = _client(fake).post(path, json=_auth(**body))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["flag"] == "AR"


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (_CANCEL, {"flag": "AP", "cancel_no": _HZ}),
        (_CANCEL, {"flag": "AR", "cancel_no": "HZAP0000000014"}),
        (_VOUCHER, {"flag": "AP", "cancel_nos": [_HZ]}),
        (_VOUCHER, {"flag": "AR", "cancel_nos": [_HZ, "YCFAP000000000001"]}),
        (_VOUCHER, {"flag": "AR", "cancel_nos": [_HZ], "pl_code": "660399"}),
        (_VOUCHER, {"flag": "AR", "cancel_nos": [_HZ], "expense_code": "660301"}),
    ],
)
def test_bad_debt_numbers_follow_the_cancel_and_voucher_rules(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=_auth(**body))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_voucher_message_lists_the_bad_debt_prefix() -> None:
    with pytest.raises(ValidationError, match="一次只能给同一类处理的批次号（坏账处理）"):
        CoArapProcVoucherIn.model_validate(_auth(flag="AR", cancel_nos=[_HZ, "YCFAP000000000001"]))
    with pytest.raises(ValidationError, match="HZAR 后接数字"):
        CoArapProcVoucherIn.model_validate(_auth(flag="AR", cancel_nos=["HZAP0000000014"]))


@pytest.mark.parametrize(
    ("path", "body"),
    [(_BAD, {"action": "provision"}), (_CANCEL, {"flag": "AR", "cancel_no": _HZ})],
)
def test_non_test_account_403_passes_through(path: str, body: dict) -> None:
    fake = _BadBridge()
    fake.error = ApiError(403, "test_account_only", "坏账处理只对配置为测试账套的账套开放")
    denied = _client(fake).post(path, json=_auth(**body))
    assert denied.status_code == 403
    error = denied.json()["error"]
    assert (error["code"], error["message"]) == ("test_account_only", "坏账处理只对配置为测试账套的账套开放")
    assert error["retryable"] is False


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (409, "state_mismatch", "未设置 2026 年坏账准备参数（应收款管理 › 设置 › 坏账准备）"),
        (409, "state_mismatch", "本次计提金额为 0"),
        (409, "state_mismatch", "坏账收回金额须等于收款单金额 300.00"),
        (404, "not_found", "收款单不存在"),
        (409, "u8_rejected", "核对不符，已回滚"),
    ],
)
def test_refusals_pass_through(status: int, code: str, message: str) -> None:
    fake = _BadBridge()
    fake.error = ApiError(status, code, message)
    denied = _client(fake).post(_BAD, json=_auth(**_RECOVER))
    assert denied.status_code == status
    assert (denied.json()["error"]["code"], denied.json()["error"]["message"]) == (code, message)


def test_dry_run_returns_the_preview() -> None:
    fake = _BadBridge()
    ok = _client(fake).post(_BAD, json=_auth(dry_run=True, **_OCCUR))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["dry_run"] is True
    assert ok.json()["mode"] == "rollback"


def test_audit_names_the_action_and_customer(capsys: pytest.CaptureFixture[str]) -> None:
    client = _client(_BadBridge())
    assert client.post(_BAD, json=_auth(**_OCCUR)).status_code == 200
    assert audit_line(capsys.readouterr().out, _BAD)["action"] == "co:arap/bad_debt:occur#C001"
    assert client.post(_BAD, json=_auth(action="provision")).status_code == 200
    out = capsys.readouterr().out
    assert audit_line(out, _BAD)["action"] == "co:arap/bad_debt:provision#date:2026-09-27"
    assert _SECRET not in out


def test_route_is_in_openapi_as_a_write() -> None:
    operation = _spec()["paths"][_BAD]["post"]
    assert operation["operationId"] == "coArapBadDebt"
    assert operation["tags"] == ["应收应付处理"]
    assert operation["x-u8co-access"] == "write"
    assert operation["x-u8co-dry-run"] is True
    words = ("9G", "9H", "9F", "HZAR", "testAccounts", "test_account_only", "本次计提金额为 0", "AR050602", "待实测")
    for word in words:
        assert word in operation["description"], word
    for path in (_CANCEL, _VOUCHER):
        assert "HZAR" in _spec()["paths"][path]["post"]["description"], path
