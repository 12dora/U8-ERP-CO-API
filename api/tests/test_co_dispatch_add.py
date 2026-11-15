"""发货单修改新增行（op=add）在 API 层的校验，以及原样转给桥。不连接服务器。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from u8co_api.co_edit_dispatch_add import check_dispatch_adds
from u8co_api.co_models_edit import CoUpdateIn

_UPDATE = "/v1/co/vouchers/update"
_ADD = {"op": "add", "source_line_id": 12, "iQuantity": 1, "cWhCode": "01"}


def _body(*lines: dict) -> dict:
    return _auth(type="dispatch", id=9, lines=list(lines))


def _with(**extra: object) -> dict:
    row = dict(_ADD)
    row.update(extra)
    return row


def _without(name: str) -> dict:
    return {key: value for key, value in _ADD.items() if key != name}


def test_add_line_reaches_the_bridge_unchanged() -> None:
    fake = FakeBridge()
    extra = _with(cBatch="L1", cMemo="补发", cFree1="红", cDefine22="x", iQuantity=2.5)
    lines = [extra, {"op": "update", "line_id": 7, "iQuantity": 1}, {"op": "delete", "line_id": 8}]
    done = _client(fake).post(_UPDATE, json=_body(*lines))
    assert done.status_code == 200, done.text
    sent = fake.calls[-1][1]
    assert sent["type"] == "dispatch"
    assert sent["lines"] == lines


def test_two_adds_from_different_order_lines_pass() -> None:
    CoUpdateIn.model_validate(_body(_ADD, _with(source_line_id=13)))


_BAD = (
    (_without("source_line_id"), "source_line_id"),
    (_with(source_line_id=0), "source_line_id"),
    (_with(source_line_id="12"), "source_line_id"),
    (_with(source_line_id=True), "source_line_id"),
    (_without("iQuantity"), "iQuantity"),
    (_with(iQuantity=0), "iQuantity"),
    (_with(iQuantity=-1), "iQuantity"),
    (_with(iQuantity="1"), "iQuantity"),
    (_without("cWhCode"), "cWhCode"),
    (_with(cWhCode=" "), "cWhCode"),
    (_with(cWhCode=1), "cWhCode"),
    (_with(cInvCode="A"), "不能设置字段 cInvCode"),
    (_with(cFree11="x"), "不能设置字段 cFree11"),
    (_with(cDefine21="x"), "不能设置字段 cDefine21"),
    (_with(quantity=1), "不能设置字段 quantity"),
    (_with(iSOsID=12), "不能填写字段 iSOsID"),
    (_with(line_id=3), "新增行不能带 line_id"),
)


@pytest.mark.parametrize(("row", "needle"), _BAD)
def test_bad_add_rows_are_refused_before_the_bridge(row: dict, needle: str) -> None:
    with pytest.raises(ValidationError, match=needle):
        CoUpdateIn.model_validate(_body(row))
    fake = FakeBridge()
    denied = _client(fake).post(_UPDATE, json=_body(row))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []


def test_infinite_quantity_is_refused() -> None:
    with pytest.raises(ValidationError, match="iQuantity"):
        CoUpdateIn.model_validate(_body(_with(iQuantity=float("inf"))))


def test_same_order_line_twice_is_refused() -> None:
    with pytest.raises(ValidationError, match="source_line_id 重复"):
        CoUpdateIn.model_validate(_body(_ADD, _with(iQuantity=2)))


def test_key_case_does_not_matter() -> None:
    CoUpdateIn.model_validate(_body({"op": "add", "SOURCE_LINE_ID": 12, "iquantity": 1, "CWHCODE": "01"}))


@pytest.mark.parametrize("kind", ("sale_return", "sale_invoice", "arrival"))
def test_other_generated_kinds_still_refuse_add(kind: str) -> None:
    with pytest.raises(ValidationError, match="不能新增行"):
        CoUpdateIn.model_validate(_auth(type=kind, id=9, lines=[_ADD]))


def test_check_ignores_other_kinds_and_non_add_rows() -> None:
    check_dispatch_adds("sale_order", [{"op": "add", "cInvCode": "A"}])
    check_dispatch_adds("dispatch", None)
    check_dispatch_adds("dispatch", [{"op": "update", "line_id": 7, "iQuantity": 1}])


def test_openapi_describes_dispatch_add() -> None:
    spec = make_client().get("/v1/openapi.json").json()
    props = spec["components"]["schemas"]["CoUpdateIn"]["properties"]
    assert "发货单可以 add" in props["lines"]["description"]
    assert "source_line_id" in props["lines"]["description"]
    assert "除外" in props["type"]["description"]
