"""字段权限遮蔽经过 API：桥置为 null 的字段和 masked_fields 原样转出（单据读取、批量读取、列表），
经营管理合并时被遮蔽的金额不当 0（含它的合计为 null，字段名记入 consolidated.masked_fields）。"""

from __future__ import annotations

import copy
from decimal import Decimal

from tests.test_co_gate import FakeBridge, _client
from tests.test_co_mgmt_cash import _cash_fake, _overview_fake
from tests.test_co_mgmt_reports import (
    _B_CASH,
    _B_SALES,
    _CASH,
    _OVERVIEW,
    _SALES,
    _SALES_801,
    _SALES_802,
    ReportFake,
    _client_for,
    _periods,
)
from tests.test_co_core_routes import _auth
from u8co_api.co_mgmt_core import madd, masked_union, mdec

_MASKED = ["iPrice", "iUnitCost"]
_DOC = {
    "ok": True,
    "type": "sale_out",
    "id": 7,
    "code": "0000000007",
    "head": {"cCode": "0000000007", "cWhCode": "01", "iPrice": None},
    "lines": [{"cInvCode": "A001", "iQuantity": "2", "iUnitCost": None, "iPrice": None}],
    "state": {"verified": True, "verifier": "张三", "verified_at": "2026-09-27"},
    "masked_fields": _MASKED,
}


class MaskedBridge(FakeBridge):
    """单据读取、批量读取、列表按遮蔽后的样子应答（与桥 PermMask 的输出同形）。"""

    def call(self, path: str, payload: dict) -> dict:
        if path == "/v1/vouchers/load":
            self.calls.append((path, dict(payload)))
            return copy.deepcopy(_DOC)
        if path == "/v1/vouchers/load_many":
            self.calls.append((path, dict(payload)))
            other = dict(copy.deepcopy(_DOC), id=8, code="0000000008", masked_fields=["iUnitCost"])
            missing = {"id": 9, "error": {"code": "not_found", "message": "单据不存在"}}
            items = [copy.deepcopy(_DOC), other, missing]
            return {"ok": True, "type": "sale_out", "items": items, "masked_fields": _MASKED}
        if path == "/v1/vouchers/list":
            self.calls.append((path, dict(payload)))
            row = {"id": 7, "code": "0000000007", "doc_date": "2026-09-27", "amount": None, "quantity": "2"}
            return {"ok": True, "type": "purchase_settle", "items": [row], "masked_fields": ["amount"]}
        return super().call(path, payload)


def test_load_passes_masked_nulls_and_masked_fields() -> None:
    reply = _client(MaskedBridge()).post("/v1/co/vouchers/load", json=_auth(type="sale_out", id=7))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert data["head"]["iPrice"] is None and data["head"]["cWhCode"] == "01"
    assert data["lines"][0]["iUnitCost"] is None and data["lines"][0]["iQuantity"] == "2"
    assert data["masked_fields"] == _MASKED


def test_load_compact_drops_masked_values_but_keeps_the_names() -> None:
    reply = _client(MaskedBridge()).post("/v1/co/vouchers/load?compact=true", json=_auth(type="sale_out", id=7))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert "iPrice" not in data["head"] and "iUnitCost" not in data["lines"][0]
    assert data["masked_fields"] == _MASKED


def test_load_many_keeps_item_masked_fields_and_the_union() -> None:
    client = _client(MaskedBridge())
    reply = client.post("/v1/co/vouchers/load_many", json=_auth(type="sale_out", ids=[7, 8, 9]))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    first, second, missing = data["items"]
    assert first["masked_fields"] == _MASKED and first["lines"][0]["iPrice"] is None
    assert second["masked_fields"] == ["iUnitCost"]
    assert "masked_fields" not in missing
    assert data["masked_fields"] == _MASKED
    # 投影只留 code 时，各项的 masked_fields 仍保留。
    projected = client.post("/v1/co/vouchers/load_many?fields=code", json=_auth(type="sale_out", ids=[7, 8, 9]))
    assert projected.status_code == 200, projected.text
    items = projected.json()["items"]
    assert items[0] == {"id": 7, "code": "0000000007", "ok": True, "masked_fields": _MASKED}


def test_list_passes_masked_amounts() -> None:
    reply = _client(MaskedBridge()).post("/v1/co/vouchers/list", json=_auth(type="purchase_settle"))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert data["items"][0]["amount"] is None and data["items"][0]["quantity"] == "2"
    assert data["masked_fields"] == ["amount"]


