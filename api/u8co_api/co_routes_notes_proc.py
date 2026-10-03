"""票据处理路由（写）：应收票据的结算、贴现、背书。注册见 co_routes.register_co_routes。
取消、制单走 arap/process/cancel、arap/process/voucher。"""

from u8co_api.co_models_notes_proc import PROC_HELP, PROC_SUMMARY, CoNoteProcIn, CoNoteProcOut
from u8co_api.co_routes_arap_proc import TAG_ARAP_PROC
from u8co_api.co_table import CoRoute

NOTES_PROC_ROUTES = (
    CoRoute(
        "/v1/co/notes/process",
        "/v1/notes/process",
        CoNoteProcIn,
        CoNoteProcOut,
        PROC_SUMMARY,
        PROC_HELP,
        "coNoteProcess",
        "co:notes/process",
        TAG_ARAP_PROC,
    ),
)
