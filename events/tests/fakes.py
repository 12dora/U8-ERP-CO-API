"""离线测试用的假桥和配置。不连网络、不需要 Redis。"""

from __future__ import annotations

from typing import Any

from u8co_events.bridge import DocumentNotFound, PageRequest
from u8co_events.config import Config, from_dict


def make_config(state_path: str, **top: Any) -> Config:
    data: dict[str, Any] = {
        "state_path": state_path,
        "bridge": {"base_url": "http://192.0.2.10:8765/u8co", "secret_file": "/run/secrets/u8co"},
        "accounts": [{"acc": "999", "operator_file": "/run/secrets/op.json", "types": ["sale_order"]}],
        "page_limit": 2,
    }
    data.update(top)
    return from_dict(data, env={})


def doc(doc_id: int, ufts: int, **state: Any) -> dict[str, Any]:
    row = {
        "id": doc_id,
        "code": f"SO{doc_id:04d}",
        "ufts": str(ufts),
        "verified": False,
        "closed": False,
        "red": False,
        "verifier": None,
        "closer": None,
        "doc_date": "2026-09-28",
    }
    row.update(state)
    return row


class FakeLister:
    """按桥的列表契约回答：changed_since 按 ufts 过滤，按 id 翻页，watermark 是见过的最大 ufts（只增不减）。"""

    def __init__(self) -> None:
        self.docs: dict[str, dict[int, dict[str, Any]]] = {}
        self.calls: list[tuple[str, PageRequest]] = []
        self.fail_at: int | None = None
        # 数据库的 rowversion 只增不减：删单据不会让水位变小。测试「库被还原」时直接改小它。
        self.high = 0
        # 模拟列表或权限出问题：列表一行都不返回，但 load 照常读得到。
        self.list_hidden = False
        self.loads: list[tuple[str, str, int]] = []
        self.load_errors: dict[int, Exception] = {}

    def put(self, type_: str, row: dict[str, Any]) -> None:
        self.docs.setdefault(type_, {})[row["id"]] = dict(row)

    def drop(self, type_: str, doc_id: int) -> None:
        del self.docs[type_][doc_id]

    def list_page(self, acc: str, request: PageRequest) -> dict[str, Any]:
        self.calls.append((acc, request))
        if self.fail_at is not None and len(self.calls) >= self.fail_at:
            raise ConnectionError("假桥：连接断开")
        rows = sorted(self.docs.get(request.type, {}).values(), key=lambda row: row["id"])
        self.high = max([self.high, *(int(row["ufts"]) for row in rows)])
        mark = self.high
        if self.list_hidden:
            rows = []
        if request.changed_since:
            rows = [row for row in rows if int(row["ufts"]) > int(request.changed_since)]
        if request.after is not None:
            rows = [row for row in rows if row["id"] > request.after]
        page = rows[: request.limit]
        following = page[-1]["id"] if len(rows) > request.limit else None
        if request.keys_only:
            page = [{"id": row["id"], "code": row["code"], "ufts": row["ufts"]} for row in page]
        return {"ok": True, "type": request.type, "items": page, "next": following, "watermark": str(mark)}

    def load(self, acc: str, type_: str, doc_id: int) -> dict[str, Any]:
        """按桥的 vouchers/load 契约：单据不存在是错误体里显式带 code=not_found 的 404（ProofClient 抛 DocumentNotFound）。"""
        self.loads.append((acc, type_, doc_id))
        if doc_id in self.load_errors:
            raise self.load_errors[doc_id]
        row = self.docs.get(type_, {}).get(doc_id)
        if row is None:
            raise DocumentNotFound(404, "not_found", "单据不存在")
        return {"ok": True, "type": type_, "id": doc_id, "code": row["code"], "head": {}, "lines": []}
