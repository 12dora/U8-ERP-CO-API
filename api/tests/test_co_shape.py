"""响应裁剪 co_shape：fields 投影和 compact 去空值。纯函数，不访问网络。"""

from __future__ import annotations

import copy

import pytest
from u8co_api.co_shape import FieldSpec, parse_fields, shape
from u8co_api.errors import ApiError


def _load() -> dict:
    return {
        "ok": True,
        "type": "sale_order",
        "id": 7,
        "code": "0000000007",
        "state": {"verified": False},
        "head": {"cCusCode": "C001", "cMemo": "", "dDate": "2024-06-01", "iRate": 0, "bFlag": False},
        "lines": [
            {"AutoID": 1, "cInvCode": "A01", "iQuantity": 5.0, "cFree1": None, "extra": []},
            {"AutoID": 2, "cInvCode": "A02", "iQuantity": 0, "cFree1": "  ", "extra": {}},
        ],
    }


# ---- parse_fields ----


def test_parse_none_means_no_projection() -> None:
    assert parse_fields(None) is None


def test_parse_bare_and_prefixed_lowercased() -> None:
    spec = parse_fields(" cInvCode , Head.cCusCode,lines.iQuantity,docs.head.ID,docs.lines.AutoId,items.Name,fields.X")
    assert spec == FieldSpec(
        frozenset({"cinvcode"}),
        {
            "head": frozenset({"ccuscode"}),
            "lines": frozenset({"iquantity"}),
            "docs.head": frozenset({"id"}),
            "docs.lines": frozenset({"autoid"}),
            "items": frozenset({"name"}),
            "fields": frozenset({"x"}),
        },
    )


def test_parse_100_entries_ok() -> None:
    spec = parse_fields(",".join(f"f{i}" for i in range(100)))
    assert len(spec.bare) == 100


@pytest.mark.parametrize(
    "text",
    (
        "",
        "  ",
        "a,,b",
        "a,",
        "a b",
        "head.",
        "docs.head",
        "other.x",
        "head.x.y",
        "客户",
        "a-b",
        ",".join(f"f{i}" for i in range(101)),
    ),
)
def test_parse_rejects_bad_syntax(text: str) -> None:
    with pytest.raises(ApiError) as caught:
        parse_fields(text)
    assert caught.value.status == 400
    assert caught.value.code == "bad_request"
    assert caught.value.field == "fields"


def test_names_for() -> None:
    spec = parse_fields("a,head.b")
    assert spec.names_for("head") == frozenset({"a", "b"})
    assert spec.names_for("lines") == frozenset({"a"})
    only = parse_fields("head.b")
    assert only.names_for("head") == frozenset({"b"})
    assert only.names_for("lines") is None
    assert only.names_for("docs.head") is None


# ---- shape：投影 ----


def test_no_options_returns_same_object() -> None:
    result = _load()
    assert shape(result, None, False) is result


def test_projection_bare_applies_to_all_containers_case_insensitive() -> None:
    out = shape(_load(), "ccuscode,CINVCODE", False)
    assert out["head"] == {"cCusCode": "C001"}
    assert out["lines"] == [{"cInvCode": "A01"}, {"cInvCode": "A02"}]


def test_projection_keeps_envelope() -> None:
    out = shape(_load(), "cinvcode", False)
    for key in ("ok", "type", "id", "code", "state"):
        assert out[key] == _load()[key]


def test_prefixed_names_only_for_their_container() -> None:
    out = shape(_load(), "head.dDate,lines.AutoID", False)
    assert out["head"] == {"dDate": "2024-06-01"}
    assert out["lines"] == [{"AutoID": 1}, {"AutoID": 2}]


def test_container_without_applicable_names_untouched() -> None:
    out = shape(_load(), "head.cCusCode", False)
    assert out["head"] == {"cCusCode": "C001"}
    assert out["lines"] == _load()["lines"]


def test_container_with_no_selected_key_becomes_empty() -> None:
    out = shape(_load(), "nothing_here", False)
    assert out["head"] == {}
    assert out["lines"] == [{}, {}]


def test_items_and_fields_containers() -> None:
    result = {
        "ok": True,
        "next": 9,
        "items": [{"code": "C1", "name": "甲", "ufts": "1"}, "odd"],
        "fields": {"ccuscode": "C1", "ccusname": "甲"},
    }
    out = shape(result, "code,fields.ccusname", False)
    assert out["items"] == [{"code": "C1"}, "odd"]
    assert out["fields"] == {"ccusname": "甲"}
    assert out["next"] == 9


