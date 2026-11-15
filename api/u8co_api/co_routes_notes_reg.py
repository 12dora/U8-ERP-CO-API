"""应收票据登记、删除路由（写）。注册见 co_routes.register_co_routes。"""

from u8co_api.co_models_notes_reg import (
    CREATE_HELP,
    CREATE_SUMMARY,
    DELETE_HELP,
    DELETE_SUMMARY,
    CoNoteCreateIn,
    CoNoteDeleteIn,
    CoNoteRegOut,
)
from u8co_api.co_routes_arap_proc import TAG_ARAP_PROC
from u8co_api.co_table import CoRoute

NOTES_REG_ROUTES = (
    CoRoute(
        "/v1/co/notes/create",
        "/v1/notes/create",
        CoNoteCreateIn,
        CoNoteRegOut,
        CREATE_SUMMARY,
        CREATE_HELP,
        "coNoteCreate",
        "co:notes/create",
        TAG_ARAP_PROC,
    ),
    CoRoute(
        "/v1/co/notes/delete",
        "/v1/notes/delete",
        CoNoteDeleteIn,
        CoNoteRegOut,
        DELETE_SUMMARY,
        DELETE_HELP,
        "coNoteDelete",
        "co:notes/delete",
        TAG_ARAP_PROC,
    ),
)
