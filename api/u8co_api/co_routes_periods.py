"""月末结账、存货核算记账与期末处理路由（写）。注册见 co_routes.register_co_routes。"""

from u8co_api.co_models_ia import (
    IA_PERIOD_END_HELP,
    IA_PERIOD_END_SUMMARY,
    IA_POST_HELP,
    IA_POST_SUMMARY,
    IaOut,
    IaPeriodEndIn,
    IaPostIn,
)
from u8co_api.co_models_periods import (
    PERIODS_CLOSE_HELP,
    PERIODS_CLOSE_SUMMARY,
    PeriodsCloseIn,
    PeriodsCloseOut,
)
from u8co_api.co_table import CoRoute

TAG_PERIOD = "月末结账"
TAG_IA = "存货核算"

PERIOD_ROUTES = (
    CoRoute(
        "/v1/co/periods/close",
        "/v1/periods/close",
        PeriodsCloseIn,
        PeriodsCloseOut,
        PERIODS_CLOSE_SUMMARY,
        PERIODS_CLOSE_HELP,
        "coPeriodsClose",
        "co:periods/close",
        TAG_PERIOD,
    ),
)

# 存货核算正常单据记账 / 恢复记账、期末处理 / 取消期末处理（只对测试账套开放）。
IA_ROUTES = (
    CoRoute(
        "/v1/co/ia/post",
        "/v1/ia/post",
        IaPostIn,
        IaOut,
        IA_POST_SUMMARY,
        IA_POST_HELP,
        "coIaPost",
        "co:ia/post",
        TAG_IA,
    ),
    CoRoute(
        "/v1/co/ia/period_end",
        "/v1/ia/period_end",
        IaPeriodEndIn,
        IaOut,
        IA_PERIOD_END_SUMMARY,
        IA_PERIOD_END_HELP,
        "coIaPeriodEnd",
        "co:ia/period_end",
        TAG_IA,
    ),
)
