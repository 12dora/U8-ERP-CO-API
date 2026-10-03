"""写入策略的 API 侧：失败即拒绝、重载、第 1 到 6 步的每个判定、审计字段、健康检查、写入一律带 caller。

假桥，不访问网络。时段按注入的本机时间判断。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal

from tests.test_co_bridge_routes import ProbeBridge
from tests.test_co_gate import FakeBridge, _client, _login
from u8co_api.co_bridge import to_api_error
from u8co_api.co_client import BridgeRoutes
from u8co_api.write_class import WriteInfo, classify
from u8co_api.write_policy import WritePolicy, decide, health_of
from u8co_api.write_policy_parse import parse

_CREATE = "/v1/co/vouchers/create"
_DELETE = "/v1/co/vouchers/delete"
_FRIDAY = datetime(2026, 10, 2, 10, 0)
_SATURDAY = datetime(2026, 10, 3, 10, 0)
_OPEN = {"version": 1, "unlisted": "allow"}


def _sale_order(lines: int = 1, **extra) -> dict:
    return _login(type="sale_order", head={"a": "b"}, lines=[{"a": index} for index in range(lines)], **extra)


def _write(path, data: object) -> None:
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    path.write_text(text, encoding="utf-8")


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _setup(tmp_path, data: object | None, now: datetime = _FRIDAY, clock: Clock | None = None):
    path = tmp_path / "write-policy.json"
    if data is not None:
        _write(path, data)
    fake = FakeBridge()
    client = _client(fake, write_policy_file=str(path))
    policy = WritePolicy(str(path), clock=clock or Clock(), now=lambda: now)
    client.app.state.write_policy = policy
    return client, fake, path


def _error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    return error


def _audit(out: str, endpoint: str) -> dict:
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    found = [line for line in lines if line.get("endpoint") == endpoint]
    assert found, "missing audit line"
    return found[-1]


# ---- 未配置、失败即拒绝 ----


def test_without_policy_writes_pass_and_health_keeps_its_shape() -> None:
    fake = FakeBridge()
    client = _client(fake)
    assert client.app.state.write_policy is None
    assert client.post(_CREATE, json=_sale_order()).status_code == 200
    assert client.get("/v1/co/health").json() == {"ok": True, "version": "test"}


def test_missing_file_refuses_writes_but_not_reads(tmp_path) -> None:
    client, fake, _ = _setup(tmp_path, None)
    response = client.post(_CREATE, json=_sale_order())
    error = _error(response, 503, "write_policy_unavailable")
    assert error["retryable"] is True and error["message"] == "写入策略不可用"
    assert response.headers["Retry-After"] == "30"
    assert fake.calls == []
    assert client.post("/v1/co/login-check", json=_login()).status_code == 200
    assert client.post("/v1/co/vouchers/load", json=_login(type="sale_order", id=1)).status_code == 200


def test_invalid_file_at_first_load_refuses_writes(tmp_path) -> None:
    client, fake, _ = _setup(tmp_path, {"version": 1, "unknown": True})
    _error(client.post(_CREATE, json=_sale_order()), 503, "write_policy_unavailable")
    assert fake.calls == []
    assert client.app.state.write_policy.state == "invalid"


def test_non_utf8_or_oversized_file_is_invalid(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(b"\xff\xfe{")
    assert WritePolicy(str(path)).state == "invalid"
    path.write_bytes(b" " * (1024 * 1024) + b'{"version": 1}')
    policy = WritePolicy(str(path))
    assert policy.state == "invalid" and policy.current is None


def test_dry_run_is_judged_too(tmp_path) -> None:
    client, fake, _ = _setup(tmp_path, {"version": 1})
    denied = client.post(_DELETE, json=_login(type="sale_order", id=1, dry_run=True))
    _error(denied, 403, "write_not_allowed")
    assert fake.calls == []


# ---- 第 2 到 6 步 ----


def test_global_freeze_with_reason(tmp_path) -> None:
    client, fake, _ = _setup(tmp_path, {**_OPEN, "freeze": {"global": True, "reason": "月末结账"}})
    response = client.post(_CREATE, json=_sale_order())
    error = _error(response, 503, "write_frozen")
    assert error["message"] == "写入已冻结：月末结账" and error["retryable"] is True
    assert response.headers["Retry-After"] == "300"
    assert fake.calls == []


def test_account_freeze_only_hits_that_account(tmp_path) -> None:
    client, _, _ = _setup(tmp_path, {**_OPEN, "freeze": {"accounts": ["902"]}})
    assert client.post(_CREATE, json=_sale_order()).status_code == 200
    client2, _, _ = _setup(tmp_path, {**_OPEN, "freeze": {"accounts": ["803"]}})
    assert _error(client2.post(_CREATE, json=_sale_order()), 503, "write_frozen")["message"] == "写入已冻结"


def test_outside_the_window_or_on_a_deny_date(tmp_path) -> None:
    windows = {**_OPEN, "windows": [{"days": "1-5", "start": "08:00", "end": "18:00"}]}
    client, fake, _ = _setup(tmp_path, windows, now=_SATURDAY)
    response = client.post(_CREATE, json=_sale_order())
    assert _error(response, 503, "write_window")["message"] == "当前时段不允许写入"
    assert response.headers["Retry-After"] == "300"
    client2, _, _ = _setup(tmp_path, {**windows, "denyDates": ["2026-10-02"]}, now=_FRIDAY)
    _error(client2.post(_CREATE, json=_sale_order()), 503, "write_window")
    client3, _, _ = _setup(tmp_path, windows, now=_FRIDAY)
    assert client3.post(_CREATE, json=_sale_order()).status_code == 200
    assert fake.calls == []


def test_unlisted_account_and_unmatched_rule_are_not_allowed(tmp_path) -> None:
    client, fake, _ = _setup(tmp_path, {"version": 1, "accounts": {"902": {"allow": [{"type": "*", "ops": ["*"]}]}}})
    error = _error(client.post(_CREATE, json=_sale_order()), 403, "write_not_allowed")
    assert error["retryable"] is False
    assert error["detail"] == {"type": "sale_order", "op": "create"}
    rules = {"version": 1, "accounts": {"803": {"allow": [{"type": "sale_order", "ops": ["verify"]}]}}}
    client2, _, _ = _setup(tmp_path, rules)
    _error(client2.post(_CREATE, json=_sale_order()), 403, "write_not_allowed")
    assert fake.calls == []


def test_matching_rule_passes_and_carries_caller(tmp_path) -> None:
    rules = {"version": 1, "accounts": {"803": {"allow": [{"type": "sale_order", "ops": ["create", "delete"]}]}}}
    client, fake, _ = _setup(tmp_path, rules)
    assert client.post(_CREATE, json=_sale_order()).status_code == 200
    assert fake.calls[-1][1]["caller"] == "tool:tool-a"
    assert "idempotency_key" not in fake.calls[-1][1]


def test_operator_deny_and_allow_lists(tmp_path) -> None:
    every = [{"type": "*", "ops": ["*"]}]
    deny = {"version": 1, "accounts": {"803": {"operators": {"deny": ["OP001"]}, "allow": every}}}
    client, fake, _ = _setup(tmp_path, deny)
    error = _error(client.post(_CREATE, json=_sale_order()), 403, "operator_not_allowed")
    assert error["message"] == "该操作员不能在此账套写入"
    allow = {"version": 1, "accounts": {"803": {"operators": {"allow": ["svc"]}, "allow": every}}}
    client2, _, _ = _setup(tmp_path, allow)
    _error(client2.post(_CREATE, json=_sale_order()), 403, "operator_not_allowed")
    assert client2.post(_CREATE, json=_sale_order(operator="svc")).status_code == 200
    assert fake.calls == []


def test_max_lines(tmp_path) -> None:
    client, fake, _ = _setup(tmp_path, {**_OPEN, "defaults": {"maxLines": 2}})
    error = _error(client.post(_CREATE, json=_sale_order(lines=3)), 400, "write_limit")
    assert error["field"] == "lines" and error["detail"] == {"max": 2, "actual": 3}
    assert error["retryable"] is False
    assert client.post(_CREATE, json=_sale_order(lines=2)).status_code == 200
    # 删除不看行数。
    assert client.post(_DELETE, json=_login(type="sale_order", id=1)).status_code == 200
    assert len(fake.calls) == 2


def test_max_amount_only_for_closebill_types() -> None:
    snap = parse(json.dumps({**_OPEN, "defaults": {"maxAmount": 1000}}), _FRIDAY)
    big = WriteInfo(acc="803", type="ar_receipt", op="create", operator="op001", amount=Decimal("-1000.01"))
    error = decide(snap, big, _FRIDAY)
    assert (error.status, error.code, error.message) == (400, "write_limit", "金额超过上限")
    assert error.detail == {"max": 1000, "actual": -1000.01}
    edge = WriteInfo(acc="803", type="ar_receipt", op="create", operator="d", amount=Decimal(1000))
    assert decide(snap, edge, _FRIDAY) is None
    other = WriteInfo(acc="803", type="ar_bill", op="create", operator="op001", amount=Decimal(99999))
    assert decide(snap, other, _FRIDAY) is None


def test_max_amount_fails_closed_on_exponent_and_unparseable_values() -> None:
    snap = parse(json.dumps({**_OPEN, "defaults": {"maxAmount": 5000000}}), _FRIDAY)
    payload = {"acc": "803", "operator": "op001", "type": "ar_receipt"}
    exp = classify("co:vouchers/create", {**payload, "lines": [{"iAmt": "6e6"}]})
    error = decide(snap, exp, _FRIDAY)
    assert (error.status, error.code, error.message) == (400, "write_limit", "金额超过上限")
    rate = classify("co:vouchers/create", {**payload, "head": {"iexchrate": "7.2e0"}, "lines": [{"iAmt_f": "1000000"}]})
    assert decide(snap, rate, _FRIDAY).code == "write_limit"
    bad = classify("co:vouchers/create", {**payload, "lines": [{"iAmt": "6,000,000"}]})
    error = decide(snap, bad, _FRIDAY)
    assert (error.status, error.code, error.message) == (400, "write_limit", "金额或汇率无法识别，按超过上限处理")
    assert error.detail == {"max": 5000000}
    # 未配置金额上限：解析不了的金额照旧放给写入方报 400。
    assert decide(parse(json.dumps(_OPEN), _FRIDAY), bad, _FRIDAY) is None


def test_decision_order_first_failure_wins() -> None:
    data = {"version": 1, "freeze": {"global": True}, "windows": [{"days": "1", "start": "00:00", "end": "00:01"}]}
    snap = parse(json.dumps(data), _FRIDAY)
    info = WriteInfo(acc="803", type="sale_order", op="create", operator="op001", lines=999)
    assert decide(snap, info, _SATURDAY).code == "write_frozen"
    assert decide(None, info, _SATURDAY).code == "write_policy_unavailable"


# ---- 重载 ----


def test_reload_swaps_keeps_last_good_and_goes_missing(tmp_path) -> None:
    clock = Clock()
    client, fake, path = _setup(tmp_path, {**_OPEN, "reloadSeconds": 5}, clock=clock)
    policy = client.app.state.write_policy
    assert client.post(_CREATE, json=_sale_order()).status_code == 200

    _write(path, {**_OPEN, "reloadSeconds": 5, "freeze": {"global": True}})
    _bump(path)
    clock.value = 4.0
    assert client.post(_CREATE, json=_sale_order()).status_code == 200  # 还没到 reloadSeconds
    clock.value = 5.0
    _error(client.post(_CREATE, json=_sale_order()), 503, "write_frozen")
    assert policy.state == "ok"

    _write(path, '{"version": 1, "bogus": 1}')
    _bump(path)
    clock.value = 10.0
    # 新内容无效：保留上一份有效策略（仍冻结），状态 invalid。
    _error(client.post(_CREATE, json=_sale_order()), 503, "write_frozen")
    assert policy.state == "invalid" and policy.current is not None

    os.remove(path)
    clock.value = 15.0
    _error(client.post(_CREATE, json=_sale_order()), 503, "write_policy_unavailable")
    assert policy.state == "missing"

    _write(path, _OPEN)
    clock.value = 20.0
    assert client.post(_CREATE, json=_sale_order()).status_code == 200
    assert len(fake.calls) == 3


def test_reload_by_content_hash_even_with_same_size_and_mtime(tmp_path) -> None:
    # 等长替换且保留修改时间（Copy-Item、scp -p）：按内容 SHA-256 判断，照样生效。
    clock = Clock()
    client, _, path = _setup(tmp_path, {**_OPEN, "freeze": {"accounts": ["801"]}}, clock=clock)
    policy = client.app.state.write_policy
    assert policy.current.is_frozen("801")
    stamp = os.stat(path)
    _write(path, {**_OPEN, "freeze": {"accounts": ["802"]}})
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert os.stat(path).st_size == stamp.st_size
    clock.value = 2.0
    assert policy.current.is_frozen("802") and not policy.current.is_frozen("801")
    same = policy.current
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns + 5_000_000_000))
    clock.value = 4.0
    assert policy.current is same


def _bump(path) -> None:
    # 同一纳秒内改写时修改时间可能不变：手动往后拨一秒。
    info = os.stat(path)
    os.utime(path, ns=(info.st_atime_ns, info.st_mtime_ns + 1_000_000_000))


# ---- 审计 ----


def test_audit_records_type_op_and_outcome(tmp_path, capsys) -> None:
    client, fake, _ = _setup(tmp_path, _OPEN)
    capsys.readouterr()
    assert client.post(_CREATE, json=_sale_order()).status_code == 200
    line = _audit(capsys.readouterr().out, _CREATE)
    assert (line["type"], line["op"], line["outcome"]) == ("sale_order", "create", "ok")

    fake.error = to_api_error(409, {"ok": False, "code": "u8_rejected", "message": "U8 拒绝"})
    assert client.post(_CREATE, json=_sale_order()).status_code == 409
    assert _audit(capsys.readouterr().out, _CREATE)["outcome"] == "u8_rejected"

    client2, _, _ = _setup(tmp_path, {**_OPEN, "freeze": {"global": True}})
    capsys.readouterr()
    assert client2.post(_DELETE, json=_login(type="sale_order", id=1)).status_code == 503
    line = _audit(capsys.readouterr().out, _DELETE)
    assert (line["type"], line["op"], line["outcome"], line["status"]) == ("sale_order", "delete", "write_frozen", 503)


def test_audit_read_has_no_type_or_op(capsys) -> None:
    client = _client(FakeBridge())
    capsys.readouterr()
    assert client.post("/v1/co/vouchers/load", json=_login(type="sale_order", id=1)).status_code == 200
    line = _audit(capsys.readouterr().out, "/v1/co/vouchers/load")
    assert (line["type"], line["op"], line["outcome"]) == (None, None, "ok")


def test_family_routes_audit_their_family_token(tmp_path, capsys) -> None:
    client, _, _ = _setup(tmp_path, _OPEN)
    capsys.readouterr()
    body = _login(id=1, action="unverify")
    assert client.post("/v1/co/sale-orders/verify", json=body).status_code == 200
    line = _audit(capsys.readouterr().out, "/v1/co/sale-orders/verify")
    assert (line["type"], line["op"]) == ("sale_order", "unverify")


# ---- 健康检查 ----


def test_health_reports_the_api_policy(tmp_path) -> None:
    data = {**_OPEN, "freeze": {"accounts": ["801"]}, "windows": [{"days": "1-5", "start": "08:00", "end": "18:00"}]}
    client, _, _ = _setup(tmp_path, data)
    body = client.get("/v1/co/health").json()
    own = body["api_write_policy"]
    assert own["state"] == "ok" and own["version"] == 1
    assert own["freeze"] == {"global": False, "accounts": ["801"]}
    assert own["window_open"] is True
    assert own["loaded_at"].endswith("Z")
    assert "write_policy" not in body


def test_health_of_missing_and_off() -> None:
    assert health_of("off", None, _FRIDAY) == {"state": "off"}
    assert health_of("missing", None, _FRIDAY) == {
        "state": "missing",
        "version": None,
        "loaded_at": None,
        "freeze": {"global": False, "accounts": []},
        "window_open": False,
    }


class PolicyBridge(ProbeBridge):
    def __init__(self, policy: object) -> None:
        super().__init__()
        self.policy = policy

    def health(self) -> dict:
        return {"ok": True, "version": "test", "write_policy": self.policy}


_BRIDGE_POLICY = {
    "state": "ok",
    "version": 1,
    "loaded_at": "2026-10-03T01:02:03Z",
    "freeze": {"global": True, "accounts": []},
    "window_open": True,
}


def test_health_passes_through_bridge_policies_per_route() -> None:
    client = _client(PolicyBridge(_BRIDGE_POLICY), co_accounts=("803", "801", "906"))
    client.app.state.co_routes = BridgeRoutes(
        (("routes[0]", ("801",), PolicyBridge({"state": "off"})), ("routes[1]", ("906",), PolicyBridge("junk"))),
    )
    body = client.get("/v1/co/health").json()
    assert body["write_policy"] == _BRIDGE_POLICY
    assert body["routes"][0]["write_policy"] == {"state": "off"}
    assert "write_policy" not in body["routes"][1]
    assert "api_write_policy" not in body


def test_health_drops_a_malformed_bridge_policy() -> None:
    client = _client(PolicyBridge({"state": "exploded"}))
    assert client.get("/v1/co/health").json() == {"ok": True, "version": "test"}
