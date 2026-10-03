"""应收 / 应付票据读取：/v1/notes/get。混入 U8CoClient。

票据（AP_Note）只是列表类型：列表用 list_vouchers(VoucherQuery("ar_note" / "ap_note"))，单张用 note_get。
key 是整数时按 id（AP_Note.Auto_ID）读，是字符串时按票据号读。票据不存在时桥回 404 not_found（U8CoNotFound）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from co.client.u8co_kinds import NOTE_KINDS
from co.client.u8co_gl_arc import _common

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

NOTE_GET_ROUTE = "/v1/notes/get"
_ID_MAX = 2147483647


class U8CoNotesReadMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def note_get(self, call: U8Call, kind: str, key: int | str) -> dict[str, Any]:
        return self.call(NOTE_GET_ROUTE, note_get_fields(call, kind, key))


def note_get_fields(call: U8Call, kind: str, key: int | str) -> dict[str, Any]:
    if kind not in NOTE_KINDS:
        raise ValueError("票据类型只能是 ar_note 或 ap_note")
    fields = _common(call)
    fields["type"] = kind
    if type(key) is int:
        if not 1 <= key <= _ID_MAX:
            raise ValueError("票据 id 必须是 1 到 2147483647 的整数")
        fields["id"] = key
        return fields
    if type(key) is not str or not 1 <= len(key) <= 60 or any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in key):
        raise ValueError("票据号必须是 1 到 60 个字符，不含控制字符")
    fields["code"] = key
    return fields
