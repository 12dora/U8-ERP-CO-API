"""经营管理查询路由 /v1/co/mgmt/*（注册见 co_routes.POST_ROUTES）。

runner 按账套多次调桥（co_mgmt_core），bridge_path 只是分类。权限是 co_access.MGMT（只认经营管理权限）。
其它经营管理报表（sales、arap、cash_stock、overview）的路由追加在 MGMT_ROUTES 末尾。
"""

from __future__ import annotations

from typing import Any

from u8co_api.co_mgmt_arap import ARAP_PATH, run_arap
from u8co_api.co_mgmt_cash import CASH_PATH, run_cash_stock
from u8co_api.co_mgmt_core import META_PATH, MGMT_BRIDGE, begin_mgmt, envelope, finish, report_failures
from u8co_api.co_mgmt_cache import public_meta
from u8co_api.co_mgmt_overview import run_overview
from u8co_api.co_mgmt_pnl import PNL_PATH, run_pnl
from u8co_api.co_mgmt_sales import SALES_PATH, run_sales
from u8co_api.co_models_mgmt import (
    META_HELP,
    META_SUMMARY,
    PNL_HELP,
    PNL_LINES_HELP,
    PNL_SUMMARY,
    MgmtMetaIn,
    MgmtOut,
    MgmtPnlIn,
)
from u8co_api.co_models_mgmt_reports import (
    ARAP_HELP,
    ARAP_SUMMARY,
    CASH_HELP,
    CASH_SUMMARY,
    OVERVIEW_HELP,
    OVERVIEW_SUMMARY,
    SALES_HELP,
    SALES_SUMMARY,
    MgmtArapIn,
    MgmtCashStockIn,
    MgmtOverviewIn,
    MgmtSalesIn,
)
from u8co_api.co_table import CoRoute

TAG_MGMT = "经营管理"


def run_meta(request, caller, action: str, _bridge_path: str, body: MgmtMetaIn) -> dict[str, Any]:
    """各账套的 mgmt/meta 原样列在 by_account，不缓存、不合并。"""
    run = begin_mgmt(request, caller, action, body)
    warnings = report_failures(run.meta)
    # 旧桥水位里的借贷方合计不转给调用方（public_meta）。
    by_account = {acc: {"ok": True, **public_meta(result.body or {})} for acc, result in run.meta.items() if result.ok}
    request.state.mgmt_audit["cache_hit"] = False
    value = finish(run, {"by_account": by_account, "complete": True})
    return envelope(run, value, warnings, {"hit": False, "age_s": 0})


def mgmt_route(name: str, pair: tuple[type, type], text: tuple[str, str], runner, bridge_path: str) -> CoRoute:
    words = "".join(part.title() for part in name.split("_"))
    return CoRoute(
        f"/v1/co/mgmt/{name}",
        bridge_path,
        pair[0],
        pair[1],
        text[0],
        text[1],
        f"coMgmt{words}",
        f"co:mgmt/{name}",
        TAG_MGMT,
        runner=runner,
        keep_null=True,
    )


MGMT_ROUTES = (
    mgmt_route("meta", (MgmtMetaIn, MgmtOut), (META_SUMMARY, META_HELP), run_meta, META_PATH),
    mgmt_route("pnl", (MgmtPnlIn, MgmtOut), (PNL_SUMMARY, PNL_HELP + PNL_LINES_HELP + "。"), run_pnl, PNL_PATH),
    mgmt_route("sales", (MgmtSalesIn, MgmtOut), (SALES_SUMMARY, SALES_HELP), run_sales, SALES_PATH),
    mgmt_route("arap", (MgmtArapIn, MgmtOut), (ARAP_SUMMARY, ARAP_HELP), run_arap, ARAP_PATH),
    mgmt_route("cash_stock", (MgmtCashStockIn, MgmtOut), (CASH_SUMMARY, CASH_HELP), run_cash_stock, CASH_PATH),
    # 关键指标没有对应的桥报表（由利润表、资金存货、往来组合），bridge_path 只是分类。
    mgmt_route(
        "overview", (MgmtOverviewIn, MgmtOut), (OVERVIEW_SUMMARY, OVERVIEW_HELP), run_overview, MGMT_BRIDGE + "overview"
    ),
)
