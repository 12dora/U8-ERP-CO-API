"""客户字段 customer_fields 的键表：各类型的合法 / 非法值、必填、expect ⊆ fields、键名与桥的声明表一致。
假桥，不访问网络。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests.test_co_arc_cusfields import _EXPECT, _PATH, _body, _FieldsBridge
from tests.test_co_gate import FakeBridge, _client
from u8co_api.co_models_arc_cusfields_keys import FIELD_KEYS, KEYS

# ---- 键表驱动：每种类型的合法 / 非法值、必填、未知键、expect ⊆ fields、键名区分大小写 ----

_VALID = {
    "text": ("示例文本", "a" * 20, "", " 首尾空白 "),
    "postcode": ("200000", "ab-12", "a" * 20, ""),
    "code": ("A001", "A" * 10, ""),
    "date": ("2026-10-20", "1900-01-01", ""),
    "bit": ("0", "1"),
    "int": ("0", "-5", "2147483647", ""),
    "num": ("0", "5.0", "12.12345678", ""),
}
_INVALID = {
    "postcode": ("ABC", "a b", "a" * 21, "20\n00"),
    "code": (" A001", "A001 ", "A\n001", "A" * 61),
    "date": ("2026/10/20", "2026-02-30", "1899-12-31", "20261020", "2026-10-20 08:00"),
    "bit": ("", "2", "true", " 1"),
    "int": ("1.5", "2147483648", "-2147483649", "1e3", "x"),
    "num": ("1e3", "1.123456789", "abc", "1,5", ".5"),
}
_RANGED = {"discount_rate": ("0", "100", "99.99"), "icustaxrate": ("0", "99.99")}
_OUT_OF_RANGE = {"discount_rate": ("-0.1", "100.1"), "fcusdiscountrate": ("101",), "icustaxrate": ("100", "-1")}


def _post(fields: dict, **over) -> object:
    return _client(_FieldsBridge()).post(_PATH, json=_body(fields=fields, **over))


def _cases(values: dict) -> list[tuple[str, str]]:
    found = []
    for key, spec in KEYS.items():
        for value in values.get(spec.kind, ()):
            blank = value.strip() == ""
            if blank and spec.required or spec.kind == "text" and len(value.strip()) > spec.limit:
                continue
            found.append((key, value))
    return found


@pytest.mark.parametrize(("key", "value"), _cases(_VALID))
def test_every_kind_accepts_valid_values(key: str, value: str) -> None:
    fake = _FieldsBridge()
    ok = _client(fake).post(_PATH, json=_body(fields={key: value}))
    assert ok.status_code == 200, (key, value, ok.text)
    assert fake.calls[-1][1]["fields"] == {key: value}  # 原样转给桥，由桥去空白、规整小数


@pytest.mark.parametrize(("key", "value"), _cases(_INVALID))
def test_every_kind_rejects_invalid_values(key: str, value: str) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_PATH, json=_body(fields={key: value}))
    assert denied.status_code == 400, (key, value, denied.text)
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


@pytest.mark.parametrize("key", [k for k, s in KEYS.items() if s.kind == "text"])
def test_text_keys_enforce_their_limit_and_control_characters(key: str) -> None:
    spec = KEYS[key]
    assert _post({key: "x" * spec.limit}).status_code == 200
    assert _post({key: "x" * (spec.limit + 1)}).status_code == 400
    assert _post({key: "甲\n乙"}).status_code == 400
    assert _post({key: 7}).status_code == 400
    assert _post({key: None}).status_code == 400


@pytest.mark.parametrize("key", [k for k, s in KEYS.items() if s.kind == "code"])
def test_code_keys_enforce_their_limit_and_whitespace(key: str) -> None:
    spec = KEYS[key]
    assert _post({key: "A" * spec.limit}).status_code == 200
    for bad in ("A" * (spec.limit + 1), " A", "A ", "A\tB", 1):
        assert _post({key: bad}).status_code == 400, (key, bad)


@pytest.mark.parametrize("key", [k for k, s in KEYS.items() if s.required])
def test_required_keys_cannot_be_empty(key: str) -> None:
    assert _post({key: ""}).status_code == 400
    assert _post({key: "  "}).status_code == 400
    assert _post({key: "X"}, expect={key: ""}).status_code == 200  # expect 可以是空


@pytest.mark.parametrize("key", [k for k, s in KEYS.items() if not s.required and s.kind != "bit"])
def test_optional_keys_accept_the_clearing_empty_string(key: str) -> None:
    assert _post({key: ""}, expect={key: ""}).status_code == 200


def test_required_keys_are_exactly_these() -> None:
    assert sorted(k for k, s in KEYS.items() if s.required) == ["abbrname", "ccusexch_name", "name", "sort_code"]


@pytest.mark.parametrize(("key", "values"), tuple(_RANGED.items()))
def test_number_ranges_accept_the_boundaries(key: str, values: tuple[str, ...]) -> None:
    for value in values:
        assert _post({key: value}).status_code == 200, (key, value)


@pytest.mark.parametrize(("key", "values"), tuple(_OUT_OF_RANGE.items()))
def test_number_ranges_reject_outside_values(key: str, values: tuple[str, ...]) -> None:
    for value in values:
        assert _post({key: value}).status_code == 400, (key, value)


def test_self_define_kinds_follow_the_item_number() -> None:
    assert [KEYS[f"self_define{n}"].spec for n in (1, 3, 4, 6, 7, 10, 11, 12, 13, 14, 15, 16)] == [
        "text(20)", "text(20)", "text(60)", "text(60)", "text(120)", "text(120)",
        "int", "int", "num", "num", "date", "date",
    ]
    assert _post({"self_define1": "x" * 21}).status_code == 400
    assert _post({"self_define6": "x" * 61}).status_code == 400
    assert _post({"self_define10": "x" * 120}).status_code == 200
    assert _post({"self_define10": "x" * 121}).status_code == 400
    assert _post({"self_define13": "1234567890123456"}).status_code == 400
    assert _post({"self_define11": "12"}).status_code == 200
    assert _post({"self_define11": "1.5"}).status_code == 400
    assert _post({"self_define13": "1234567.12345678"}).status_code == 200
    assert _post({"self_define15": "2026-10-20"}).status_code == 200
    assert _post({"self_define17": "x"}).status_code == 400


def test_key_names_are_lowercase_and_case_sensitive() -> None:
    assert all(key == key.lower() for key in FIELD_KEYS)
    assert len(FIELD_KEYS) == len(set(FIELD_KEYS))
    for key in ("Abbrname", "NAME", "Tax_No", "ccusBankCode", "Self_Define1", "cCusPerson"):
        assert _post({key: "x"}).status_code == 400, key
    assert _post({"ccusbankcode": "x"}).status_code == 200


def test_keys_map_to_the_documented_columns() -> None:
    expected = {
        "contact": "cCusPerson", "mobile": "cCusHand", "bank_open": "cCusBank", "bank_acc_number": "cCusAccount",
        "ccusbankcode": "cCusBankCode", "devliver_site": "cCusOAddress", "sort_code": "cCCCode",
        "ccustradeccode": "cCusTradeCCode", "head_corp_code": "cCusHeadCode", "seed_date": "dCusDevDate",
        "crelvendor": "cRelVendor", "discount_rate": "iCusDisRate", "memo": "cMemo", "self_define16": "cCusDefine16",
    }
    for key, column in expected.items():
        assert KEYS[key].column == column
    assert len({spec.column for spec in KEYS.values()}) == len(KEYS)


def test_expect_keys_must_be_inside_fields_for_every_key() -> None:
    for key, spec in KEYS.items():
        value = next(v for v in _VALID[spec.kind] if v.strip())
        other = "memo" if key != "memo" else "phone"
        assert _post({key: value}).status_code == 200, key
        assert _post({key: value}, expect={other: ""}).status_code == 400, key


def test_expect_is_lenient_for_stored_values_but_syntactic() -> None:
    assert _post({"name": "x"}, expect={"name": " 旧 名称 "}).status_code == 200  # 旧数据可以有首尾空白
    assert _post({"postcode": "1"}, expect={"postcode": "ABC 1"}).status_code == 200
    assert _post({"discount_rate": "1"}, expect={"discount_rate": "5.00"}).status_code == 200
    assert _post({"discount_rate": "1"}, expect={"discount_rate": "abc"}).status_code == 400
    assert _post({"seed_date": "2026-01-01"}, expect={"seed_date": "2026/01/01"}).status_code == 400
    assert _post({"bcusdomestic": "1"}, expect={"bcusdomestic": "2"}).status_code == 400
    assert _post({"bcusdomestic": "1"}, expect={"bcusdomestic": ""}).status_code == 400  # 0/1 列不为空，空串永远对不上
    assert _post({"self_define11": "1"}, expect={"self_define11": "x"}).status_code == 400
    assert _post({"ccusbankcode": "A"}, expect={"ccusbankcode": "A "}).status_code == 400
    assert _post({"memo": "x"}, expect={"memo": "y" * 1001}).status_code == 400


def test_end_date_may_not_precede_seed_date_in_the_same_request() -> None:
    assert _post({"seed_date": "2026-05-01", "end_date": "2026-04-30"}).status_code == 400
    assert _post({"seed_date": "2026-05-01", "end_date": "2026-05-01"}).status_code == 200
    assert _post({"seed_date": "", "end_date": "2020-01-01"}).status_code == 200
    assert _post({"seed_date": "2026-05-01", "end_date": ""}).status_code == 200


def test_many_new_keys_are_forwarded_together() -> None:
    fields = {
        "contact": "联系人甲", "bank_open": "示例银行", "bank_acc_number": "6222000000000000",
        "ccustradeccode": "01", "self_define1": "文本A", "bcusoverseas": "1", "discount_rate": "5",
        "head_corp_code": "", "crelvendor": "",
    }
    fake = _FieldsBridge()
    ok = _client(fake).post(_PATH, json=_body(fields=fields, expect={"contact": "", "bank_open": ""}))
    assert ok.status_code == 200, ok.text
    assert fake.calls[-1][1]["fields"] == fields
    assert ok.json()["after"] == {**_EXPECT, **fields}


_BRIDGE_TABLE = Path(__file__).resolve().parents[2] / "co" / "bridge" / "src" / "ArcCusCol.cs"
_ROW = re.compile(
    r'ArcCusCol\.(?:Text|Ref)\("([a-z0-9_]+)", "(\w+)"|ArcCusCol\.Of\(ArcCusCol\.K\w+, "([a-z0-9_]+)", "(\w+)"'
)


def test_key_table_matches_the_bridge_declaration_table() -> None:
    """API 键表与桥的声明表（ArcCusCol.cs）逐键、逐列一致；自定义项由桥里的循环生成。"""
    if not _BRIDGE_TABLE.exists():
        pytest.skip("没有桥源码")
    source = _BRIDGE_TABLE.read_text(encoding="utf-8")
    bridge = {first or third: second or fourth for first, second, third, fourth in _ROW.findall(source)}
    bridge.update({f"self_define{n}": f"cCusDefine{n}" for n in range(1, 17)})
    assert bridge == {key: spec.column for key, spec in KEYS.items()}
