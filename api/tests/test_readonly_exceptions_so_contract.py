"""只读例外（U8CO_READONLY_EXCEPTIONS）type=vouchers、op=so_contract：解析、闸门（路径 + 请求体 type）、审计、预演、
健康摘要，以及与 head_fix 互不连带、vouchers/update 等其余写路由仍然 403 的反向用例。假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.test_co_dryrun import DryBridge
from tests.test_co_gate import FakeBridge, _login
from tests.test_readonly_exceptions import (
    _ITEM,
    _PERSON,
    _RO,
    _UPDATE,
    _VOUCHER,
    _VOUCHER_UPDATE,
    _audit,
    _exits,
    _gated,
    _match,
    _raw,
    _refused,
)
from u8co_api.config import load_settings
from u8co_api.readonly_exceptions import ReadOnlyException, allows, parse_exceptions

_SO_CONTRACT = "/v1/co/vouchers/so_contract"
_HEAD_FIX = "/v1/co/vouchers/head_fix"
_ITEM_SO = {**_ITEM, "type": "vouchers", "op": "so_contract"}
_ITEM_HEAD = {**_ITEM, "type": "vouchers", "op": "head_fix"}
_BLANK = {"contract_no": None, "customer_contract_no": None}


def _rules(**over) -> tuple:
    return parse_exceptions(json.dumps([{**_ITEM_SO, **over}]), _RO)


def _write(**over) -> dict:
    body = _login(operator="svc01", type="sale_order", id=1000000001, contract_no="HT202611001", expect=dict(_BLANK))
    body.update(over)
    return body


def _head_fix() -> dict:
    return _login(
        operator="svc01", type="dispatch", id=1000000001, fields={"addr_code": "02"}, expect={"addr_code": "01"}
    )


# ---- 解析 ----


def test_so_contract_parses_and_summarises() -> None:
    (item,) = _rules()
    assert (item.type, item.op, item.archives) == ("vouchers", "so_contract", None)
    assert item.summary() == _ITEM_SO
    assert "archives" not in item.summary()


def test_both_voucher_ops_can_be_listed_together() -> None:
    rules = parse_exceptions(json.dumps([_ITEM_HEAD, _ITEM_SO]), _RO)
    assert [(item.type, item.op) for item in rules] == [("vouchers", "head_fix"), ("vouchers", "so_contract")]


@pytest.mark.parametrize("op", ("delete", "lock", "customer_person", "So_contract", "so_contract ", ""))
def test_vouchers_rejects_other_ops(op: str) -> None:
    _exits(json.dumps([{**_ITEM_SO, "op": op}]), _RO, "type=vouchers 时只能是 head_fix、so_contract")


@pytest.mark.parametrize("archives", (["district_class"], None, []))
def test_so_contract_rejects_archives_key(archives) -> None:
    _exits(json.dumps([{**_ITEM_SO, "archives": archives}]), _RO, "archives 只能用于 type=archives")


def test_archives_type_rejects_so_contract() -> None:
    _exits(_raw(op="so_contract"), _RO, "type=archives 时只能是")


def test_so_contract_account_must_be_read_only() -> None:
    _exits(json.dumps([{**_ITEM_SO, "accounts": ["801"]}]), _RO, "不在 U8CO_READONLY_ACCOUNTS")


def test_so_contract_applies_the_operator_rules() -> None:
    _exits(json.dumps([{**_ITEM_SO, "operators": ["svc 01"]}]), _RO, "操作员编码")
    _exits(json.dumps([{**_ITEM_SO, "operators": ["svc01", "SVC01"]}]), _RO, "重复")


def test_load_settings_reads_so_contract(monkeypatch) -> None:
    monkeypatch.setenv("U8CO_TRUST_FILE", "")
    monkeypatch.setenv("U8CO_ACCOUNTS", "803,801")
    monkeypatch.setenv("U8CO_READONLY_ACCOUNTS", "803")
    monkeypatch.setenv("U8CO_READONLY_EXCEPTIONS", json.dumps([_ITEM_SO]))
    (item,) = load_settings().read_only_exceptions
    assert (item.type, item.op, item.archives) == ("vouchers", "so_contract", None)


# ---- 闸门 ----


def test_matching_so_contract_is_admitted(capsys) -> None:
    fake = FakeBridge()
    ok = _gated(fake, read_only_exceptions=_rules()).post(_SO_CONTRACT, json=_write())
    assert ok.status_code == 200, ok.text
    assert [call[0] for call in fake.calls] == ["/v1/vouchers/so_contract"]
    line = _audit(capsys.readouterr().out, _SO_CONTRACT)
    assert line["outcome"] == "read_only_exception"
    assert line["read_only_exception"] is True
    assert (line["type"], line["op"]) == ("sale_order", "so_contract")


def test_other_voucher_writes_stay_refused_with_a_so_contract_exception() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_rules())
    edit = _login(operator="svc01", type="sale_order", id=1, head={"cMemo": "a"})
    _refused(client.post(_VOUCHER_UPDATE, json=edit))
    _refused(client.post(_VOUCHER, json=_login(operator="svc01", type="sale_order", head={"a": "b"}, lines=[{"a": 1}])))
    _refused(client.post(_HEAD_FIX, json=_head_fix()))
    assert fake.calls == []


def test_so_contract_rule_does_not_open_archives_routes() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_rules())
    _refused(client.post(_PERSON, json=_match()))
    _refused(client.post(_UPDATE, json=_login(operator="svc01", archive="customer", code="C900001", fields={"a": "b"})))
    assert fake.calls == []


def test_head_fix_rule_does_not_open_so_contract() -> None:
    fake = FakeBridge()
    rules = parse_exceptions(json.dumps([_ITEM_HEAD]), _RO)
    _refused(_gated(fake, read_only_exceptions=rules).post(_SO_CONTRACT, json=_write()))
    assert fake.calls == []


def test_so_contract_is_refused_without_a_matching_rule() -> None:
    fake = FakeBridge()
    _refused(_gated(fake, read_only_exceptions=()).post(_SO_CONTRACT, json=_write()))
    _refused(_gated(fake, read_only_exceptions=_rules()).post(_SO_CONTRACT, json=_write(operator="op001")))
    assert fake.calls == []


def test_so_contract_checks_the_account() -> None:
    fake = FakeBridge()
    client = _gated(
        fake,
        co_accounts=("803", "801"),
        read_only_accounts=("803", "801"),
        read_only_exceptions=_rules(),
    )
    _refused(client.post(_SO_CONTRACT, json=_write(acc="801")))
    assert fake.calls == []


def test_so_contract_operator_matches_without_regard_to_case() -> None:
    fake = FakeBridge()
    ok = _gated(fake, read_only_exceptions=_rules()).post(_SO_CONTRACT, json=_write(operator="SVC01"))
    assert ok.status_code == 200, ok.text
    padded = {"acc": "803", "operator": " svc01", "type": "sale_order"}
    assert allows(_rules(), "co:vouchers/so_contract", padded) is False


def test_allows_matches_the_exact_path_and_the_body_type() -> None:
    payload = {"acc": "803", "operator": "svc01", "type": "sale_order"}
    rules = _rules()
    assert allows(rules, "co:vouchers/so_contract", payload) is True
    for action in (
        "co:vouchers/update",
        "co:vouchers/head_fix",
        "co:vouchers/so_contract/x",
        "co:vouchers/so_contract2",
        "co:archives/so_contract",
        "co:gl/vouchers/so_contract",
        "vouchers/so_contract",
        "co:vouchers/",
        "",
    ):
        assert allows(rules, action, payload) is False, action
    assert allows((), "co:vouchers/so_contract", payload) is False
    # 路径对、请求体 type 不是 sale_order（缺失、别的单据、大小写不同、非字符串）一律不放行。
    for bad in ({}, {"type": "dispatch"}, {"type": "Sale_Order"}, {"type": " sale_order"}, {"type": None}, {"type": 1}):
        assert allows(rules, "co:vouchers/so_contract", {"acc": "803", "operator": "svc01", **bad}) is False, bad


def test_head_fix_also_checks_its_fixed_body_type() -> None:
    # head_fix 与 so_contract 同在登记表里：请求体 type 须是它固定的 dispatch（与桥 ReadOnlyVoucherOps 一致）。
    rules = parse_exceptions(json.dumps([_ITEM_HEAD]), _RO)
    assert allows(rules, "co:vouchers/head_fix", {"acc": "803", "operator": "svc01", "type": "dispatch"}) is True
    assert allows(rules, "co:vouchers/head_fix", {"acc": "803", "operator": "svc01"}) is False


def test_covers_rejects_a_hand_built_rule_with_the_wrong_type() -> None:
    forged = ReadOnlyException(accounts=("803",), operators=("svc01",), type="archives", op="so_contract")
    assert forged.covers({"type": "sale_order"}) is False
    stray = ReadOnlyException(accounts=("803",), operators=("svc01",), type="vouchers", op="update")
    assert stray.covers({"type": "sale_order"}) is False


def test_wrong_body_type_never_reaches_the_gate() -> None:
    # 请求模型把 type 限定为 sale_order：别的类型在闸门之前就是 400，不访问桥。
    fake = FakeBridge()
    denied = _gated(fake, read_only_exceptions=_rules()).post(_SO_CONTRACT, json=_write(type="dispatch"))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_so_contract_dry_run_passes_the_gate() -> None:
    fake = DryBridge()
    ok = _gated(fake, read_only_exceptions=_rules()).post(_SO_CONTRACT, json=_write(dry_run=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["dry_run"] is True


def test_dry_run_is_refused_without_a_rule() -> None:
    fake = DryBridge()
    _refused(_gated(fake, read_only_exceptions=()).post(_SO_CONTRACT, json=_write(dry_run=True)))
    assert fake.calls == []


def test_idempotency_replay_of_so_contract_is_refused_without_a_rule() -> None:
    fake = FakeBridge()
    headers = {"Idempotency-Key": "replay-non-matching"}
    _refused(_gated(fake, read_only_exceptions=()).post(_SO_CONTRACT, json=_write(), headers=headers))
    assert fake.calls == []


def test_idempotency_key_is_forwarded_when_admitted() -> None:
    fake = FakeBridge()
    ok = _gated(fake, read_only_exceptions=_rules()).post(
        _SO_CONTRACT, json=_write(), headers={"Idempotency-Key": "so-contract-1"}
    )
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["idempotency_key"] == "so-contract-1"


def test_policy_still_denies_after_so_contract_admission(tmp_path, capsys) -> None:
    path = tmp_path / "write-policy.json"
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    fake = FakeBridge()
    denied = _gated(fake, read_only_exceptions=_rules(), write_policy_file=str(path)).post(_SO_CONTRACT, json=_write())
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "write_not_allowed"
    assert fake.calls == []
    line = _audit(capsys.readouterr().out, _SO_CONTRACT)
    assert line["outcome"] == "write_not_allowed"
    assert line["read_only_exception"] is True


def test_policy_allows_only_the_so_contract_op(tmp_path) -> None:
    # 策略放行 (sale_order, update) 不连带放行 so_contract；单独放行 (sale_order, so_contract) 才通过。
    update_only = tmp_path / "update.json"
    allow_update = {"type": "sale_order", "ops": ["update"]}
    update_only.write_text(_policy(allow_update), encoding="utf-8")
    fake = FakeBridge()
    denied = _gated(fake, read_only_exceptions=_rules(), write_policy_file=str(update_only)).post(
        _SO_CONTRACT, json=_write()
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "write_not_allowed"
    only = tmp_path / "only.json"
    only.write_text(_policy({"type": "sale_order", "ops": ["so_contract"]}), encoding="utf-8")
    ok = _gated(fake, read_only_exceptions=_rules(), write_policy_file=str(only)).post(_SO_CONTRACT, json=_write())
    assert ok.status_code == 200, ok.text


def _policy(rule: dict) -> str:
    return json.dumps({"version": 1, "accounts": {"803": {"allow": [rule]}}})


def test_health_summary_has_type_vouchers_and_no_archives() -> None:
    data = _gated(read_only_exceptions=_rules()).get("/v1/co/health").json()
    assert data["api_read_only_exceptions"] == [_ITEM_SO]
