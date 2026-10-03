"""无来源销售出库单（sale_out，来源库存）：新增在 API 层放行并原样转给桥；表头字段名单、必填和账套选项由桥查。"""

from __future__ import annotations

from typing import get_args

from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.co_models import CoCreateIn, CreateType, DeleteType
from u8co_api.co_srcless import SRCLESS_CREATE

_HEAD = {"cWhCode": "05", "cCusCode": "C001", "cDepCode": "D01", "cRdCode": "101", "cSTCode": "XS"}
_LINES = [{"cInvCode": "A001", "iQuantity": 2, "cBMemo": "无来源出库"}]


def test_type_lists() -> None:
    assert "sale_out" in get_args(CreateType)
    assert "sale_out" in get_args(DeleteType)
    # 走桥的库存 StockDom 路径，API 不套无来源名单。
    assert "sale_out" not in SRCLESS_CREATE


def test_model_accepts_create() -> None:
    body = CoCreateIn.model_validate(_auth(type="sale_out", head=_HEAD, lines=_LINES))
    assert (body.type, body.head, body.lines) == ("sale_out", _HEAD, _LINES)


def test_forwards_exact_keys() -> None:
    body = _auth(type="sale_out", head=_HEAD, lines=_LINES)
    _forwards("/v1/co/vouchers/create", "/v1/vouchers/create", body, _sealed("type", "head", "lines"))
