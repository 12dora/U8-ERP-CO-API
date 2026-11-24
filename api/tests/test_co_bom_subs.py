"""BOM (type=bom) substitutes / locators on component lines: forwarded as-is, shape checked before the bridge."""

from __future__ import annotations

import pytest
from tests.test_co_bom import _CREATE, _LINE, _UPDATE, _body, _BomBridge, _edit
from tests.test_co_core_routes import _auth
from tests.test_co_gate import FakeBridge, _client
from u8co_api.co_models_bom import check_bom_create, check_bom_update

_SUB = {
    "inv_code": "S001",
    "sequence": 1,
    "factor": 1.5,
    "replace_flag": False,
    "eff_begin": "2026-10-01",
    "eff_end": "2099-12-31",
}


def test_bom_create_forwards_substitutes_and_locators() -> None:
    fake = _BomBridge()
    lines = [dict(_LINE, substitutes=[_SUB, {"inv_code": "S002"}], locators=[{"loc": "R1"}, {"loc": "R2"}])]
    done = _client(fake).post(_CREATE, json=_body(lines=lines))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["lines"] == lines


def test_bom_update_forwards_replace_and_clear() -> None:
    fake = _BomBridge()
    lines = [
        {"op": "update", "sort_seq": 10, "substitutes": [{"inv_code": "S003", "factor": 2}]},
        {"op": "update", "sort_seq": 20, "substitutes": [], "locators": []},
        {"op": "add", "inv_code": "C003", "base_qty_n": 1, "substitutes": [_SUB]},
    ]
    done = _client(fake).post(_UPDATE, json=_edit(None, lines))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["lines"] == lines


def _sub_line(*subs: object) -> dict:
    return dict(_LINE, substitutes=list(subs))


@pytest.mark.parametrize(
    "body",
    [
        _body(lines=[dict(_LINE, substitutes="S001")]),
        _body(lines=[dict(_LINE, substitutes=["S001"])]),
        _body(lines=[_sub_line({"factor": 1})]),
        _body(lines=[_sub_line({"inv_code": " "})]),
        _body(lines=[_sub_line({"inv_code": "S001"}, {"inv_code": "s001"})]),
        _body(lines=[_sub_line({"inv_code": "S001", "factor": 0})]),
        _body(lines=[_sub_line({"inv_code": "S001", "factor": 0.0000001})]),
        _body(lines=[_sub_line({"inv_code": "S001", "sequence": 0})]),
        _body(lines=[_sub_line({"inv_code": "S001", "sequence": 10001})]),
        _body(lines=[_sub_line({"inv_code": "S001", "replace_flag": 1})]),
        _body(lines=[_sub_line({"inv_code": "S001", "eff_begin": "2026-9-1"})]),
        _body(lines=[_sub_line({"inv_code": "S001", "eff_begin": "1752-12-31"})]),
        _body(lines=[_sub_line({"inv_code": "S001", "qty": 1})]),
        _body(lines=[_sub_line(*({"inv_code": f"S{i:03d}"} for i in range(51)))]),
        _body(lines=[dict(_LINE, locators=[{"loc": "R1"}, {"loc": "R1"}])]),
        _body(lines=[dict(_LINE, locators=[{"loc": ""}])]),
        _body(lines=[dict(_LINE, locators=[{"loc": "R" * 256}])]),
        _body(lines=[dict(_LINE, locators=[{"loc": "R1", "sort_seq": 1}])]),
        _body(lines=[dict(_LINE, remark=["x"])]),
    ],
)
def test_bom_subs_reject_before_the_bridge(body: dict) -> None:
    fake = _BomBridge()
    denied = _client(fake).post(_CREATE, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


def test_bom_update_subs_reject_before_the_bridge() -> None:
    fake = _BomBridge()
    lines = [{"op": "update", "sort_seq": 10, "substitutes": [{"inv_code": "S001", "factor": -1}]}]
    denied = _client(fake).post(_UPDATE, json=_edit(None, lines))
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (_CREATE, _auth(type="other_in", head={"a": "b"}, lines=[{"a": 1, "substitutes": []}])),
        (_UPDATE, _auth(type="production_order", id=9, lines=[{"op": "update", "line_id": 1, "locators": []}])),
    ],
)
def test_only_bom_lines_take_arrays(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(path, json=body)
    assert denied.status_code in (400, 422), denied.text
    assert fake.calls == []


def test_bom_subs_messages() -> None:
    check_bom_create({"inv_code": "P001"}, [_sub_line(_SUB)])
    check_bom_update(None, [{"op": "update", "sort_seq": 10, "substitutes": [], "locators": []}])
    with pytest.raises(ValueError, match="替代料存货重复：s001"):
        check_bom_create({"inv_code": "P001"}, [_sub_line({"inv_code": "S001"}, {"inv_code": "s001"})])
    with pytest.raises(ValueError, match="factor 必须大于 0"):
        check_bom_create({"inv_code": "P001"}, [_sub_line({"inv_code": "S001", "factor": 0})])
    with pytest.raises(ValueError, match="定位符重复：R1"):
        check_bom_create({"inv_code": "P001"}, [dict(_LINE, locators=[{"loc": "R1"}, {"loc": "R1 "}])])
    with pytest.raises(ValueError, match="substitutes 必须是数组"):
        check_bom_create({"inv_code": "P001"}, [dict(_LINE, substitutes="S001")])


_LOADED_SUB = {
    "sequence": 1,
    "inv_code": "S001",
    "factor": 1.5,
    "replace_flag": False,
    "eff_begin": "2026-10-01",
    "eff_end": None,
    "defines": {"define22": "文本A", "define26": 2.5},
}


class _LoadBridge(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        body = super().call(path, payload)
        if path == "/v1/vouchers/load":
            first = {"line_id": "51", "sort_seq": "10", "inv_code": "C001", "client_only": "0"}
            first.update(substitutes=[_LOADED_SUB], locators=[{"sort_seq": 10, "loc": "DEMO-001"}])
            second = {"line_id": "52", "sort_seq": "20", "inv_code": "C002", "client_only": "0"}
            second.update(substitutes=[], locators=[])
            body["lines"] = [first, second]
        return body


def test_bom_load_returns_substitutes_and_locators() -> None:
    done = _client(_LoadBridge()).post("/v1/co/vouchers/load", json=_auth(type="bom", id=9))
    assert done.status_code == 200, done.text
    lines = done.json()["lines"]
    assert lines[0]["substitutes"] == [_LOADED_SUB]
    assert lines[0]["locators"] == [{"sort_seq": 10, "loc": "DEMO-001"}]
    assert (lines[1]["substitutes"], lines[1]["locators"]) == ([], [])
    assert lines[0]["client_only"] == "0"
