"""来料 / 产品检验单、其他检验单、其他报检单的修改（vouchers/update，只收 head）在 API 层的校验与转发。"""

from __future__ import annotations

import pytest
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.co_edit_qm import check_qm_update

_UPDATE = "/v1/co/vouchers/update"
_CHECKS = ("qm_incoming_check", "qm_product_check", "qm_other_check")
_ITEMS = [{"cChkItemCode": "I01", "cChkGuideCode": "G01", "cCheckValue": "10.5", "cTargetQJug": "不合格"}]
_CHECK_HEAD = {
    "fRegQuantity": 9,
    "fConQuantiy": 0,
    "fDisQuantity": 1,
    "cChkConclusion": "不合格",
    "cReasonCode": "03",
    "cCheckPersonCode": "P01",
    "dDate": "2026-09-30",
    "cDefine10": "B-1",
    "chDefine11": "001",
    "items": _ITEMS,
}
_INSPECT_HEAD = {"dDate": "2026-09-30", "cInspectDepCode": "D01", "cDefine10": "B-1", "chdefine16": "x"}


def _upd(kind: str, head: dict | None, **extra) -> dict:
    body: dict = {"type": kind, "id": 9}
    if head is not None:
        body["head"] = head
    body.update(extra)
    return _auth(**body)


_ACCEPTS = tuple(_upd(kind, _CHECK_HEAD) for kind in _CHECKS) + (
    _upd("qm_incoming_check", {"cchkconclusion": "合格"}),
    _upd("qm_product_check", {"fDisQuantity": 2, "creasoncode": "03"}),
    _upd("qm_product_check", {"fConQuantiy": 1, "cReasonCode": "03", "cYielderCode": "P01", "dYieldDate": "2026-09-30"}),
    _upd("qm_other_check", {"items": _ITEMS}),
    _upd("qm_other_check", {"cDefine10": "B-2"}),
    _upd("qm_other_inspect", _INSPECT_HEAD),
    _upd("qm_other_inspect", {"cdefine10": 3}),
)

_REJECTS = (
    _upd("qm_incoming_check", None),
    _upd("qm_incoming_check", {}),
    _upd("qm_incoming_check", {"cChkConclusion": "合格"}, lines=[{"op": "update", "line_id": 7, "cMemo": "x"}]),
    _upd("qm_incoming_check", {"fQuantity": 5}),
    _upd("qm_incoming_check", {"cInvCode": "A"}),
    _upd("qm_incoming_check", {"cWhCode": "01"}),
    _upd("qm_incoming_check", {"project_code": "P1"}),
    _upd("qm_incoming_check", {"fDtQuantity": 1}),
    _upd("qm_incoming_check", {"cCheckPersonCode": ""}),
    _upd("qm_incoming_check", {"cReasonCode": 3}),
    _upd("qm_incoming_check", {"fDisQuantity": -1}),
    _upd("qm_incoming_check", {"fDisQuantity": "1"}),
    _upd("qm_incoming_check", {"dDate": "2026/09/30"}),
    _upd("qm_incoming_check", {"dYieldDate": "2026/09/30"}),
    _upd("qm_incoming_check", {"cYielderCode": ""}),
    _upd("qm_other_inspect", {"cYielderCode": "P01"}),
    _upd("qm_incoming_check", {"cDefine17": "x"}),
    _upd("qm_incoming_check", {"chDefine10": "x"}),
    _upd("qm_incoming_check", {"items": []}),
    _upd("qm_incoming_check", {"items": [{"cChkItemCode": "I"}]}),
    _upd("qm_product_check", {"cChkConclusion": "合格", "cchkconclusion": "不合格"}),
    _upd("qm_other_inspect", {"items": _ITEMS}),
    _upd("qm_other_inspect", {"cReasonCode": "03"}),
    _upd("qm_other_inspect", {"cInspectDepCode": ""}),
    _upd("qm_other_inspect", {"cDefine10": "x"}, lines=[{"op": "update", "line_id": 7, "quantity": 2}]),
    # 报检单、不良品处理单仍不能修改
    _upd("qm_incoming_inspect", {"dDate": "2026-09-30"}),
    # 产品报检单也不能修改（U8 的修改不可用），只开放弃审
    _upd("qm_product_inspect", {"dDate": "2026-09-30"}),
    _upd("qm_product_inspect", None, lines=[{"op": "update", "line_id": 7, "quantity": 2}]),
    _upd("qm_product_reject", {"dDate": "2026-09-30"}),
    # 别的类型的表头仍不能嵌套
    _upd("other_in", {"items": _ITEMS}),
)


@pytest.mark.parametrize("body", _ACCEPTS)
def test_qm_update_is_forwarded(body: dict) -> None:
    _forwards(_UPDATE, "/v1/vouchers/update", body, _sealed(*(key for key in ("type", "id", "head", "lines") if key in body)))


@pytest.mark.parametrize("body", _REJECTS)
def test_qm_update_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_UPDATE, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_openapi_lists_qm_update_types() -> None:
    spec = make_client().get("/v1/openapi.json").json()
    props = spec["components"]["schemas"]["CoUpdateIn"]["properties"]
    for kind in _CHECKS + ("qm_other_inspect",):
        assert kind in props["type"]["enum"]
    assert "其他报检单" in props["type"]["description"]
    assert "cReasonCode" in str(props["head"])


def test_bad_yield_date_names_its_own_field() -> None:
    with pytest.raises(ValueError, match="dYieldDate"):
        check_qm_update("qm_incoming_check", {"dYieldDate": "2026/09/30"}, None)


_VERIFY = "/v1/co/vouchers/verify"


def test_product_inspect_unverify_is_forwarded() -> None:
    body = _auth(type="qm_product_inspect", id=9, action="unverify")
    _forwards(_VERIFY, "/v1/vouchers/verify", body, _sealed("type", "id", "action"))


@pytest.mark.parametrize("kind", ["qm_product_inspect", "qm_incoming_inspect"])
def test_inspect_verify_is_refused_before_the_bridge(kind: str) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_VERIFY, json=_auth(type=kind, id=9, action="verify"))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []
