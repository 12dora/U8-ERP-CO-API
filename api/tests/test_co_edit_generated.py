"""生单来的单据和应收应付的修改在 API 层放行并原样转给桥；新增行在 API 层就拒绝。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tests.support import make_client
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.co_models_edit import CoUpdateIn

_UPDATE = "/v1/co/vouchers/update"
_MORE = (
    "dispatch",
    "sale_return",
    "sale_invoice",
    "sale_out",
    "product_in",
    "material_out",
    "arrival",
    "purchase_return",
    "purchase_invoice",
    "ar_receipt",
    "ap_payment",
    "ar_bill",
    "ap_bill",
)
_ARAP = ("ar_receipt", "ap_payment", "ar_bill", "ap_bill")
# 销售出库单来源库存的可新增行，API 层放行、由桥按来源判断。
# 发货单可以新增参照来源订单的行（test_co_dispatch_add）。
_NO_ADD = tuple(kind for kind in _MORE if kind not in _ARAP and kind not in ("sale_out", "dispatch"))
_EDIT = [{"op": "update", "line_id": 7, "iQuantity": 3, "cMemo": "改"}, {"op": "delete", "line_id": 8}]
_BANNED = {
    "dispatch": ("DLID", "cDLCode", "cVerifier", "iDLsID"),
    "sale_return": ("DLID", "cDLCode", "dverifydate"),
    "sale_invoice": ("SBVID", "cSBVCode", "cChecker"),
    "ar_receipt": ("iID", "cVouchID", "cCheckMan"),
    "ap_payment": ("iID", "cVouchID", "dverifydate"),
    "ar_bill": ("Auto_ID", "cVouchID", "cCheckMan"),
    "ap_bill": ("Auto_ID", "cVouchID", "dverifydate"),
}


def _update(kind: str, **extra) -> dict:
    body = {"type": kind, "id": 9, "head": {"cMemo": "改"}, "lines": _EDIT}
    body.update(extra)
    return _auth(**body)


@pytest.mark.parametrize("kind", _MORE)
def test_more_update_forwards_exact_keys(kind: str) -> None:
    _forwards(_UPDATE, "/v1/vouchers/update", _update(kind), _sealed("type", "id", "head", "lines"))


@pytest.mark.parametrize("kind", _MORE)
def test_more_update_reaches_the_bridge_unchanged(kind: str) -> None:
    fake = FakeBridge()
    done = _client(fake).post(_UPDATE, json=_update(kind))
    assert done.status_code == 200, done.text
    sent = fake.calls[-1][1]
    assert sent["type"] == kind
    assert sent["id"] == 9
    assert sent["head"] == {"cMemo": "改"}
    assert sent["lines"] == _EDIT


@pytest.mark.parametrize("kind", _MORE)
def test_more_update_head_only_is_enough(kind: str) -> None:
    fake = FakeBridge()
    done = _client(fake).post(_UPDATE, json=_auth(type=kind, id=9, head={"cDefine1": "x"}))
    assert done.status_code == 200, done.text
    assert "lines" not in fake.calls[-1][1]


@pytest.mark.parametrize("kind", _NO_ADD)
def test_generated_update_refuses_added_lines(kind: str) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_UPDATE, json=_update(kind, lines=[{"op": "add", "cInvCode": "A", "iQuantity": 1}]))
    assert denied.status_code == 400, denied.text
    assert denied.json()["error"]["code"] == "bad_request"
    assert fake.calls == []
    with pytest.raises(ValidationError, match="不能新增行"):
        CoUpdateIn.model_validate(_update(kind, lines=[{"op": "add", "cInvCode": "A"}]))


def test_manual_and_existing_update_types_still_accept_added_lines() -> None:
    fake = FakeBridge()
    add = [{"op": "add", "cInvCode": "A", "iQuantity": 1}]
    for kind in ("sale_order", "purchase_order", "other_in", "purchase_in") + _ARAP:
        done = _client(fake).post(_UPDATE, json=_update(kind, lines=add))
        assert done.status_code == 200, (kind, done.text)


def test_sale_out_added_and_deleted_lines_reach_the_bridge() -> None:
    # 来源库存的销售出库单可加行、删行；来源发货单的由桥 400，API 不看来源。
    fake = FakeBridge()
    lines = [{"op": "add", "cInvCode": "A", "iQuantity": 1, "cbMemo": "加"}, {"op": "delete", "line_id": 8}]
    done = _client(fake).post(_UPDATE, json=_update("sale_out", lines=lines))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["lines"] == lines
    with pytest.raises(ValidationError, match="新增行不能带 line_id"):
        CoUpdateIn.model_validate(_update("sale_out", lines=[{"op": "add", "line_id": 3, "cInvCode": "A"}]))


def test_openapi_says_stock_sale_out_can_add_lines() -> None:
    spec = make_client().get("/v1/openapi.json").json()
    prop = spec["components"]["schemas"]["CoUpdateIn"]["properties"]
    assert "来源为库存的销售出库单除外" in prop["type"]["description"]
    assert "来源为库存的销售出库单除外" in prop["lines"]["description"]


_REJECTS = tuple(_update(kind, head={name: "x"}) for kind, names in _BANNED.items() for name in names) + (
    _update("dispatch", lines=[{"op": "update", "line_id": 7, "iDLsID": 1}]),
    _update("sale_invoice", lines=[{"op": "delete", "line_id": 7, "iQuantity": 1}]),
    _update("sale_return", lines=[{"op": "update", "line_id": 7}]),
    _update("arrival", lines=[{"op": "update", "line_id": 7, "iQuantity": 1}, {"op": "delete", "line_id": 7}]),
    _update("ar_bill", lines=[{"op": "move", "line_id": 7}]),
    _auth(type="dispatch", id=9),
    _auth(type="sale_invoice", id=0, head={"cMemo": "x"}),
)


@pytest.mark.parametrize("body", _REJECTS)
def test_more_update_rejects_before_the_bridge(body: dict) -> None:
    fake = FakeBridge()
    denied = _client(fake).post(_UPDATE, json=body)
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_close_is_still_refused_for_the_new_update_types() -> None:
    fake = FakeBridge()
    # 到货单可关闭（test_co_arrival_close）。
    for kind in ("dispatch", "sale_return", "sale_invoice", "ar_receipt"):
        denied = _client(fake).post("/v1/co/vouchers/close", json=_auth(type=kind, id=9, action="close"))
        assert denied.status_code == 400, (kind, denied.text)
    assert fake.calls == []


def test_openapi_lists_the_new_update_types() -> None:
    spec = make_client().get("/v1/openapi.json").json()
    prop = spec["components"]["schemas"]["CoUpdateIn"]["properties"]["type"]
    for kind in _MORE:
        assert kind in prop["enum"]
    assert "发货单" in prop["description"]
    assert "不能新增行" in prop["description"]
