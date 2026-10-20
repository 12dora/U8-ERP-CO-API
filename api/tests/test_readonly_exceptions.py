"""只读例外（U8CO_READONLY_EXCEPTIONS）：解析失败让进程起不来；路由、账套、操作员（create 另看 archive）命中才穿过闸门。
op 为 customer_person、customer_fields、customer_address、create、delete（后两者须列 archives，目前只有 district_class）。假桥，不访问网络。
"""

from __future__ import annotations

import json

import pytest
from tests.test_co_dryrun import DryBridge
from tests.test_co_gate import FakeBridge, _client, _login
from u8co_api.config import load_settings
from u8co_api.errors import ApiError
from u8co_api.readonly_exceptions import allows, parse_exceptions

_RO = ("803",)
_BOTH = ("803", "801")
_PERSON = "/v1/co/archives/customer_person"
_UPDATE = "/v1/co/archives/update"
_CREATE = "/v1/co/archives/create"
_DELETE = "/v1/co/archives/delete"
_FIELDS = "/v1/co/archives/customer_fields"
_ADDRESS = "/v1/co/archives/customer_address"
_VOUCHER = "/v1/co/vouchers/create"
_VOUCHER_UPDATE = "/v1/co/vouchers/update"
_KEY = "replay-non-matching"
_ITEM = {
    "accounts": ["803"],
    "operators": ["svc01"],
    "type": "archives",
    "op": "customer_person",
}
_SUMMARY = {
    "accounts": ["803"],
    "operators": ["svc01"],
    "type": "archives",
    "op": "customer_person",
}


def _raw(**over) -> str:
    item = dict(_ITEM)
    item.update(over)
    return json.dumps([item])


def _rules(read_only: tuple[str, ...] = _RO, **over) -> tuple:
    return parse_exceptions(_raw(**over), read_only)


def _match(**over) -> dict:
    body = _login(operator="svc01", code="C900001", person="EX040001")
    body.update(over)
    return body


def _update(**over) -> dict:
    body = _login(
        operator="svc01",
        archive="customer",
        code="C900001",
        fields={"spec_operator": "EX040001"},
    )
    body.update(over)
    return body


def _gated(fake=None, **flags):
    flags.setdefault("read_only_accounts", _RO)
    flags.setdefault("read_only_exceptions", _rules())
    return _client(fake if fake is not None else FakeBridge(), **flags)


def _refused(response) -> None:
    assert response.status_code == 403, response.text
    error = response.json()["error"]
    assert error["code"] == "account_read_only"
    assert error["message"] == "该账套只开放读取"


def _audit(out: str, endpoint: str) -> dict:
    lines = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    found = [line for line in lines if line.get("endpoint") == endpoint]
    assert found, "missing audit line"
    return found[-1]


def _exits(raw: str, read_only: tuple[str, ...], text: str) -> None:
    with pytest.raises(SystemExit, match=text):
        parse_exceptions(raw, read_only)


# ---- 解析 ----


def test_empty_env_is_no_exceptions() -> None:
    assert parse_exceptions("", _RO) == ()
    assert parse_exceptions("[]", _RO) == ()


def test_unknown_key_stops_the_process() -> None:
    _exits(_raw(comment="x"), _RO, "不认识的键")


def test_removed_archive_and_fields_keys_stop_the_process() -> None:
    _exits(_raw(archive="customer"), _RO, "不认识的键")
    _exits(_raw(fields=["spec_operator"]), _RO, "不认识的键")


def test_account_must_be_read_only() -> None:
    _exits(_raw(accounts=["801"]), _RO, "不在 U8CO_READONLY_ACCOUNTS")


@pytest.mark.parametrize(
    ("over", "text"),
    (
        ({"type": "vouchers"}, "只能是 archives"),
        ({"op": "update"}, "只能是 customer_person、customer_fields、customer_address、create、delete"),
        ({"op": "list"}, "只能是 customer_person、customer_fields、customer_address、create、delete"),
        ({"op": "get"}, "只能是 customer_person、customer_fields、customer_address、create、delete"),
    ),
)
def test_type_and_op_must_be_the_opened_pair(over: dict, text: str) -> None:
    _exits(_raw(**over), _RO, text)


@pytest.mark.parametrize("op", ("customer_person", "customer_fields", "customer_address"))
def test_customer_ops_parse_without_archives(op: str) -> None:
    (item,) = _rules(op=op)
    assert (item.type, item.op, item.archives) == ("archives", op, None)
    assert item.summary() == {**_SUMMARY, "op": op}


@pytest.mark.parametrize("op", ("customer_person", "customer_fields", "customer_address"))
def test_customer_ops_reject_archives(op: str) -> None:
    _exits(_raw(op=op, archives=["district_class"]), _RO, "archives 只能用于 op=create 或 delete")
    _exits(_raw(op=op, archives=None), _RO, "archives 只能用于 op=create 或 delete")


