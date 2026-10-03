"""出入库调整单（ia_adjust）、存货调价单（inventory_price_adjust）：可读取、列表、查找，写路由的类型表里没有。"""

from __future__ import annotations

from typing import get_args

import pytest
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from u8co_api.co_models import CreateType, DeleteType, VerifyType, VoucherType
from u8co_api.co_models_wf import WfType

_KINDS = ("ia_adjust", "inventory_price_adjust")


@pytest.mark.parametrize("kind", _KINDS)
def test_readable_but_not_writable(kind: str) -> None:
    assert kind in get_args(VoucherType)
    for writable in (VerifyType, CreateType, DeleteType, WfType):
        assert kind not in get_args(writable)


@pytest.mark.parametrize("kind", _KINDS)
def test_list_and_load_reach_the_bridge(kind: str) -> None:
    fake = FakeBridge()
    client = _client(fake)
    listed = client.post("/v1/co/vouchers/list", json=_auth(type=kind, keys_only=True))
    assert listed.status_code == 200, listed.text
    loaded = client.post("/v1/co/vouchers/load", json=_auth(type=kind, id=1))
    assert loaded.status_code == 200, loaded.text
    assert [call[0] for call in fake.calls] == ["/v1/vouchers/list", "/v1/vouchers/load"]


@pytest.mark.parametrize("kind", _KINDS)
def test_verify_is_refused_before_the_bridge(kind: str) -> None:
    fake = FakeBridge()
    denied = _client(fake).post("/v1/co/vouchers/verify", json=_auth(type=kind, id=1, action="verify"))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []
