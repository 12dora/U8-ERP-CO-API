"""Two-level /v1/co access: u8co_write may call everything, u8co_read only the read routes."""

from __future__ import annotations

import pytest
from tests.support import base_claims, entry
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from tests.test_co_update_close_gen import _spec
from tests.test_co_gl_arc import _HEAD, _KEY, _LINES
from u8co_api.auth import caller_from
from u8co_api.co_access import ACCESS, MGMT, MGMT_ACTIONS, PERM_EVALUATE, READ, WRITE
from u8co_api.co_routes_arap_proc import ARAP_PROC_ROUTES  # 应收应付处理
from u8co_api.co_routes_arap_proc_list import ARAP_PROC_LIST_ROUTES  # 应收应付处理记录（事件源）
from u8co_api.co_routes_arap_voucher import ARAP_VOUCHER_ROUTES  # R8 应收 / 应付制单
from u8co_api.co_routes_attach import ATTACH_ROUTES  # 凭证、单据附件列表
from u8co_api.co_routes_gl_transfer import GL_TRANSFER_ROUTES  # 期间损益结转、自定义转账
from u8co_api.co_routes_notes_proc import NOTES_PROC_ROUTES  # 票据处理
from u8co_api.co_routes_notes_read import NOTES_READ_ROUTES  # 票据读取
from u8co_api.co_routes_notes_reg import NOTES_REG_ROUTES  # 应收票据登记、删除
from u8co_api.co_routes_ai import AI_ROUTES  # 档案名称解析、幂等结果查询
from u8co_api.co_routes_openings import OPENING_ROUTES  # 采购期初记账
from u8co_api.co_routes_gl_arc import GL_ARC_ROUTES
from u8co_api.co_routes_perm import PERM_ROUTES  # 有效权限
from u8co_api.co_routes_periods import IA_ROUTES, PERIOD_ROUTES  # 月末结账；存货核算
from u8co_api.co_routes_lookup import LOOKUP_ROUTES  # 字段标签、单据查询、批量读取
from u8co_api.co_routes_reports import REPORT_ROUTES  # 只读报表
from u8co_api.co_table import ROUTES
from u8co_api.co_ic_core import FANOUT_ACTIONS  # 公司间路由（多账套，单独测试）
from u8co_api.co_routes_ic import IC_WRITE_ROUTES  # 按卖方单据生成买方单据

_QM = {"type": "qm_product_check", "id": 1}
_ARC = {"archive": "customer", "code": "C900001"}

_READS = (
    ("/v1/co/login-check", _auth()),
    ("/v1/co/vouchers/load", _auth(type="sale_order", id=1)),
    ("/v1/co/vouchers/list", _auth(type="ar_receipt")),
    ("/v1/co/stock/current", _auth()),
    ("/v1/co/workflow/state", _auth(**_QM)),
    ("/v1/co/workflow/history", _auth(**_QM)),
    ("/v1/co/workflow/tasks", _auth()),
    ("/v1/co/gl/vouchers/load", _auth(**_KEY)),
    ("/v1/co/gl/vouchers/list", _auth(period_from=1, period_to=12)),
    ("/v1/co/gl/vouchers/digest", _auth()),  # 凭证摘要（事件源）
    ("/v1/co/archives/get", _auth(**_ARC)),
    ("/v1/co/archives/list", _auth(archive="customer")),
    # 只读报表。
    ("/v1/co/reports/close_status", _auth()),
    ("/v1/co/reports/gl_balance", _auth(period_from=1, period_to=8)),
    ("/v1/co/reports/gl_aux_balance", _auth(dim="customer", period_from=8, period_to=8)),
    ("/v1/co/reports/arap_balance", _auth(side="ar")),
    ("/v1/co/reports/arap_aging", _auth(side="ap")),
    ("/v1/co/reports/bom", _auth(parent="INV0012")),
    ("/v1/co/reports/arap_detail", _auth(side="ar", partner="C001", date_from="2026-08-01")),
    ("/v1/co/reports/gl_detail", _auth(code="1122", period_from=8, period_to=8)),
    ("/v1/co/reports/order_execution", _auth(type="purchase_order")),
    ("/v1/co/reports/doc_trace", _auth(type="dispatch", id=5)),
    # 库存与销售支持报表。
    ("/v1/co/reports/stock_ledger", _auth(inv="INV0001", date_from="2026-09-01")),
    ("/v1/co/reports/stock_summary", _auth(date_from="2026-09-01")),
    ("/v1/co/reports/position_stock", _auth()),
    ("/v1/co/reports/batch_stock", _auth()),
    ("/v1/co/reports/customer_credit", _auth()),
    ("/v1/co/reports/price_list", _auth(kind="customer")),
    # 期初余额、凭证附件与单据附件列表。
    ("/v1/co/reports/opening_balance", _auth(module="stock")),
    ("/v1/co/reports/arap_writeoffs", _auth(flag="AR")),  # R8 核销记录
    ("/v1/co/arap/process/list", _auth(flag="AR")),  # 应收应付处理记录
    ("/v1/co/reports/account_readiness", _auth()),  # 账套体检
    ("/v1/co/reports/fa_changes", _auth()),  # 固定资产变动单
    ("/v1/co/reports/fa_depreciation", _auth(period=8)),  # 固定资产折旧
    ("/v1/co/gl/vouchers/attachments/list", _auth(**_KEY)),
    ("/v1/co/vouchers/attachments/list", _auth(type="sale_order", id=1)),
    # 档案名称解析、幂等结果查询。
    ("/v1/co/archives/resolve", _auth(items=[{"archive": "customer", "q": "客户甲"}])),
    ("/v1/co/idempotency/get", _auth(path="/v1/co/vouchers/create", key="order-1")),
    # 字段标签、单据查询、批量读取单据和档案。
    ("/v1/co/meta/fields", _auth(type="sale_order")),
    ("/v1/co/vouchers/search", _auth(type="sale_order", code_like="SO")),
    ("/v1/co/vouchers/load_many", _auth(type="sale_order", ids=[1, 2])),
    ("/v1/co/archives/get_many", _auth(archive="customer", codes=["C900001"])),
    ("/v1/co/notes/get", _auth(type="ar_note", id=1)),  # 票据读取
    ("/v1/co/perm/snapshot", _auth()),  # 当前操作员的有效权限
)

