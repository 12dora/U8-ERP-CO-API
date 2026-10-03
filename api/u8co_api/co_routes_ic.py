"""公司间路由：公司间对账（只读，登记在报表路由里）和按卖方单据生成买方单据（写）。

两条路由都不直接转给桥：runner 按账套调多次桥（co_ic_core）。bridge_path 只用于预演、幂等键和文档的分类，
generate_buyer 的幂等键实际随 vouchers/generate 记在买方账套的桥上。
"""

from __future__ import annotations

from u8co_api.co_ic_generate import run_generate
from u8co_api.co_ic_match import run_match
from u8co_api.co_models_ic import (
    GENERATE_HELP,
    GENERATE_SUMMARY,
    MATCH_HELP,
    MATCH_SUMMARY,
    IcGenerateIn,
    IcGenerateOut,
    IcMatchIn,
    IcMatchOut,
)
from u8co_api.co_table import TAG_GEN, CoRoute

TAG_REPORT = "报表"

IC_MATCH_ROUTE = CoRoute(
    "/v1/co/reports/intercompany_match",
    "/v1/reports/intercompany_match",
    IcMatchIn,
    IcMatchOut,
    MATCH_SUMMARY,
    MATCH_HELP,
    "coReportIntercompanyMatch",
    "co:reports/intercompany_match",
    TAG_REPORT,
    runner=run_match,
)

IC_WRITE_ROUTES = (
    CoRoute(
        "/v1/co/intercompany/generate_buyer",
        "/v1/intercompany/generate_buyer",
        IcGenerateIn,
        IcGenerateOut,
        GENERATE_SUMMARY,
        GENERATE_HELP,
        "coIntercompanyGenerateBuyer",
        "co:intercompany/generate_buyer",
        TAG_GEN,
        runner=run_generate,
    ),
)