# ---------- 经营管理 ----------


def test_mdec_keeps_masked_nulls_and_madd_propagates_them() -> None:
    assert mdec({"cogs": None}, "cogs") is None
    assert mdec({}, "cogs") == Decimal(0)
    assert mdec({"cogs": "1.5"}, "cogs") == Decimal("1.5")
    assert madd(Decimal(1), None) is None and madd(None, Decimal(1)) is None
    assert madd(Decimal(3), Decimal(1), -1) == Decimal(2)
    assert masked_union([{"masked_fields": ["cogs"]}, {}], "gross") == ["cogs", "gross"]


def _masked_sales() -> dict:
    found = copy.deepcopy(_SALES_802)
    for row in found["rows"]:
        row["cogs"] = None
    found["totals"]["cogs"] = None
    found["masked_fields"] = ["cogs", "gross", "gross_pct"]
    return found


def test_masked_cogs_nulls_consolidated_cogs_and_margin() -> None:
    fake = ReportFake({("801", _B_SALES): _SALES_801, ("802", _B_SALES): _masked_sales()})
    reply = _client_for(fake).post(_SALES, json=_periods(("801", "802")))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    totals = data["consolidated"]["totals"]
    # 收入照常合并（1300 + 500 − 内部 300），成本、毛利、毛利率不拿部分数冒充合计。
    assert totals["revenue"] == 1500
    assert totals["cogs"] is None and totals["gross"] is None and totals["gross_pct"] is None
    assert data["consolidated"]["masked_fields"] == ["cogs", "gross", "gross_pct"]
    assert data["by_account"]["802"]["masked_fields"] == ["cogs", "gross", "gross_pct"]
    assert data["by_account"]["802"]["totals"]["cogs"] is None
    assert "masked_fields" not in data["by_account"]["801"]


def test_unmasked_sales_have_no_masked_fields() -> None:
    fake = ReportFake({("801", _B_SALES): _SALES_801, ("802", _B_SALES): _SALES_802})
    data = _client_for(fake).post(_SALES, json=_periods(("801", "802"))).json()
    assert "masked_fields" not in data["consolidated"]
    assert data["consolidated"]["totals"]["gross_pct"] == 30.0


def _mask_cash(found: dict) -> dict:
    out = copy.deepcopy(found)
    out["inventory"]["amount"] = None
    purchases = out["purchases"]
    purchases["total_amount"] = None
    purchases["others"]["amount"] = None
    for row in purchases["items"]:
        row["amount"] = None
    out["masked_fields"] = ["amount", "total_amount"]
    return out


def test_masked_inventory_and_purchases_null_the_consolidated_amounts() -> None:
    fake = _cash_fake()
    fake.data[("802", _B_CASH)] = _mask_cash(fake.data[("802", _B_CASH)])
    reply = _client_for(fake).post(_CASH, json=_periods(("801", "802"), purchase_source="invoice", top=10))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    merged = data["consolidated"]
    assert merged["inventory"]["amount"] is None and merged["inventory"]["qty"] == 20
    assert merged["purchases"]["external_amount"] is None and merged["purchases"]["external_qty"] == 9
    assert merged["cash"]["total"] == 1300
    assert merged["masked_fields"] == ["amount", "external_amount", "total_amount"]
    assert data["by_account"]["802"]["masked_fields"] == ["amount", "total_amount"]
    # 被遮的账套没有 others 时不误报内部供应商。
    assert not any("others" in line for line in data["warnings"])


def test_overview_names_the_masked_kpis() -> None:
    fake = _overview_fake()
    fake.data[("802", _B_CASH)] = _mask_cash(fake.data[("802", _B_CASH)])
    reply = _client_for(fake).post(_OVERVIEW, json=_periods(("801", "802"), as_of="2026-09-30"))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    masked = data["by_account"]["802"]
    assert masked["kpis"]["inventory_value"] is None and masked["masked_fields"] == ["inventory_value"]
    assert data["by_account"]["801"]["kpis"]["inventory_value"] == 700
    assert "masked_fields" not in data["by_account"]["801"]
    total = data["consolidated"]
    assert total["kpis"]["inventory_value"] is None and total["masked_fields"] == ["inventory_value"]
    assert total["kpis"]["cash"] == 1300
