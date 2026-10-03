"""货位调整单（position_adjust）：读取、新增、删除、审核在 API 层放行并原样转给桥，审核、读取的附加字段不丢；
数量按同族库存单据的规则在 API 层先查；不开修改、关闭、生单。"""

from __future__ import annotations

from typing import get_args

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth, _sealed
from tests.test_co_gl_arc import test_gl_arc_forwards_exact_sealed_keys as _forwards
from u8co_api.co_models import CreateType, DeleteType, VerifyType, VoucherType

_KIND = "position_adjust"
_CREATE = "/v1/co/vouchers/create"
_HEAD = {"cWhCode": "05", "dDate": "2026-10-03", "cMemo": "调货位"}
_LINES = [{"cInvCode": "A001", "cBPosCode": "5A010101", "cAPosCode": "5A010102", "iQuantity": 2}]
_MOVES = [
    {"wh": "05", "pos": "5A010101", "inv": "A001", "batch": "", "before": "5", "after": "3"},
    {"wh": "05", "pos": "5A010102", "inv": "A001", "batch": "", "before": "0", "after": "2"},
]
_LEDGER = [{"id": "1", "line_id": "11", "inbound": "0", "pos": "5A010101", "inv": "A001", "qty": "2"}]


class _Bridge(FakeBridge):
    """审核、读取另回货位调整单的附加字段。"""

    def call(self, path: str, payload: dict) -> dict:
        body = super().call(path, payload)
        if path == "/v1/vouchers/verify":
            return {**body, "ledger_rows": 2, "bin_moves": _MOVES}
        if path == "/v1/vouchers/load":
            return {**body, "positions": _LEDGER}
        return body


def _cases() -> list[tuple[str, str, dict, set[str]]]:
    create = _auth(type=_KIND, head=_HEAD, lines=_LINES)
    return [
        (_CREATE, "/v1/vouchers/create", create, _sealed("type", "head", "lines")),
        ("/v1/co/vouchers/delete", "/v1/vouchers/delete", _auth(type=_KIND, id=9), _sealed("type", "id")),
        ("/v1/co/vouchers/load", "/v1/vouchers/load", _auth(type=_KIND, id=9), _sealed("type", "id")),
        ("/v1/co/vouchers/verify", "/v1/vouchers/verify", _auth(type=_KIND, id=9, action="unverify"),
         _sealed("type", "id", "action")),
    ]


def test_type_lists() -> None:
    for kinds in (VoucherType, VerifyType, CreateType, DeleteType):
        assert _KIND in get_args(kinds)


@pytest.mark.parametrize(("path", "bridge", "body", "keys"), _cases())
def test_forwards_exact_keys(path: str, bridge: str, body: dict, keys: set[str]) -> None:
    _forwards(path, bridge, body, keys)


def test_verify_keeps_ledger_rows_and_bin_moves() -> None:
    done = _client(_Bridge()).post("/v1/co/vouchers/verify", json=_auth(type=_KIND, id=9, action="verify"))
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["ledger_rows"], body["bin_moves"]) == (2, _MOVES)


def test_load_keeps_positions() -> None:
    done = _client(_Bridge()).post("/v1/co/vouchers/load", json=_auth(type=_KIND, id=9))
    assert done.status_code == 200, done.text
    assert done.json()["positions"] == _LEDGER


@pytest.mark.parametrize("qty", [0, -1, 1.1234567, True, "abc", 1e13])
def test_quantity_checked_before_the_bridge(qty: object) -> None:
    fake = FakeBridge()
    lines = [dict(_LINES[0], iQuantity=qty)]
    denied = _client(fake).post(_CREATE, json=_auth(type=_KIND, head=_HEAD, lines=lines))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []


def test_update_close_generate_refused() -> None:
    fake = FakeBridge()
    client = _client(fake)
    update = _auth(type=_KIND, id=9, head={"cMemo": "改"}, lines=None)
    assert client.post("/v1/co/vouchers/update", json=update).status_code == 400
    assert client.post("/v1/co/vouchers/close", json=_auth(type=_KIND, id=9, action="close")).status_code == 400
    generate = _auth(type=_KIND, id=3, lines=[{"source_line_id": 1, "quantity": 1}])
    assert client.post("/v1/co/vouchers/generate", json=generate).status_code == 400
    assert fake.calls == []