@pytest.mark.parametrize("op", ("create", "delete"))
def test_create_and_delete_require_the_archives_list(op: str) -> None:
    _exits(_raw(op=op), _RO, f"archives 在 op={op} 时必须给出")
    _exits(_raw(op=op, archives=None), _RO, "必须是非空数组")
    _exits(_raw(op=op, archives=[]), _RO, "必须是非空数组")
    _exits(_raw(op=op, archives="district_class"), _RO, "必须是非空数组")
    _exits(_raw(op=op, archives=[""]), _RO, "非空字符串")
    _exits(_raw(op=op, archives=["district_class", "district_class"]), _RO, "重复")


@pytest.mark.parametrize("op", ("create", "delete"))
@pytest.mark.parametrize("kind", ("customer", "vendor", "project", "fa_card", "District_class", "unit"))
def test_archives_must_be_eligible(op: str, kind: str) -> None:
    _exits(_raw(op=op, archives=[kind]), _RO, "不能开放写入")
    _exits(_raw(op=op, archives=["district_class", kind]), _RO, "不能开放写入")


@pytest.mark.parametrize("op", ("create", "delete"))
def test_district_class_parses_and_summarises(op: str) -> None:
    (item,) = _rules(op=op, archives=["district_class"])
    assert (item.type, item.op, item.archives) == ("archives", op, ("district_class",))
    assert item.summary() == {**_SUMMARY, "op": op, "archives": ["district_class"]}


@pytest.mark.parametrize(
    ("over", "text"),
    (
        ({"operators": ["svc 01"]}, "操作员编码"),
        ({"operators": [" svc01"]}, "操作员编码"),
        ({"operators": ['svc"01']}, "操作员编码"),
        ({"operators": ["svc\\01"]}, "操作员编码"),
        ({"operators": ["svc\x7f01"]}, "操作员编码"),
        ({"operators": ["svc01", "SVC01"]}, "重复"),
    ),
)
def test_parse_applies_bridge_rules(over: dict, text: str) -> None:
    _exits(_raw(**over), _RO, text)


def test_load_settings_reads_create_and_customer_rules(monkeypatch) -> None:
    monkeypatch.setenv("U8CO_TRUST_FILE", "")
    monkeypatch.setenv("U8CO_ACCOUNTS", "803,801")
    monkeypatch.setenv("U8CO_READONLY_ACCOUNTS", "803")
    rules = [
        {**_ITEM, "op": "customer_fields"},
        {**_ITEM, "op": "customer_address"},
        {**_ITEM, "op": "create", "archives": ["district_class"]},
        {**_ITEM, "op": "delete", "archives": ["district_class"]},
    ]
    monkeypatch.setenv("U8CO_READONLY_EXCEPTIONS", json.dumps(rules))
    loaded = load_settings().read_only_exceptions
    assert [(item.op, item.archives) for item in loaded] == [
        ("customer_fields", None),
        ("customer_address", None),
        ("create", ("district_class",)),
        ("delete", ("district_class",)),
    ]


def test_load_settings_reads_the_env(monkeypatch) -> None:
    monkeypatch.setenv("U8CO_TRUST_FILE", "")
    monkeypatch.setenv("U8CO_ACCOUNTS", "803,801")
    monkeypatch.setenv("U8CO_READONLY_ACCOUNTS", "803")
    monkeypatch.setenv("U8CO_READONLY_EXCEPTIONS", _raw())
    loaded = load_settings()
    assert len(loaded.read_only_exceptions) == 1
    item = loaded.read_only_exceptions[0]
    assert (item.type, item.op) == ("archives", "customer_person")
    assert item.accounts == ("803",)
    assert item.operators == ("svc01",)
    monkeypatch.delenv("U8CO_READONLY_EXCEPTIONS")
    assert load_settings().read_only_exceptions == ()


# ---- 匹配 ----


def test_matching_customer_person_is_admitted() -> None:
    fake = FakeBridge()
    ok = _gated(fake).post(_PERSON, json=_match())
    assert ok.status_code == 200, ok.text
    assert len(fake.calls) == 1
    assert fake.calls[0][0] == "/v1/archives/customer_person"
    body = ok.json()
    assert body["ok"] is True
    assert body["code"] == "C900001"
    assert body["before"] is None
    assert body["after"] == "EX040001"


def test_archives_update_is_always_refused() -> None:
    fake = FakeBridge()
    _refused(_gated(fake).post(_UPDATE, json=_update()))
    assert fake.calls == []
    payload = _match()
    assert allows(_rules(), "co:archives/update", payload) is False
    assert allows(_rules(), "co:archives/customer_person", payload) is True


def test_create_and_delete_are_refused() -> None:
    fake = FakeBridge()
    client = _gated(fake)
    _refused(client.post(_CREATE, json=_update(fields={"name": "客户甲", "spec_operator": "EX040001"})))
    _refused(client.post(_DELETE, json=_login(operator="svc01", archive="customer", code="C900001")))
    _refused(client.post(_VOUCHER, json=_login(operator="svc01", type="sale_order", head={"a": "b"}, lines=[{"a": 1}])))
    assert fake.calls == []