_LINE = {"type": "26", "id": "0000000001", "amount": 5}
# 应收应付处理各写路由的最小合法请求体（不含登录字段）。
ARAP_BATCH_WRITES = (
    ("/v1/co/arap/transfer", {"flag": "AR", "customer": "C001", "vendor": "V001", "ar_lines": [_LINE],
                              "ap_lines": [{"type": "01", "id": "0000000002", "amount": 5}]}),
    ("/v1/co/arap/merge", {"flag": "AR", "from": "C001", "to": "C002", "lines": [_LINE]}),
    ("/v1/co/arap/red_offset", {"flag": "AR", "partner": "C001", "red": [_LINE],
                                "blue": [{"type": "26", "id": "0000000003", "amount": 5}]}),
    ("/v1/co/arap/process/cancel", {"flag": "AR", "cancel_no": "YCFAP000000000001"}),
    ("/v1/co/arap/process/voucher", {"flag": "AR", "cancel_nos": ["YCFAP000000000001"]}),
    ("/v1/co/arap/exchange_gain", {"flag": "AR", "currency": "美元"}),
    ("/v1/co/arap/exchange_gain/cancel", {"flag": "AR"}),
    ("/v1/co/arap/bad_debt", {"action": "provision"}),  # 坏账（只对测试账套开放）
    # 应收票据登记、删除、处理。
    ("/v1/co/notes/create", {"flag": "AR", "note_no": "N2026001", "settle_code": "301", "amount": 100,
                             "sign_date": "2026-08-01", "receipt_date": "2026-08-10", "expire_date": "2027-02-01",
                             "customer": "C001", "dept": "D01", "receiver": "客户甲"}),
    ("/v1/co/notes/delete", {"flag": "AR", "note_no": "N2026001"}),
    ("/v1/co/notes/process", {"flag": "AR", "op": "settle", "note": "N2026001", "bank_code": "100201"}),
)
_WRITES = (
    ("/v1/co/sale-orders/verify", _auth(id=1, action="verify")),
    ("/v1/co/dispatches/verify", _auth(id=1, action="unverify")),
    ("/v1/co/vouchers/verify", _auth(type="sale_order", id=1, action="verify")),
    ("/v1/co/vouchers/create", _auth(type="sale_order", head={"a": "b"}, lines=[{"a": 1}])),
    ("/v1/co/vouchers/delete", _auth(type="sale_order", id=1)),
    ("/v1/co/vouchers/update", _auth(type="sale_order", id=1, head={"cMemo": "a"})),
    ("/v1/co/vouchers/close", _auth(type="sale_order", id=1, action="close")),
    ("/v1/co/vouchers/generate", _auth(type="sale_out", id=1)),
    ("/v1/co/vouchers/lock", _auth(type="sale_order", id=1, action="lock")),
    (
        "/v1/co/arap/writeoff",
        _auth(receipt={"type": "ar_receipt", "id": 1}, items=[{"type": "sale_invoice", "id": 2, "amount": 1}]),
    ),
    ("/v1/co/arap/writeoff/cancel", _auth(flag="AR", cancel_no="HXAR0000000000001")),
    ("/v1/co/arap/writeoff/auto", _auth(flag="AR", partner="C001")),
    ("/v1/co/arap/voucher", _auth(flag="AR", type="sale_invoice", id=1)),
    ("/v1/co/arap/voucher/delete", _auth(flag="AR", pz_id="AR0000000000001")),
    # 应收应付处理（汇兑损益只对测试账套开放，权限分级与其他写路由相同）。
    *((path, _auth(**body)) for path, body in ARAP_BATCH_WRITES),
    ("/v1/co/openings/post", _auth(module="pu", action="post")),  # 采购期初记账
    ("/v1/co/openings/arap", _auth(side="ar", action="verify", id=1)),  # 应收应付期初单据
    ("/v1/co/periods/close", _auth(module="gl", fiscal_year=2026, period=9, action="close")),  # 月末结账
    ("/v1/co/ia/post", _auth(fiscal_year=2026, period=9, action="post")),  # 存货核算记账
    ("/v1/co/ia/period_end", _auth(fiscal_year=2026, period=9, action="run")),  # 存货核算期末处理
    ("/v1/co/workflow/submit", _auth(**_QM)),
    ("/v1/co/workflow/withdraw", _auth(**_QM)),
    ("/v1/co/workflow/approve", _auth(**_QM)),
    ("/v1/co/workflow/disagree", _auth(opinion="不同意", **_QM)),
    ("/v1/co/workflow/return", _auth(opinion="退回", **_QM)),
    ("/v1/co/workflow/abandon", _auth(**_QM)),
    ("/v1/co/workflow/resubmit", _auth(**_QM)),
    ("/v1/co/gl/vouchers/create", _auth(head=_HEAD, lines=_LINES)),
    ("/v1/co/gl/vouchers/update", _auth(head=_HEAD, lines=_LINES, **_KEY)),
    *((f"/v1/co/gl/vouchers/{op}", _auth(**_KEY)) for op in ("void", "unvoid", "verify", "unverify")),
    *((f"/v1/co/gl/vouchers/{op}", _auth(**_KEY)) for op in ("sign", "unsign", "delete")),
    ("/v1/co/gl/vouchers/post", _auth(period=9, vouchers=[{"sign": "转", "no": 3}])),  # R8 记账
    ("/v1/co/gl/vouchers/unpost", _auth()),  # 取消记账（测试账套）
    ("/v1/co/gl/vouchers/reverse", _auth(**_KEY)),  # 红字冲销
    ("/v1/co/gl/transfer/pnl", _auth(fiscal_year=2026, period=9)),  # 期间损益结转（测试账套）
    ("/v1/co/gl/transfer/custom", _auth(fiscal_year=2026, period=9, tran_id="0001")),  # 自定义转账（测试账套）
    ("/v1/co/archives/create", _auth(fields={"name": "客户甲"}, **_ARC)),
    ("/v1/co/archives/update", _auth(fields={"name": "客户乙"}, **_ARC)),
    ("/v1/co/archives/delete", _auth(**_ARC)),
)

