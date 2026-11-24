"""存货核算制单、取消制单路由（写，测试账套）。注册见 co_routes.register_co_routes。"""

from u8co_api.co_models_ia_voucher import (
    DELETE_HELP,
    DELETE_SUMMARY,
    VOUCHER_HELP,
    VOUCHER_SUMMARY,
    CoIaVoucherDeleteIn,
    CoIaVoucherDeleteOut,
    CoIaVoucherIn,
    CoIaVoucherOut,
)
from u8co_api.co_routes_periods import TAG_IA
from u8co_api.co_table import CoRoute

IA_VOUCHER_ROUTES = (
    CoRoute(
        "/v1/co/ia/voucher",
        "/v1/ia/voucher",
        CoIaVoucherIn,
        CoIaVoucherOut,
        VOUCHER_SUMMARY,
        VOUCHER_HELP,
        "coIaVoucher",
        "co:ia/voucher",
        TAG_IA,
    ),
    CoRoute(
        "/v1/co/ia/voucher/delete",
        "/v1/ia/voucher/delete",
        CoIaVoucherDeleteIn,
        CoIaVoucherDeleteOut,
        DELETE_SUMMARY,
        DELETE_HELP,
        "coIaVoucherDelete",
        "co:ia/voucher/delete",
        TAG_IA,
    ),
)
