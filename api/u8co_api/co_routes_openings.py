"""期初记账、应收应付期初单据路由（写）。注册见 co_routes.register_co_routes。"""

from u8co_api.co_models_openings import (
    OPENINGS_ARAP_HELP,
    OPENINGS_ARAP_SUMMARY,
    OPENINGS_POST_HELP,
    OPENINGS_POST_SUMMARY,
    OpeningsArapIn,
    OpeningsArapOut,
    OpeningsPostIn,
    OpeningsPostOut,
)
from u8co_api.co_table import CoRoute

TAG_OPENING = "期初记账"

OPENING_ROUTES = (
    CoRoute(
        "/v1/co/openings/post",
        "/v1/openings/post",
        OpeningsPostIn,
        OpeningsPostOut,
        OPENINGS_POST_SUMMARY,
        OPENINGS_POST_HELP,
        "coOpeningsPost",
        "co:openings/post",
        TAG_OPENING,
    ),
    # 应收 / 应付期初单据（只对测试账套开放）。
    CoRoute(
        "/v1/co/openings/arap",
        "/v1/openings/arap",
        OpeningsArapIn,
        OpeningsArapOut,
        OPENINGS_ARAP_SUMMARY,
        OPENINGS_ARAP_HELP,
        "coOpeningsArap",
        "co:openings/arap",
        TAG_OPENING,
    ),
)
