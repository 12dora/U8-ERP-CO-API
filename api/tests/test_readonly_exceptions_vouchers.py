"""只读例外（U8CO_READONLY_EXCEPTIONS）type=vouchers、op=head_fix：解析、闸门、审计、预演、健康摘要，
以及 vouchers/update 等其余写路由在只读账套上仍然 403 的反向用例。假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.test_co_dryrun import DryBridge
from tests.test_co_gate import FakeBridge, _login
from tests.test_readonly_exceptions import (
    _ADDRESS,
    _CREATE,
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
    _update,
)
from u8co_api.config import load_settings
from u8co_api.readonly_exceptions import allows, parse_exceptions

_HEAD_FIX = "/v1/co/vouchers/head_fix"
_ITEM_FIX = {**_ITEM, "type": "vouchers", "op": "head_fix"}
_SUMMARY_FIX = dict(_ITEM_FIX)


def _rules(**over) -> tuple:
    return parse_exceptions(json.dumps([{**_ITEM_FIX, **over}]), _RO)


def _fix(**over) -> dict:
    body = _login(
        operator="svc01",
        type="dispatch",
        id=1000000001,
        fields={"addr_code": "02"},
        expect={"addr_code": "01"},
    )
    body.update(over)
    return body


def _edit(**over) -> dict:
    body = _login(operator="svc01", type="sale_order", id=1, head={"cMemo": "a"})
    body.update(over)
    return body


# ---- 解析 ----


def test_head_fix_parses_and_summarises() -> None:
    (item,) = _rules()
    assert (item.type, item.op, item.archives) == ("vouchers", "head_fix", None)
    assert item.summary() == _SUMMARY_FIX
    assert "archives" not in item.summary()


@pytest.mark.parametrize(
    "op",
    ("update", "create", "delete", "customer_person", "customer_address_add", "list", "Head_fix", "head_fix ", ""),
)
def test_vouchers_allows_only_head_fix(op: str) -> None:
    _exits(json.dumps([{**_ITEM_FIX, "op": op}]), _RO, "type=vouchers 时只能是 head_fix")


def test_vouchers_missing_or_non_string_op_stops_the_process() -> None:
    absent = {key: value for key, value in _ITEM_FIX.items() if key != "op"}
    _exits(json.dumps([absent]), _RO, "缺少: op")
    _exits(json.dumps([{**_ITEM_FIX, "op": None}]), _RO, "type=vouchers 时只能是 head_fix")
    _exits(json.dumps([{**_ITEM_FIX, "op": ["head_fix"]}]), _RO, "type=vouchers 时只能是 head_fix")


@pytest.mark.parametrize("op", ("head_fix",))
def test_archives_type_rejects_head_fix(op: str) -> None:
    _exits(_raw(op=op), _RO, "type=archives 时只能是")


@pytest.mark.parametrize("archives", (["district_class"], None, []))
def test_head_fix_rejects_archives_key(archives) -> None:
    _exits(
        json.dumps([{**_ITEM_FIX, "archives": archives}]), _RO, "archives 只能用于 type=archives 的 op=create 或 delete"
    )


def test_head_fix_account_must_be_read_only() -> None:
    _exits(json.dumps([{**_ITEM_FIX, "accounts": ["801"]}]), _RO, "不在 U8CO_READONLY_ACCOUNTS")


def test_head_fix_applies_the_operator_rules() -> None:
    _exits(json.dumps([{**_ITEM_FIX, "operators": ["svc 01"]}]), _RO, "操作员编码")
    _exits(json.dumps([{**_ITEM_FIX, "operators": ["svc01", "SVC01"]}]), _RO, "重复")


def test_load_settings_reads_head_fix(monkeypatch) -> None:
    monkeypatch.setenv("U8CO_TRUST_FILE", "")
    monkeypatch.setenv("U8CO_ACCOUNTS", "803,801")
    monkeypatch.setenv("U8CO_READONLY_ACCOUNTS", "803")
    monkeypatch.setenv("U8CO_READONLY_EXCEPTIONS", json.dumps([_ITEM_FIX]))
    (item,) = load_settings().read_only_exceptions
    assert (item.type, item.op, item.archives) == ("vouchers", "head_fix", None)


def test_load_settings_stops_on_unopened_voucher_op(monkeypatch) -> None:
    monkeypatch.setenv("U8CO_TRUST_FILE", "")
    monkeypatch.setenv("U8CO_ACCOUNTS", "803,801")
    monkeypatch.setenv("U8CO_READONLY_ACCOUNTS", "803")
    monkeypatch.setenv("U8CO_READONLY_EXCEPTIONS", json.dumps([{**_ITEM_FIX, "op": "update"}]))
    with pytest.raises(SystemExit, match="type=vouchers 时只能是 head_fix"):
        load_settings()


# ---- 闸门 ----


def test_matching_head_fix_is_admitted(capsys) -> None:
    fake = FakeBridge()
    ok = _gated(fake, read_only_exceptions=_rules()).post(_HEAD_FIX, json=_fix())
    assert ok.status_code == 200, ok.text
    assert [call[0] for call in fake.calls] == ["/v1/vouchers/head_fix"]
    line = _audit(capsys.readouterr().out, _HEAD_FIX)
    assert line["outcome"] == "read_only_exception"
    assert line["read_only_exception"] is True
    assert (line["type"], line["op"]) == ("dispatch", "head_fix")


def test_vouchers_update_stays_refused_with_a_head_fix_exception() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_rules())
    _refused(client.post(_VOUCHER_UPDATE, json=_edit()))
    _refused(client.post(_VOUCHER, json=_login(operator="svc01", type="sale_order", head={"a": "b"}, lines=[{"a": 1}])))
    assert fake.calls == []


def test_head_fix_rule_does_not_open_archives_routes() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_rules())
    _refused(client.post(_PERSON, json=_match()))
    address = _match(address="1", fields={"name": "x"}, expect={"name": "y"})
    del address["person"]
    _refused(client.post(_ADDRESS, json=address))
    _refused(client.post(_UPDATE, json=_update()))
    _refused(client.post(_CREATE, json=_update(archive="district_class", code="33", fields={"name": "x"})))
    assert fake.calls == []


def test_archives_rules_do_not_open_head_fix() -> None:
    fake = FakeBridge()
    items = [{**_ITEM, "op": op} for op in ("customer_person", "customer_address_add", "customer_address_delete")]
    client = _gated(fake, read_only_exceptions=parse_exceptions(json.dumps(items), _RO))
    _refused(client.post(_HEAD_FIX, json=_fix()))
    assert fake.calls == []


def test_head_fix_is_refused_without_a_matching_rule() -> None:
    fake = FakeBridge()
    _refused(_gated(fake, read_only_exceptions=()).post(_HEAD_FIX, json=_fix()))
    _refused(_gated(fake, read_only_exceptions=_rules()).post(_HEAD_FIX, json=_fix(operator="op001")))
    assert fake.calls == []


def test_head_fix_checks_the_account() -> None:
    fake = FakeBridge()
    client = _gated(
        fake,
        co_accounts=("803", "801"),
        read_only_accounts=("803", "801"),
        read_only_exceptions=_rules(),
    )
    _refused(client.post(_HEAD_FIX, json=_fix(acc="801")))
    assert fake.calls == []


def test_head_fix_operator_matches_without_regard_to_case() -> None:
    fake = FakeBridge()
    ok = _gated(fake, read_only_exceptions=_rules()).post(_HEAD_FIX, json=_fix(operator="SVC01"))
    assert ok.status_code == 200, ok.text
    padded = {"acc": "803", "operator": " svc01"}
    assert allows(_rules(), "co:vouchers/head_fix", padded) is False


def test_allows_matches_the_exact_path_only() -> None:
    payload = {"acc": "803", "operator": "svc01"}
    rules = _rules()
    assert allows(rules, "co:vouchers/head_fix", payload) is True
    for action in (
        "co:vouchers/update",
        "co:vouchers/head_fix/x",
        "co:vouchers/head_fix2",
        "co:archives/head_fix",
        "co:gl/vouchers/head_fix",
        "vouchers/head_fix",
        "co:vouchers/",
        "",
    ):
        assert allows(rules, action, payload) is False, action
    assert allows((), "co:vouchers/head_fix", payload) is False
    # 请求体里的 type / archive 不影响放行判定（路径才是依据）。
    assert allows(rules, "co:vouchers/head_fix", {**payload, "type": "sale_order", "archive": "customer"}) is True


def test_head_fix_dry_run_passes_the_gate() -> None:
    fake = DryBridge()
    ok = _gated(fake, read_only_exceptions=_rules()).post(_HEAD_FIX, json=_fix(dry_run=True))
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["dry_run"] is True


def test_idempotency_replay_of_vouchers_update_is_refused() -> None:
    fake = FakeBridge()
    client = _gated(fake, read_only_exceptions=_rules())
    _refused(client.post(_VOUCHER_UPDATE, json=_edit(), headers={"Idempotency-Key": "replay-non-matching"}))
    assert fake.calls == []


def test_policy_still_denies_after_head_fix_admission(tmp_path, capsys) -> None:
    path = tmp_path / "write-policy.json"
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    fake = FakeBridge()
    denied = _gated(fake, read_only_exceptions=_rules(), write_policy_file=str(path)).post(_HEAD_FIX, json=_fix())
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "write_not_allowed"
    assert fake.calls == []
    line = _audit(capsys.readouterr().out, _HEAD_FIX)
    assert line["outcome"] == "write_not_allowed"
    assert line["read_only_exception"] is True


def test_health_summary_has_type_vouchers_and_no_archives() -> None:
    data = _gated(read_only_exceptions=_rules()).get("/v1/co/health").json()
    assert data["api_read_only_exceptions"] == [_SUMMARY_FIX]


class _ExBridge(FakeBridge):
    def health(self) -> dict:
        return {"ok": True, "version": "test", "read_only_exceptions": [_SUMMARY_FIX]}


def test_health_keeps_the_bridge_vouchers_summary() -> None:
    data = _gated(_ExBridge()).get("/v1/co/health").json()
    assert data["read_only_exceptions"] == [_SUMMARY_FIX]
