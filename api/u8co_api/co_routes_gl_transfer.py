"""期间损益结转、自定义转账路由（写，测试账套）。注册见 co_routes.register_co_routes。"""

from u8co_api.co_models_gl_transfer import (
    CUSTOM_HELP,
    CUSTOM_SUMMARY,
    PNL_HELP,
    PNL_SUMMARY,
    GlTransferCustomIn,
    GlTransferOut,
    GlTransferPnlIn,
)
from u8co_api.co_routes_gl_arc import TAG_GL
from u8co_api.co_table import CoRoute

GL_TRANSFER_ROUTES = (
    CoRoute(
        "/v1/co/gl/transfer/pnl",
        "/v1/gl/transfer/pnl",
        GlTransferPnlIn,
        GlTransferOut,
        PNL_SUMMARY,
        PNL_HELP,
        "coGlTransferPnl",
        "co:gl/transfer/pnl",
        TAG_GL,
    ),
    CoRoute(
        "/v1/co/gl/transfer/custom",
        "/v1/gl/transfer/custom",
        GlTransferCustomIn,
        GlTransferOut,
        CUSTOM_SUMMARY,
        CUSTOM_HELP,
        "coGlTransferCustom",
        "co:gl/transfer/custom",
        TAG_GL,
    ),
)
