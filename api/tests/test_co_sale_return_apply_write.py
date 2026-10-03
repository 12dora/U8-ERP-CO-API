"""退货申请单（sale_return_apply）写入：新增、修改的 API 层名单与桥 ReturnsApplyReq 一致；退货单可参照退货申请单生单。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from u8co_api.co_gen_source import SOURCES
from u8co_api.co_models import CoCreateIn
from u8co_api.co_models_edit import CoGenerateIn, CoUpdateIn

_KIND = "sale_return_apply"
_LINE = {"source_line_id": 7, "quantity": 2, "cReasonCode": "01", "cMemo": "质量问题", "cDefine22": "x"}


def test_create_accepts_dispatch_lines() -> None:
    body = CoCreateIn.model_validate(_auth(type=_KIND, head={"dDate": "2026-10-01", "cMemo": "退货"}, lines=[_LINE]))
    assert (body.type, body.lines) == (_KIND, [_LINE])


@pytest.mark.parametrize(
    ("head", "line"),
    [
        ({}, {"quantity": 2}),
        ({}, {"source_line_id": 7, "quantity": "2"}),
        ({}, {"source_line_id": 7, "quantity": 0}),
        ({}, {"source_line_id": True, "quantity": 1}),
        ({}, {"source_line_id": 7, "quantity": 1, "cInvCode": "A001"}),
        ({"cCusCode": "C001"}, {"source_line_id": 7, "quantity": 1}),
        ({"dDate": "2026/10/01"}, {"source_line_id": 7, "quantity": 1}),
    ],
)
def test_create_refuses(head: dict, line: dict) -> None:
    with pytest.raises(ValidationError):
        CoCreateIn.model_validate(_auth(type=_KIND, head=head, lines=[line]))


def test_create_refuses_duplicate_source() -> None:
    with pytest.raises(ValidationError):
        CoCreateIn.model_validate(_auth(type=_KIND, head={}, lines=[_LINE, dict(_LINE)]))


def test_update_only_edits_existing_lines() -> None:
    ok = CoUpdateIn.model_validate(
        _auth(type=_KIND, id=3, head={"cMemo": "改"}, lines=[{"op": "update", "line_id": 5, "iQuantity": 1.5}])
    )
    assert ok.lines == [{"op": "update", "line_id": 5, "iQuantity": 1.5}]
    for row in (
        {"op": "add", "source_line_id": 7, "quantity": 1},
        {"op": "delete", "line_id": 5},
        {"op": "update", "line_id": 5},
        {"op": "update", "line_id": 5, "cInvCode": "A001"},
        {"op": "update", "line_id": 5, "iQuantity": -1},
    ):
        with pytest.raises(ValidationError):
            CoUpdateIn.model_validate(_auth(type=_KIND, id=3, lines=[row]))
    with pytest.raises(ValidationError):
        CoUpdateIn.model_validate(_auth(type=_KIND, id=3, head={"cCusCode": "C001"}))


def test_sale_return_generates_from_apply() -> None:
    assert SOURCES["sale_return"] == ("dispatch", _KIND)
    body = CoGenerateIn.model_validate(
        _auth(type="sale_return", id=3, source_type=_KIND, lines=[{"source_line_id": 5, "quantity": 1}])
    )
    assert body.source_type == _KIND


def test_writes_reach_the_bridge() -> None:
    fake = FakeBridge()
    client = _client(fake)
    created = client.post("/v1/co/vouchers/create", json=_auth(type=_KIND, head={}, lines=[_LINE]))
    assert created.status_code == 200, created.text
    verified = client.post("/v1/co/vouchers/verify", json=_auth(type=_KIND, id=3, action="verify"))
    assert verified.status_code == 200, verified.text
    deleted = client.post("/v1/co/vouchers/delete", json=_auth(type=_KIND, id=3))
    assert deleted.status_code == 200, deleted.text
    assert [call[0] for call in fake.calls] == ["/v1/vouchers/create", "/v1/vouchers/verify", "/v1/vouchers/delete"]
