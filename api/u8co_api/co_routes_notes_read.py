"""票据读取路由（只读）。注册见 co_routes.register_co_routes。票据列表走 /v1/co/vouchers/list（type=ar_note / ap_note）。"""

from u8co_api.co_models_notes_read import NOTE_GET_HELP, NOTE_GET_SUMMARY, CoNoteGetIn, CoNoteGetOut
from u8co_api.co_table import TAG_READ, CoRoute

NOTES_READ_ROUTES = (
    CoRoute(
        "/v1/co/notes/get",
        "/v1/notes/get",
        CoNoteGetIn,
        CoNoteGetOut,
        NOTE_GET_SUMMARY,
        NOTE_GET_HELP,
        "coNoteGet",
        "co:notes/get",
        TAG_READ,
    ),
)
