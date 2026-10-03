"""经营管理的配置：信任项的 mgmt_claim / mgmt_scope（问题即起不来）、令牌里的经营管理权限、
利润表行定义 U8CO_MGMT_LINES_FILE（严格校验）、公司间对照的 rev_cogs 抵销规则。不访问网络。"""

from __future__ import annotations

import copy
import json
import os

import pytest
from tests.ic_fakes import map_data
from tests.support import base_claims, entry
from u8co_api.auth import caller_from
from u8co_api.co_access import access_of, may_call
from u8co_api.co_ic_map import parse_ic_map
from u8co_api.co_mgmt_lines import DEFAULT_LINES, load_mgmt_lines, parse_mgmt_lines
from u8co_api.config import load_settings
from u8co_api.trust import load_trust, parse_entry

_ROW = {
    "name": "app-a",
    "issuer": "https://idp.example.com",
    "audience": "u8co-api",
    "jwks_url": "https://idp.example.com/oauth/jwks",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("U8CO_"):
            monkeypatch.delenv(name)
    trust = tmp_path / "trust.json"
    trust.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("U8CO_TRUST_FILE", str(trust))
    monkeypatch.setenv("U8CO_ACCOUNTS", "801,802,803")
    return monkeypatch


def test_mgmt_is_off_unless_the_entry_names_a_claim() -> None:
    item = parse_entry(dict(_ROW))
    assert (item.mgmt_claim, item.mgmt_scope) == ("", "")
    item = parse_entry(dict(_ROW, mgmt_claim="u8co_mgmt", mgmt_scope="u8co.mgmt"))
    assert (item.mgmt_claim, item.mgmt_scope) == ("u8co_mgmt", "u8co.mgmt")


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"mgmt_claim": "u8co_read"}, "mgmt_claim"),
        ({"mgmt_claim": "u8co_write"}, "mgmt_claim"),
        ({"mgmt_claim": "accs", "accounts_claim": "accs"}, "mgmt_claim"),
        ({"mgmt_claim": "bad claim"}, "声明名无效"),
        ({"mgmt_claim": 1}, "字符串"),
        ({"mgmt_scope": "u8co.read", "read_scope": "u8co.read"}, "mgmt_scope"),
        ({"mgmt_scope": "two words"}, "scope 无效"),
    ],
)
def test_bad_mgmt_settings_stop_the_process(change: dict, needle: str) -> None:
    with pytest.raises(SystemExit, match=needle):
        parse_entry(dict(_ROW, **change))


def test_env_entry_reads_the_mgmt_claim() -> None:
    found = load_trust(
        {
            "U8CO_TRUST_FILE": "",
            "U8CO_OIDC_ISSUER": _ROW["issuer"],
            "U8CO_OIDC_AUDIENCE": _ROW["audience"],
            "U8CO_OIDC_MGMT_CLAIM": "u8co_mgmt",
        }
    )
    assert found[-1].mgmt_claim == "u8co_mgmt"


def test_caller_mgmt_needs_a_true_claim_or_the_scope() -> None:
    spec = entry(mgmt_claim="u8co_mgmt", mgmt_scope="u8co.mgmt")
    assert caller_from(base_claims(u8co_mgmt=True), spec).mgmt is True
    assert caller_from(base_claims(scope="openid u8co.mgmt"), spec).mgmt is True
    assert caller_from(base_claims(u8co_mgmt="true"), spec).mgmt is False
    assert caller_from(base_claims(u8co_write=True, u8co_read=True), spec).mgmt is False
    assert caller_from(base_claims(u8co_mgmt=True), entry()).mgmt is False


def test_mgmt_access_is_separate_from_read_and_write() -> None:
    assert access_of("co:mgmt/pnl") == "mgmt"
    assert may_call(True, True, "co:mgmt/pnl") is False
    assert may_call(False, False, "co:mgmt/pnl", True) is True
    assert may_call(False, False, "co:reports/gl_balance", True) is False
    assert may_call(True, False, "co:vouchers/create") is True


def test_default_lines_are_generic_first_level_codes() -> None:
    assert load_mgmt_lines("") is DEFAULT_LINES
    by_id = {line.id: line for line in DEFAULT_LINES.lines}
    assert by_id["revenue"].codes_for("801") == ("6001", "6051")
    assert by_id["finance"].codes_for("801") == ("6603",)
    assert all(len(code) == 4 for line in DEFAULT_LINES.lines for code in line.codes_for("801"))
    assert not DEFAULT_LINES.needs_leaf("801")
    assert [item.id for item in DEFAULT_LINES.derived] == [
        "gross_profit",
        "operating_profit",
        "total_profit",
        "net_profit",
    ]


_LINES = {
    "version": 1,
    "lines": [
        {"id": "revenue", "name": "营业收入", "codes": {"*": ["6001"], "801": ["600111"]}, "sign": "income"},
        {"id": "cogs", "name": "营业成本", "codes": {"*": ["6401"]}, "sign": "expense"},
    ],
    "derived": [{"id": "gross_profit", "name": "毛利", "plus": ["revenue"], "minus": ["cogs"]}],
}


