"""应收 / 应付制单、取消制单路由（写）。注册见 co_routes.register_co_routes。"""

from u8co_api.co_models_arap_voucher import (
    DELETE_HELP,
    DELETE_SUMMARY,
    VOUCHER_HELP,
    VOUCHER_SUMMARY,
    CoArapVoucherDeleteIn,
    CoArapVoucherDeleteOut,
    CoArapVoucherIn,
    CoArapVoucherOut,
)
from u8co_api.co_table import CoRoute

ARAP_VOUCHER_ROUTES = (
    CoRoute(
        "/v1/co/arap/voucher",
        "/v1/arap/voucher",
        CoArapVoucherIn,
        CoArapVoucherOut,
        VOUCHER_SUMMARY,
        VOUCHER_HELP,
        "coArapVoucher",
        "co:arap/voucher",
    ),
    CoRoute(
        "/v1/co/arap/voucher/delete",
        "/v1/arap/voucher/delete",
        CoArapVoucherDeleteIn,
        CoArapVoucherDeleteOut,
        DELETE_SUMMARY,
        DELETE_HELP,
        "coArapVoucherDelete",
        "co:arap/voucher/delete",
    ),
)