# 不在路由表里的 GET 路由：健康检查、字段元数据。
_GET_ACTIONS = {"co:health", "co:meta"}
_ALL = {route.path: route for route in ROUTES + GL_ARC_ROUTES}
_ALL.update({route.path: route for route in REPORT_ROUTES})
_ALL.update({route.path: route for route in ATTACH_ROUTES})
_ALL.update({route.path: route for route in NOTES_READ_ROUTES})
_ALL.update({route.path: route for route in NOTES_REG_ROUTES + NOTES_PROC_ROUTES})
_ALL.update({route.path: route for route in ARAP_VOUCHER_ROUTES})
_ALL.update({route.path: route for route in ARAP_PROC_ROUTES})
_ALL.update({route.path: route for route in ARAP_PROC_LIST_ROUTES})
_ALL.update({route.path: route for route in OPENING_ROUTES})
_ALL.update({route.path: route for route in PERIOD_ROUTES})
_ALL.update({route.path: route for route in IA_ROUTES})
_ALL.update({route.path: route for route in AI_ROUTES})
_ALL.update({route.path: route for route in LOOKUP_ROUTES})
_ALL.update({route.path: route for route in IC_WRITE_ROUTES})
_ALL.update({route.path: route for route in PERM_ROUTES})
_ALL.update({route.path: route for route in GL_TRANSFER_ROUTES})