def test_items_projection_keeps_id_code_error_ok() -> None:
    result = {
        "ok": True,
        "items": [
            {"ok": True, "id": 1, "code": "0001", "head": {"ccode": "0001"}, "state": {}},
            {"id": 2, "error": {"code": "not_found", "message": "单据不存在", "retryable": False}},
            {"Code": "C9", "Error": {"code": "no_permission"}, "name": "x"},
        ],
    }
    out = shape(result, "ccode", False)
    assert out["items"] == [
        {"ok": True, "id": 1, "code": "0001"},
        {"id": 2, "error": {"code": "not_found", "message": "单据不存在", "retryable": False}},
        {"Code": "C9", "Error": {"code": "no_permission"}},
    ]


def test_items_kept_keys_still_compacted() -> None:
    result = {"ok": True, "items": [{"id": 1, "code": "", "error": None, "ok": False, "name": "x"}]}
    assert shape(result, "ccode", True)["items"] == [{"id": 1, "ok": False}]
    assert shape(result, "ccode", False)["items"] == [{"id": 1, "code": "", "error": None, "ok": False}]


def test_kept_keys_only_for_items() -> None:
    result = {"ok": True, "head": {"id": 1, "code": "c", "x": 2}, "lines": [{"id": 3, "x": 4}]}
    out = shape(result, "x", False)
    assert out["head"] == {"x": 2}
    assert out["lines"] == [{"x": 4}]


def test_items_untouched_without_applicable_names() -> None:
    result = {"ok": True, "items": [{"id": 1, "name": "x"}]}
    assert shape(result, "head.x", False)["items"] == [{"id": 1, "name": "x"}]


def test_docs_head_and_lines() -> None:
    result = {
        "ok": True,
        "dry_run": True,
        "head": {"x": 1},
        "docs": [
            {
                "type": "sale_order",
                "id": 1,
                "state": "exists",
                "head": {"ccuscode": "C", "cmemo": ""},
                "lines": [{"a": 1, "b": 2}],
            },
            {"type": "sale_order", "id": 2, "state": "deleted"},
            "odd",
        ],
    }
    out = shape(result, "docs.head.ccuscode,docs.lines.b", False)
    assert out["head"] == {"x": 1}
    assert out["docs"][0]["head"] == {"ccuscode": "C"}
    assert out["docs"][0]["lines"] == [{"b": 2}]
    assert out["docs"][0]["type"] == "sale_order"
    assert out["docs"][1] == {"type": "sale_order", "id": 2, "state": "deleted"}
    assert out["docs"][2] == "odd"


def test_bare_names_apply_to_docs_too() -> None:
    result = {"ok": True, "docs": [{"id": 1, "head": {"a": 1, "b": 2}, "lines": [{"a": 3, "c": 4}]}]}
    out = shape(result, "a", False)
    assert out["docs"][0] == {"id": 1, "head": {"a": 1}, "lines": [{"a": 3}]}


def test_non_dict_containers_left_alone() -> None:
    result = {"ok": True, "head": "text", "lines": 3, "items": None, "fields": [1, 2]}
    out = shape(result, "a", True)
    assert out == {"ok": True, "head": "text", "lines": 3, "items": None, "fields": [1, 2]}


def test_bad_fields_raises() -> None:
    with pytest.raises(ApiError) as caught:
        shape(_load(), "a,,b", False)
    assert caught.value.field == "fields"


# ---- shape：compact ----


def test_compact_drops_empty_values_keeps_zero_and_false() -> None:
    out = shape(_load(), None, True)
    assert out["head"] == {"cCusCode": "C001", "dDate": "2024-06-01", "iRate": 0, "bFlag": False}
    assert out["lines"] == [
        {"AutoID": 1, "cInvCode": "A01", "iQuantity": 5.0},
        {"AutoID": 2, "cInvCode": "A02", "iQuantity": 0},
    ]


def test_compact_does_not_touch_envelope() -> None:
    result = {"ok": True, "message": "", "warnings": [], "detail": {}, "head": {"a": ""}}
    out = shape(result, None, True)
    assert out == {"ok": True, "message": "", "warnings": [], "detail": {}, "head": {}}


def test_compact_with_projection() -> None:
    out = shape(_load(), "cMemo,cCusCode,cFree1", True)
    assert out["head"] == {"cCusCode": "C001"}
    assert out["lines"] == [{}, {}]


def test_compact_in_docs() -> None:
    result = {"ok": True, "docs": [{"id": 1, "head": {"a": None, "b": " "}, "lines": [{"c": [], "d": 1}]}]}
    out = shape(result, None, True)
    assert out["docs"][0] == {"id": 1, "head": {}, "lines": [{"d": 1}]}


def test_shape_does_not_mutate_input() -> None:
    result = _load()
    before = copy.deepcopy(result)
    shape(result, "cinvcode", True)
    assert result == before
