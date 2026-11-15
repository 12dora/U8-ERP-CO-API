"""公司间对照（U8CO_IC_MAP_FILE）：解析、严格校验（问题即起不来）、按账套找组。不访问网络。"""

from __future__ import annotations

import json
import os

import pytest
from tests.ic_fakes import map_data, sample_map
from u8co_api.co_ic_map import load_ic_map, parse_ic_map
from u8co_api.config import load_settings
from u8co_api.errors import ApiError


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


def _file(tmp_path, data: object) -> str:
    path = tmp_path / "ic.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_sample_map_is_parsed() -> None:
    found = sample_map()
    group = found.group_of({"801", "803"})
    assert group.id == "grp1"
    assert group.name("802") == "乙公司"
    assert group.customer_code("803", "801") == "C900001"
    assert group.vendor_code("801", "803") == "S900002"
    assert group.company_of_vendor("802", "S900001") == "803"
    assert group.inventory_of("803", "B0001").id == "lig"
    assert group.inventory_of("803", "A901") is None
    assert group.inventory_codes("802") == ["A901"]
    assert group.gl_logical("801", "220299") == "ic_ap"
    pair = group.elim[0].pairs[0]
    assert (pair.ar.acc, pair.ap.acc, pair.ap.partner) == ("803", "801", "S900002")
    assert found.group_of({"998", "999"}).id == "grp9"


def test_accounts_from_two_groups_are_a_mismatch() -> None:
    with pytest.raises(ApiError) as caught:
        sample_map().group_of({"801", "998"})
    assert (caught.value.status, caught.value.code) == (400, "ic_group_mismatch")
    assert caught.value.message == "账套不在同一公司组"


def test_unset_file_leaves_ic_off(env) -> None:
    assert load_settings().ic_map is None


def test_file_is_loaded_at_startup(env, tmp_path) -> None:
    env.setenv("U8CO_IC_MAP_FILE", _file(tmp_path, map_data()))
    assert load_settings().ic_map.group_of({"802"}).id == "grp1"


@pytest.mark.parametrize("text", ["{", "[]", '{"groups": []}', '{"groups": [], "x": 1}'])
def test_bad_file_stops_startup(env, tmp_path, text: str) -> None:
    env.setenv("U8CO_IC_MAP_FILE", _file(tmp_path, text))
    with pytest.raises(SystemExit, match="U8CO_IC_MAP_FILE"):
        load_settings()


def test_missing_file_stops_startup(tmp_path) -> None:
    with pytest.raises(SystemExit, match="读不到"):
        load_ic_map(str(tmp_path / "absent.json"))


def _broken(change) -> dict:
    data = map_data()
    change(data["groups"][0])
    return data


_BROKEN = [
    (lambda g: g.update(extra=1), "不认识的键: extra"),
    (lambda g: g.pop("accounts"), "缺少: accounts"),
    (lambda g: g.update(id="组 1"), "id"),
    (lambda g: g.update(accounts=["801"]), "至少 2 个"),
    (lambda g: g.update(accounts=["801", "801"]), "重复"),
    (lambda g: g.update(accounts=["801", 802]), "三位数字"),
    (lambda g: g["names"].update({"809": "丁"}), "不在本组"),
    (lambda g: g["as_customer"]["801"].update({"801": "C1"}), "自己"),
    (lambda g: g["as_customer"]["801"].update({"803": "C900001"}), "同一个编码"),
    (lambda g: g["inventory"][0].update(match="name"), "match"),
    (lambda g: g["inventory"][1].update(id="inv1"), "id 有重复"),
    (lambda g: g["inventory"][1]["codes"].update({"801": "A901"}), "出现在多项里"),
    (lambda g: g["gl"][0].update(logical="IC AR"), "logical"),
    (lambda g: g["gl"][0]["codes"].update({"801": "11-22"}), "编码"),
    (lambda g: g["elim"][0].update(rule="net"), "rule"),
    # rev_cogs 的键与 ar_ap 不同（co_mgmt_pnl），带 pairs 的 rev_cogs 无效。
    (lambda g: g["elim"][0].update(rule="rev_cogs"), "不认识的键: pairs"),
    (lambda g: g["elim"][0]["pairs"][0].update(ar=["803", "ic_xx", "C900001"]), "ic_xx"),
    (lambda g: g["elim"][0]["pairs"][0].update(ar=["803", "ic_ar", "C900002"]), "不是账套 801"),
    (lambda g: g["elim"][0]["pairs"][0].update(ap=["801", "ic_ap", "S900001"]), "不是账套 803"),
]


@pytest.mark.parametrize(("change", "needle"), _BROKEN)
def test_invalid_group_is_rejected(change, needle: str) -> None:
    with pytest.raises(SystemExit, match=needle):
        parse_ic_map(_broken(change))


def test_an_account_in_two_groups_is_rejected() -> None:
    data = map_data()
    data["groups"][1]["accounts"] = ["803", "998"]
    with pytest.raises(SystemExit, match="多个组"):
        parse_ic_map(data)
    data = map_data()
    data["groups"][1]["id"] = "grp1"
    with pytest.raises(SystemExit, match="重复"):
        parse_ic_map(data)