def _lines(change) -> dict:
    data = copy.deepcopy(_LINES)
    change(data)
    return data


def test_site_lines_override_per_account() -> None:
    found = parse_mgmt_lines(copy.deepcopy(_LINES))
    revenue = found.lines[0]
    assert revenue.codes_for("801") == ("600111",) and revenue.codes_for("802") == ("6001",)
    assert found.needs_leaf("801") and not found.needs_leaf("802")
    assert found.source == "file" and found.name_of("gross_profit") == "毛利"


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        (lambda d: d.update(extra=1), "不认识的键"),
        (lambda d: d.update(version=2), "version"),
        (lambda d: d.update(version=True), "version"),
        (lambda d: d.pop("derived"), "缺少: derived"),
        (lambda d: d.update(lines=[]), "lines"),
        (lambda d: d["lines"].pop(1), "cogs"),
        (lambda d: d["lines"][0].update(id="Revenue"), "id"),
        (lambda d: d["lines"][1].update(id="revenue"), "重复"),
        (lambda d: d["lines"][0].update(sign="plus"), "sign"),
        (lambda d: d["lines"][0].update(note="x"), "不认识的键: note"),
        (lambda d: d["lines"][0]["codes"].update({"80": ["6001"]}), "账套号"),
        (lambda d: d["lines"][0]["codes"].update({"802": []}), "非空"),
        (lambda d: d["lines"][0]["codes"].update({"802": ["60-01"]}), "数字"),
        (lambda d: d["lines"][0]["codes"].update({"802": ["6001", "6001"]}), "重复"),
        (lambda d: d["derived"][0].update(plus=["nope"]), "nope"),
        (lambda d: d["derived"][0].update(plus=[]), "非空"),
        (lambda d: d["derived"][0].update(id="cogs"), "重复"),
        (lambda d: d["derived"].append({"id": "x", "name": "x", "plus": ["y"]}), "'y'"),
    ],
)
def test_bad_lines_file_is_rejected(change, needle: str) -> None:
    with pytest.raises(SystemExit, match=needle):
        parse_mgmt_lines(_lines(change))


def test_lines_file_is_loaded_at_startup(env, tmp_path) -> None:
    path = tmp_path / "lines.json"
    path.write_text(json.dumps(_LINES, ensure_ascii=False), encoding="utf-8")
    env.setenv("U8CO_MGMT_LINES_FILE", str(path))
    assert load_settings().mgmt_lines.source == "file"
    path.write_text("{", encoding="utf-8")
    with pytest.raises(SystemExit, match="U8CO_MGMT_LINES_FILE"):
        load_settings()


def test_unset_lines_file_uses_the_defaults(env) -> None:
    assert load_settings().mgmt_lines is DEFAULT_LINES


_RULE = {
    "rule": "rev_cogs",
    "seller": "802",
    "buyer": "801",
    "revenue_source": "sales_to_customer",
    "cost_source": "gl_cogs_all",
}


def _with_rule(**change) -> dict:
    data = map_data()
    data["groups"][0]["elim"].append(dict(_RULE, **change))
    return data


def test_rev_cogs_rule_is_parsed() -> None:
    group = parse_ic_map(_with_rule()).groups[0]
    rule = group.elim[-1]
    assert (rule.rule, rule.seller, rule.buyer, rule.pairs) == ("rev_cogs", "802", "801", ())
    assert (rule.revenue_source, rule.cost_source) == ("sales_to_customer", "gl_cogs_all")
    assert group.elim[0].rule == "ar_ap"


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"seller": "809"}, "不在本组"),
        ({"buyer": "802"}, "同一个账套"),
        ({"revenue_source": "gl"}, "revenue_source"),
        ({"cost_source": "gl"}, "cost_source"),
        ({"pairs": []}, "不认识的键: pairs"),
        ({"seller": "803", "buyer": "802", "cost_source": "ia_to_customer", "revenue_source": "gl_revenue_all"}, None),
    ],
)
def test_bad_rev_cogs_rule_is_rejected(change: dict, needle: str | None) -> None:
    data = _with_rule(**change)
    if needle is None:
        # 803 的 as_customer 里有 802（C900002），按客户取数可以。
        assert parse_ic_map(data).groups[0].elim[-1].seller == "803"
        return
    with pytest.raises(SystemExit, match=needle):
        parse_ic_map(data)


def test_rev_cogs_by_customer_needs_the_customer_code() -> None:
    data = _with_rule()
    data["groups"][0]["as_customer"]["802"].pop("801")
    with pytest.raises(SystemExit, match="as_customer.802"):
        parse_ic_map(data)
    data = _with_rule(revenue_source="gl_revenue_all")
    data["groups"][0]["as_customer"]["802"].pop("801")
    assert parse_ic_map(data).groups[0].elim[-1].revenue_source == "gl_revenue_all"


def test_duplicate_rev_cogs_pair_is_rejected() -> None:
    data = _with_rule()
    data["groups"][0]["elim"].append(dict(_RULE))
    with pytest.raises(SystemExit, match="重复"):
        parse_ic_map(data)
