"""公司间测试共用：占位的公司间对照（甲 801 / 乙 802 / 丙 803）和按账套应答的假桥。不访问网络。"""

from __future__ import annotations

import copy

from tests.support import base_claims, entry
from tests.test_co_gate import _SECRET, _client
from u8co_api.co_ic_map import IcMap, parse_ic_map
from u8co_api.errors import ApiError

ACCS = ("801", "802", "803")
MAP_DATA = {
    "groups": [
        {
            "id": "grp1",
            "accounts": ["801", "802", "803"],
            "names": {"801": "甲公司", "802": "乙公司", "803": "丙公司"},
            "as_customer": {
                "801": {"802": "C900001", "803": "C900002"},
                "802": {"801": "C900001", "803": "C900002"},
                "803": {"801": "C900001", "802": "C900002"},
            },
            "as_vendor": {
                "801": {"802": "S900001", "803": "S900002"},
                "802": {"801": "S900002", "803": "S900001"},
                "803": {"802": "S900001"},
            },
            "inventory": [
                {"id": "inv1", "codes": {"801": "A901", "802": "A901"}, "match": "code"},
                {"id": "lig", "codes": {"801": "A0001", "803": "B0001"}, "match": "qty_date"},
            ],
            "gl": [
                {"logical": "ic_ar", "codes": {"801": "112299", "802": "112299", "803": "112299"}},
                {"logical": "ic_ap", "codes": {"801": "220299", "802": "220299", "803": "220299"}},
            ],
            "elim": [{"rule": "ar_ap", "pairs": [{"ar": ["803", "ic_ar", "C900001"], "ap": ["801", "ic_ap", "S900002"]}]}],
        },
        {"id": "grp9", "accounts": ["998", "999"]},
    ]
}


def map_data() -> dict:
    return copy.deepcopy(MAP_DATA)


def sample_map() -> IcMap:
    return parse_ic_map(map_data())


def login(acc: str, **extra) -> dict:
    body = {"acc": acc, "operator": "op001", "password": _SECRET}
    body.update(extra)
    return body


# 公司间对账、多账套汇总、合并报表要经营管理权限：测试的信任项配 mgmt_claim，令牌缺省同时有写和经营管理权限。
IC_TRUST = entry(mgmt_claim="u8co_mgmt")


def ic_claims(**extra) -> dict:
    return base_claims(u8co_mgmt=True, **extra)


def ic_client(fake, claims=None, **flags):
    flags.setdefault("co_accounts", ACCS)
    flags.setdefault("ic_map", sample_map())
    flags.setdefault("trust", (IC_TRUST,))
    return _client(fake, claims=claims or ic_claims(u8co_write=True), **flags)


class IcBridge:
    """按请求体里的 acc 应答。search / stock_ledger / order_execution 按 limit 翻页（next 为最后一项的序号）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.heads: dict[tuple[str, str], list[dict]] = {}
        self.ledger: dict[tuple[str, str], list[dict]] = {}
        self.docs: dict[tuple[str, int], dict] = {}
        self.orders: dict[str, list[dict]] = {}
        self.errors: dict[str, ApiError] = {}
        self.reply: dict | None = None
        self.idem: dict | None = None

    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        acc = payload.get("acc")
        if acc in self.errors:
            raise self.errors[acc]
        if path == "/v1/vouchers/search":
            return self._page(self.heads.get((acc, payload["type"]), []), payload)
        if path == "/v1/reports/stock_ledger":
            return self._page(self.ledger.get((acc, payload["inv"]), []), payload)
        if path == "/v1/reports/order_execution":
            return self._page(self.orders.get(acc, []), payload)
        if path == "/v1/vouchers/load_many":
            return {"ok": True, "type": payload["type"], "items": [self._doc(acc, doc_id) for doc_id in payload["ids"]]}
        if path == "/v1/vouchers/generate":
            return self._generated(payload)
        if path == "/v1/idempotency/get" and self.idem is not None:
            return self.idem
        return {"ok": True}

    def health(self) -> dict:
        return {"ok": True, "version": "test"}

    def paths(self, acc: str | None = None) -> list[str]:
        return [path for path, payload in self.calls if acc is None or payload.get("acc") == acc]

    def _doc(self, acc: str, doc_id: int) -> dict:
        found = self.docs.get((acc, doc_id))
        if found is None:
            return {"id": doc_id, "error": {"code": "not_found", "message": "单据不存在"}}
        return found

    def _page(self, rows: list[dict], payload: dict) -> dict:
        start = int(payload.get("after") or 0)
        limit = int(payload.get("limit") or 100)
        chunk = rows[start : start + limit]
        out: dict = {"ok": True, "items": chunk}
        if start + limit < len(rows):
            out["next"] = start + limit
        return out

    def _generated(self, payload: dict) -> dict:
        if self.reply is not None:
            return self.reply
        if payload.get("dry_run") is True:
            return {"ok": True, "dry_run": True, "mode": "rollback", "route": "vouchers/generate", "type": payload["type"]}
        state = {"verified": False, "verifier": "", "verified_at": ""}
        return {"ok": True, "type": payload["type"], "id": 77, "code": "0000000077", "state": state, "lines": 1}
