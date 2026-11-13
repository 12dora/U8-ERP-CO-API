"""销售发票参照多张发货单（vouchers/generate，type=sale_invoice，正文 sources 代替 id + lines）。混入 U8CoClient。

sources 每项 {id: 发货单 DLID, lines: [...]}，行形状同 generate_voucher 的 lines（source_line_id、quantity，另可带 cmemo、
cdefine22–37 和回签字段 cbdefine1 回签数量 / cbdefine2 回签含税价 / cbdefine3 回签价税合计）。1 到 200 张、id 不重复，
合计 1 到 200 行。同一客户、业务类型、币种、汇率、表头税率，数量不超过剩余可开票数量：由桥查库核对。
只有一张发货单时也可以用 generate_voucher（id + lines），两者在桥上是同一条路径。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_gl_arc import _common
from co.client.u8co_idem import with_key
from co.client.u8co_rows import _present_row, _rows

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

_ID_MAX = 2147483647
_MAX = 200


@dataclass(frozen=True)
class InvoiceSources:
    """sources：[{"id": DLID, "lines": [...]}, ...]；head 同 generate_voucher（cvouchtype 26 / 27、ddate、cmemo、cdefine1–16）。"""

    sources: Any
    head: Any = None
    source_type: str = ""


def invoice_body(call: U8Call, draft: InvoiceSources) -> dict[str, Any]:
    if draft.source_type not in ("", "dispatch"):
        raise ValueError("sources 只用于参照发货单生成销售发票（source_type 省略或 dispatch）")
    body = _common(call)
    body["type"] = "sale_invoice"
    if draft.source_type:
        body["source_type"] = draft.source_type
    head = _present_row(draft.head)
    if head is not None:
        body["head"] = head
    body["sources"] = _sources(draft.sources)
    return body


def _sources(raw: object) -> list[dict[str, Any]]:
    if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= _MAX:
        raise ValueError("sources 必须是 1 到 200 张发货单")
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    total = 0
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"id", "lines"}:
            raise ValueError("sources 的每一项只能有 id 和 lines")
        doc = item["id"]
        if type(doc) is not int or not 1 <= doc <= _ID_MAX:
            raise ValueError("sources 的 id 必须是正整数")
        if doc in seen:
            raise ValueError("来源发货单重复")
        seen.add(doc)
        lines = _rows(item["lines"])
        total += len(lines)
        out.append({"id": doc, "lines": lines})
    if total > _MAX:
        raise ValueError("各来源合计的明细必须是 1 到 200 行")
    return out


class U8CoInvoiceMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def generate_invoice_from_dispatches(
        self, call: U8Call, draft: InvoiceSources, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """一张销售发票参照多张发货单；call.doc_id 不用（来源 id 在 sources 里）。响应另有 source_ids。"""
        return self.call("/v1/vouchers/generate", with_key(invoice_body(call, draft), idempotency_key))
