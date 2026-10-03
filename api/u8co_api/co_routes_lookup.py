"""只读路由：字段标签、单据查询、批量读取单据和档案。注册见 co_routes.register_co_routes。"""

from __future__ import annotations

from u8co_api.co_models_lookup import (
    FIELDS_HELP,
    FIELDS_SUMMARY,
    GET_MANY_HELP,
    GET_MANY_SUMMARY,
    LOAD_MANY_HELP,
    LOAD_MANY_SUMMARY,
    SEARCH_HELP,
    SEARCH_SUMMARY,
    GetManyIn,
    GetManyOut,
    LoadManyIn,
    LoadManyOut,
    MetaFieldsIn,
    MetaFieldsOut,
    VoucherSearchIn,
    VoucherSearchOut,
)
from u8co_api.co_routes_gl_arc import TAG_ARC
from u8co_api.co_table import TAG_CO, TAG_READ, CoRoute

LOOKUP_ROUTES = (
    CoRoute(
        "/v1/co/meta/fields",
        "/v1/meta/fields",
        MetaFieldsIn,
        MetaFieldsOut,
        FIELDS_SUMMARY,
        FIELDS_HELP,
        "coMetaFields",
        "co:meta/fields",
        TAG_CO,
    ),
    CoRoute(
        "/v1/co/vouchers/search",
        "/v1/vouchers/search",
        VoucherSearchIn,
        VoucherSearchOut,
        SEARCH_SUMMARY,
        SEARCH_HELP,
        "coVoucherSearch",
        "co:vouchers/search",
        TAG_READ,
    ),
    CoRoute(
        "/v1/co/vouchers/load_many",
        "/v1/vouchers/load_many",
        LoadManyIn,
        LoadManyOut,
        LOAD_MANY_SUMMARY,
        LOAD_MANY_HELP,
        "coVoucherLoadMany",
        "co:vouchers/load_many",
        TAG_READ,
    ),
    CoRoute(
        "/v1/co/archives/get_many",
        "/v1/archives/get_many",
        GetManyIn,
        GetManyOut,
        GET_MANY_SUMMARY,
        GET_MANY_HELP,
        "coArchiveGetMany",
        "co:archives/get_many",
        TAG_ARC,
    ),
)
