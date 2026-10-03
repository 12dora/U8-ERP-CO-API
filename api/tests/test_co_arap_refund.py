"""供应商退款（ap_refund，AP48）、客户退款（ar_refund，AR49）：同收付款单，读取、新增、修改、删除、审核在 API 层放行
并原样转给桥；不进核销、制单。"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import ValidationError
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.co_models import CreateType, DeleteType, VerifyType, VoucherType
from u8co_api.co_models_arap_voucher import VoucherDocType
from u8co_api.co_models_edit import CoUpdateIn, UpdateType
from u8co_api.co_models_writeoff import ReceiptType

_KINDS = ("ap_refund", "ar_refund")
_HEAD = {"ap_refund": {"cDwCode": "S0001"}, "ar_refund": {"cDwCode": "C0001"}}
_LINES = [{"cKm": "2202", "iAmt": 100}]


def _cases() -> list[tuple[str, str, dict, set[str]]]:
    cases = []
    for kind in _KINDS:
        head = dict(_HEAD[kind], cSSCode="1", cCode="1002")
        cases += [
            ("/v1/co/vouchers/create", "/v1/vouchers/create", _auth(type=kind, head=head, lines=_LINES),
             _sealed("type", "head", "lines")),
            ("/v1/co/vouchers/delete", "/v1/vouchers/delete", _auth(type=kind, id=9), _sealed("type", "id")),
            ("/v1/co/vouchers/load", "/v1/vouchers/load", _auth(type=kind, id=9), _sealed("type", "id")),
            ("/v1/co/vouchers/verify", "/v1/vouchers/verify", _auth(type=kind, id=9, action="unverify"),
             _sealed("type", "id", "action")),
        ]
    return cases


@pytest.mark.parametrize("kind", _KINDS)
def test_type_lists(kind: str) -> None:
    for kinds in (VoucherType, VerifyType, CreateType, DeleteType, UpdateType):
        assert kind in get_args(kinds)
    # 核销只认收款单 / 付款单（退款方向的对冲在 U8 客户端做）；退款单可以制单。
    assert kind not in get_args(ReceiptType)
    assert kind in get_args(VoucherDocType)


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _cases())
def test_forwards_exact_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    _forwards(path, bridge, body, keys)


@pytest.mark.parametrize("kind", _KINDS)
def test_update_reaches_the_bridge(kind: str) -> None:
    fake = FakeBridge()
    lines = [{"op": "update", "line_id": 7, "iAmt": 80}, {"op": "add", "cKm": "2202", "iAmt": 20}]
    done = _client(fake).post("/v1/co/vouchers/update", json=_auth(type=kind, id=9, head={"cDigest": "退款"}, lines=lines))
    assert done.status_code == 200, done.text
    assert fake.calls[-1][1]["type"] == kind


@pytest.mark.parametrize("kind", _KINDS)
@pytest.mark.parametrize("field", ["iID", "cVouchID", "cCheckMan", "dverifydate"])
def test_update_bans_keys(kind: str, field: str) -> None:
    with pytest.raises(ValidationError):
        CoUpdateIn.model_validate(_auth(type=kind, id=9, head={field: "x"}, lines=None))


@pytest.mark.parametrize("kind", _KINDS)
def test_writeoff_refuses_refund_receipt(kind: str) -> None:
    fake = FakeBridge()
    body = _auth(receipt={"type": kind, "id": 5, "line_id": 7}, items=[{"type": "ap_bill", "id": 2, "amount": 1}])
    assert _client(fake).post("/v1/co/arap/writeoff", json=body).status_code == 400
    assert fake.calls == []