def test_operator_not_listed_is_refused() -> None:
    fake = FakeBridge()
    _refused(_gated(fake).post(_PERSON, json=_match(operator="op001")))
    assert fake.calls == []


def test_other_account_is_refused() -> None:
    fake = FakeBridge()
    client = _gated(
        fake,
        co_accounts=_BOTH,
        read_only_accounts=_BOTH,
        read_only_exceptions=_rules(_BOTH),
    )
    _refused(client.post(_PERSON, json=_match(acc="801")))
    assert fake.calls == []


def test_dry_run_matching_shape_passes_the_gate() -> None:
    fake = DryBridge()
    ok = _gated(fake).post(_PERSON, json=_match(dry_run=True))
    assert ok.status_code == 200, ok.text
    assert ok.json()["dry_run"] is True
    assert len(fake.calls) == 1
    assert fake.calls[0][1]["dry_run"] is True


def test_matching_write_records_the_exception_outcome(capsys) -> None:
    fake = FakeBridge()
    ok = _gated(fake).post(_PERSON, json=_match())
    assert ok.status_code == 200, ok.text
    line = _audit(capsys.readouterr().out, _PERSON)
    assert line["outcome"] == "read_only_exception"
    assert line["read_only_exception"] is True
    assert (line["type"], line["op"]) == ("archives", "customer_person")


def test_bridge_error_after_admission_keeps_the_exception_marker(capsys) -> None:
    fake = FakeBridge()
    fake.error = ApiError(409, "u8_rejected", "U8 拒绝")
    denied = _gated(fake).post(_PERSON, json=_match())
    assert denied.status_code == 409, denied.text
    assert denied.json()["error"]["code"] == "u8_rejected"
    assert len(fake.calls) == 1
    line = _audit(capsys.readouterr().out, _PERSON)
    assert line["outcome"] == "u8_rejected"
    assert line["read_only_exception"] is True


def test_health_shows_the_summary() -> None:
    data = _gated().get("/v1/co/health").json()
    assert data["api_read_only_accounts"] == ["803"]
    assert data["api_read_only_exceptions"] == [_SUMMARY]


def test_no_env_keeps_today_s_refusal() -> None:
    fake = FakeBridge()
    client = _client(fake, read_only_accounts=_RO)
    _refused(client.post(_PERSON, json=_match()))
    _refused(client.post(_UPDATE, json=_update()))
    _refused(client.post(_VOUCHER, json=_login(type="sale_order", head={"a": "b"}, lines=[{"a": 1}])))
    assert fake.calls == []
    health = client.get("/v1/co/health").json()
    assert "api_read_only_exceptions" not in health
    assert "read_only_exceptions" not in health


def test_operator_matches_without_regard_to_case() -> None:
    fake = FakeBridge()
    client = _gated(fake)
    ok = client.post(_PERSON, json=_match(operator="SVC01"))
    assert ok.status_code == 200, ok.text
    assert fake.calls[0][1]["operator"] == "SVC01"
    padded = _match()
    padded["operator"] = " svc01"
    assert allows(_rules(), "co:archives/customer_person", padded) is False


def test_vouchers_update_type_spoof_is_refused() -> None:
    spoof = _match(type="archives")
    assert allows(_rules(), "co:archives/customer_person", spoof) is True
    assert allows(_rules(), "co:vouchers/update", spoof) is False
    fake = FakeBridge()
    body = _login(operator="svc01", type="sale_order", id=1, head={"cMemo": "a"})
    _refused(_gated(fake).post(_VOUCHER_UPDATE, json=body))
    assert fake.calls == []


def test_idempotency_replay_of_archives_update_is_refused() -> None:
    fake = FakeBridge()
    _refused(_gated(fake).post(_UPDATE, json=_update(), headers={"Idempotency-Key": _KEY}))
    assert fake.calls == []


def test_policy_still_denies_after_exception_admission(tmp_path, capsys) -> None:
    path = tmp_path / "write-policy.json"
    path.write_text(json.dumps({"version": 1}), encoding="utf-8")
    fake = FakeBridge()
    client = _gated(fake, write_policy_file=str(path))
    denied = client.post(_PERSON, json=_match())
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "write_not_allowed"
    assert fake.calls == []
    line = _audit(capsys.readouterr().out, _PERSON)
    assert line["outcome"] == "write_not_allowed"
    assert line["read_only_exception"] is True


class _ExBridge(FakeBridge):
    def health(self) -> dict:
        return {"ok": True, "version": "test", "read_only_exceptions": [_SUMMARY]}


def test_health_keeps_bridge_exceptions() -> None:
    data = _gated(_ExBridge()).get("/v1/co/health").json()
    assert data["read_only_exceptions"] == [_SUMMARY]
    assert data["api_read_only_exceptions"][0]["op"] == "customer_person"
