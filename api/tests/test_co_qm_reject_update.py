"""来料 / 产品不良品处理单的修改（vouchers/update：表头日期与自定义项，处理行按 op 增删改）在 API 层的校验与转发。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.co_edit_qm import check_qm_update

_UPDATE = "/v1/co/vouchers/update"
_KINDS = ("qm_incoming_reject", "qm_product_reject")
_HEAD = {"dDate": "2026-10-02", "cDefine1": "DEMO-001", "chDefine15": "文本A"}
_ADD = {"op": "add", "quantity": 1, "cScrapDisCode": "Sys03", "cReasonCode": "01", "cDimInvCode": "A01"}
_SPLIT = [{"op": "update", "line_id": 11, "quantity": 1}, _ADD]


def _upd(kind: str = "qm_product_reject", head: dict | None = None, lines: list | None = None) -> dict:
    body: dict = {"type": kind, "id": 9}
    if head is not None:
        body["head"] = head
    if lines is not None:
        body["lines"] = lines
    return _auth(**body)


_ACCEPTS = (
    _upd("qm_incoming_reject", _HEAD),
    _upd("qm_product_reject", {"chdefine15": "文本A"}),
    _upd(lines=_SPLIT),
    _upd(head=_HEAD, lines=_SPLIT),
    _upd(lines=[{"op": "delete", "line_id": 12}, {"op": "update", "line_id": 11, "quantity": 2, "fDimQuantity": 2}]),
    _upd(lines=[{"op": "update", "line_id": 11, "cScrapDisCode": "Sys01", "cbWhCode": "01"}]),
)

_REJECTS = (
    _upd(),
    _upd(head={}),
    _upd(head={"dDate": ""}),
    _upd(head={"dDate": "2026/10/02"}),
    _upd(head={"cMemo": "x"}),
    _upd(head={"chDefine10": "x"}),
    _upd(head={"checkid": 5}),
    _upd(lines=[{"op": "update", "line_id": 11, "source_line_id": 5}]),
    _upd(lines=[{"op": "update", "line_id": 11, "cInvCode": "A01"}]),
    _upd(lines=[{"op": "update", "line_id": 11, "checkid": 5}]),
    _upd(lines=[{"op": "update", "line_id": 11}]),
    _upd(lines=[{"op": "update", "quantity": 1}]),
    _upd(lines=[{"op": "update", "line_id": 0, "quantity": 1}]),
    _upd(lines=[{"op": "update", "line_id": 11, "quantity": 0}]),
    _upd(lines=[{"op": "update", "line_id": 11, "fDimQuantity": -1}]),
    _upd(lines=[{"op": "update", "line_id": 11, "cScrapDisCode": ""}]),
    _upd(lines=[{"op": "delete", "line_id": 11, "quantity": 1}]),
    _upd(lines=[{"op": "add", "line_id": 11, "quantity": 1, "cScrapDisCode": "Sys01", "cReasonCode": "01"}]),
    _upd(lines=[{"op": "add", "quantity": 1, "cScrapDisCode": "Sys01"}]),
    _upd(lines=[{"op": "modify", "line_id": 11, "quantity": 1}]),
    _upd(lines=[{"op": "delete", "line_id": 11}, {"op": "update", "line_id": 11, "quantity": 1}]),
)


@pytest.mark.parametrize("body", _ACCEPTS)
def test_qm_reject_update_is_forwarded(body: dict) -> None:
    keys = (key for key in ("type", "id", "head", "lines") if key in body)
    _forwards(_UPDATE, "/v1/vouchers/update", body, _sealed(*keys))


@pytest.mark.parametrize("body", _REJECTS)
def test_qm_reject_update_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_UPDATE, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


@pytest.mark.parametrize("kind", _KINDS)
def test_check_qm_update_owns_reject_kinds(kind: str) -> None:
    assert check_qm_update(kind, _HEAD, None) is True
    with pytest.raises(ValueError, match="line_id"):
        check_qm_update(kind, None, [{"op": "delete", "line_id": 11}, {"op": "delete", "line_id": 11}])


def test_qm_reject_bridge_conflict_passes_through() -> None:
    from u8co_api.errors import ApiError

    fake = FakeBridge()
    fake.error = ApiError(409, "state_mismatch", "单据已审核，不能修改")
    done = _client(fake).post(_UPDATE, json=_upd(head=_HEAD))
    assert done.status_code == 409, done.text
    assert done.json()["error"]["code"] == "state_mismatch"


def test_openapi_lists_qm_reject_update() -> None:
    from tests.test_co_update_close_gen import _enum, _spec

    spec = _spec()
    for kind in _KINDS:
        assert kind in _enum(spec, "CoUpdateIn", "type")
    lines = spec["components"]["schemas"]["CoUpdateIn"]["properties"]["lines"]["description"]
    assert "fDimQuantity" in lines
