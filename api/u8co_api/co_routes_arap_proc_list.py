"""应收 / 应付处理记录路由（读，事件源）：/v1/co/arap/process/list。注册见 co_routes.register_co_routes。"""

from u8co_api.co_models_arap_proc_list import (
    PROC_LIST_HELP,
    PROC_LIST_SUMMARY,
    CoArapProcListIn,
    CoArapProcListOut,
)
from u8co_api.co_routes_arap_proc import TAG_ARAP_PROC
from u8co_api.co_table import CoRoute

ARAP_PROC_LIST_ROUTES = (
    CoRoute(
        "/v1/co/arap/process/list",
        "/v1/arap/process/list",
        CoArapProcListIn,
        CoArapProcListOut,
        PROC_LIST_SUMMARY,
        PROC_LIST_HELP,
        "coArapProcessList",
        "co:arap/process/list",
        TAG_ARAP_PROC,
    ),
)
