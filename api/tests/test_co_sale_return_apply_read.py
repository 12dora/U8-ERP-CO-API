"""退货申请单（sale_return_apply）：可读取、列表、单据追溯；可新增、修改、删除、审核，不能关闭、不是生单目标。"""

from __future__ import annotations

from typing import get_args

from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from u8co_api.co_models import CreateType, DeleteType, VerifyType, VoucherType
from u8co_api.co_models_edit import CloseType, GenerateType, UpdateType
from u8co_api.co_models_reports_trace import TRACE_KINDS
from u8co_api.co_models_wf import WfType

_KIND = "sale_return_apply"


def test_type_lists() -> None:
    assert _KIND in get_args(VoucherType)
    assert _KIND in TRACE_KINDS
    for writable in (VerifyType, CreateType, DeleteType, UpdateType):
        assert _KIND in get_args(writable)
    for refused in (CloseType, GenerateType, WfType):
        assert _KIND not in get_args(refused)


def test_list_and_load_reach_the_bridge() -> None:
    fake = FakeBridge()
    client = _client(fake)
    listed = client.post("/v1/co/vouchers/list", json=_auth(type=_KIND, keys_only=True))
    assert listed.status_code == 200, listed.text
    loaded = client.post("/v1/co/vouchers/load", json=_auth(type=_KIND, id=1))
    assert loaded.status_code == 200, loaded.text
    assert [call[0] for call in fake.calls] == ["/v1/vouchers/list", "/v1/vouchers/load"]


def test_close_and_workflow_are_refused_before_the_bridge() -> None:
    fake = FakeBridge()
    client = _client(fake)
    close = client.post("/v1/co/vouchers/close", json=_auth(type=_KIND, id=1, action="close"))
    assert close.status_code == 400, close.text
    submit = client.post("/v1/co/workflow/submit", json=_auth(type=_KIND, id=1))
    assert submit.status_code == 400, submit.text
    assert fake.calls == []