def _reader(fake: FakeBridge, **extra):
    return _client(fake, claims=base_claims(u8co_read=True, **extra))


def test_every_route_is_classified_exactly_once() -> None:
    actions = {route.action for route in _ALL.values()} | _GET_ACTIONS
    # 经营管理路由（co_routes_mgmt）只认经营管理权限，在 test_co_mgmt_* 里单独测。
    assert actions == set(ACCESS) - MGMT_ACTIONS
    assert set(ACCESS.values()) == {READ, WRITE, MGMT, PERM_EVALUATE}
    # 公司间路由一次请求调多个账套的桥，读写分级在 test_co_ic_* 里单独测。
    reads = {k for k, v in ACCESS.items() if v == READ} - _GET_ACTIONS - FANOUT_ACTIONS
    assert {_ALL[path].action for path, _ in _READS} == reads
    assert {_ALL[path].action for path, _ in _WRITES} == {k for k, v in ACCESS.items() if v == WRITE} - FANOUT_ACTIONS


@pytest.mark.parametrize(("path", "body"), _READS)
def test_read_only_caller_reaches_the_bridge_on_reads(path: str, body: dict) -> None:
    fake = FakeBridge()
    ok = _reader(fake).post(path, json=body)
    assert ok.status_code == 200, ok.text
    assert len(fake.calls) == 1
    assert "/v1/co" + fake.calls[0][0].removeprefix("/v1") == path


def test_read_only_caller_may_check_health() -> None:
    ok = _reader(FakeBridge()).get("/v1/co/health")
    assert ok.status_code == 200, ok.text


@pytest.mark.parametrize(("path", "body"), _WRITES)
def test_read_only_caller_is_403_on_writes_without_calling(path: str, body: dict) -> None:
    fake = FakeBridge()
    denied = _reader(fake).post(path, json=body)
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert denied.json()["error"]["message"] == "只读权限不能调用写操作"
    assert fake.calls == []


@pytest.mark.parametrize(("path", "body"), _WRITES + _READS)
def test_full_caller_is_unchanged(path: str, body: dict) -> None:
    fake = FakeBridge()
    ok = _client(fake).post(path, json=body)
    assert ok.status_code == 200, ok.text
    assert len(fake.calls) == 1
    both = _client(fake, claims=base_claims(u8co_write=True, u8co_read=True)).post(path, json=body)
    assert both.status_code == 200, both.text


def test_read_claim_must_be_a_boolean() -> None:
    fake = FakeBridge()
    client = _client(fake, claims=base_claims(u8co_read="true"))
    denied = client.post("/v1/co/vouchers/load", json=_auth(type="sale_order", id=1))
    assert denied.status_code == 403
    assert denied.json()["error"]["message"] == "无权使用 CO 接口"
    assert fake.calls == []


def test_read_only_caller_is_still_limited_to_the_account_list() -> None:
    fake = FakeBridge()
    denied = _reader(fake).post("/v1/co/vouchers/load", json=_auth(type="sale_order", id=1, acc="001"))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []


def test_claims_parse_into_the_caller() -> None:
    reader = caller_from(base_claims(u8co_read=True), entry())
    assert reader.read is True
    assert reader.write is False
    assert reader.name == "tool"
    assert reader.client_id == "tool-a"
    plain = caller_from(base_claims(u8co_write=True), entry())
    assert plain.read is False
    assert plain.accounts is None


def test_openapi_names_the_level_of_every_route() -> None:
    spec = _spec()
    for path, route in _ALL.items():
        text = spec["paths"][path]["post"]["description"]
        # 公司间多账套报表为经营管理，perm/evaluate 为权限评估。
        want = {READ: "权限：只读", WRITE: "权限：写", MGMT: "权限：经营管理", PERM_EVALUATE: "权限：权限评估"}[
            ACCESS[route.action]
        ]
        assert want in text, path
    assert "权限：只读" in spec["paths"]["/v1/co/health"]["get"]["description"]
    assert "u8co_read" in spec["info"]["description"]
