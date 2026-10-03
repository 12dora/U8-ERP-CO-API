"""经营管理 mgmt/cash_stock 与 mgmt/overview：资金存货按账套与合并（内部供应商剔除），总览由利润表、资金存货、往来组成。"""

from __future__ import annotations

from u8co_api.errors import unavailable
from u8co_api.co_mgmt_arap import NOTE_NEGATIVE

from tests.test_co_mgmt_reports import (
    ReportFake,
    _B_ARAP,
    _B_CASH,
    _B_META,
    _B_PNL,
    _CASH,
    _OVERVIEW,
    _arap_fake,
    _body,
    _client_for,
    _periods,
    _unapplied_fake,
)


def _cash(cash: float, inventory: float, production: list[dict], purchases: list[dict], others: float = 0) -> dict:
    return {
        "period": 2,
        "closed": {"GL": True},
        "cash": {"items": [{"code": "100201", "balance": cash}], "total": cash},
        "notes_receivable": {"items": [], "total": 50, "open": {"count": 1, "amount": 40}},
        "inventory": {"rows": 3, "qty": 10, "amount": inventory},
        "production": {"items": production, "total_qty": sum(row["qty"] for row in production), "others": {}},
        "purchases": {
            "source": "invoice",
            "items": purchases,
            "total_amount": sum(row["amount"] for row in purchases) + others,
            "total_qty": sum(row["qty"] for row in purchases),
            "others": {"count": 1 if others else 0, "amount": others},
        },
    }


def _cash_fake() -> ReportFake:
    return ReportFake(
        {
            ("801", _B_CASH): _cash(
                1000,
                700,
                [{"code": "A901", "qty": 5}, {"code": "A902", "qty": 2}],
                [{"code": "S900001", "amount": 200, "qty": 2}, {"code": "S900021", "amount": 100, "qty": 1}],
            ),
            ("802", _B_CASH): _cash(
                300, 200, [{"code": "A901", "qty": 3}], [{"code": "S900022", "amount": 80, "qty": 8}]
            ),
        }
    )


def test_cash_stock_consolidation_adds_up_and_drops_internal_vendors() -> None:
    fake = _cash_fake()
    body = _periods(("801", "802"), purchase_source="invoice", top=10)
    reply = _client_for(fake).post(_CASH, json=body)
    assert reply.status_code == 200, reply.text
    data = reply.json()
    merged = data["consolidated"]
    assert merged["cash"]["total"] == 1300 and merged["cash"]["by_account"] == {"801": 1000, "802": 300}
    assert merged["inventory"]["amount"] == 900 and merged["notes_receivable"]["total"] == 100
    (made,) = merged["production"]["items"]
    assert made["inventory"] == "inv1" and made["qty"] == 8
    assert merged["unmapped"] == [{"acc": "801", "kind": "inventory", "count": 1, "codes": ["A902"]}]
    # 801 向代表乙公司（802）的供应商 S900001 的采购 200 去掉。
    assert merged["purchases"]["external_amount"] == 180
    assert [row["code"] for row in merged["purchases"]["items"]] == ["S900021", "S900022"]
    (elim,) = data["eliminations"]
    assert (elim["rule"], elim["acc"], elim["company"], elim["vendor"], elim["amount"]) == (
        "ic_purchase",
        "801",
        "802",
        "S900001",
        200,
    )
    assert any("未实现的内部利润" in note for note in data["notes"])
    # 期间区间只取 period_to 一期，notes 里说明。
    assert any("第 2 期" in note for note in data["notes"])
    sent = fake.sent(_B_CASH, "801")[0]
    assert (sent["period"], sent["purchase_source"], sent["top"]) == (2, "invoice", 10)


def test_cash_stock_internal_vendor_in_others_is_reported() -> None:
    fake = _cash_fake()
    fake.data[("801", _B_CASH)] = _cash(1, 1, [], [{"code": "S900021", "amount": 100, "qty": 1}], others=60)
    data = _client_for(fake).post(_CASH, json=_body(("801", "802"), period_from=2, period_to=2)).json()
    assert any("S900001" in line and "others" in line for line in data["warnings"])


def test_cash_stock_without_consolidation() -> None:
    body = _body(("801", "802"), period_from=2, period_to=2, consolidate=False)
    data = _client_for(_cash_fake()).post(_CASH, json=body).json()
    assert data["consolidated"] is None and data["eliminations"] == []
    assert data["by_account"]["802"]["cash"]["total"] == 300


# ---------- 关键指标 ----------


def _pnl_rows(revenue: float, cogs: float, admin: float) -> dict:
    rows = [
        {"period": 1, "code": "6001", "debit": 0, "credit": revenue},
        {"period": 1, "code": "6401", "debit": cogs, "credit": 0},
        {"period": 2, "code": "6602", "debit": admin, "credit": 0},
    ]
    return {"items": rows}


def _overview_fake() -> ReportFake:
    fake = _arap_fake()
    fake.data.update(_cash_fake().data)
    fake.data[("801", _B_PNL)] = _pnl_rows(2000, 1200, 300)
    fake.data[("802", _B_PNL)] = _pnl_rows(500, 400, 50)
    return fake


def test_overview_composes_the_reports_per_account_and_consolidated() -> None:
    fake = _overview_fake()
    # 缓存按应用各一份（co_mgmt_cache.cache_of）：两次请求用同一个客户端。
    client = _client_for(fake)
    reply = client.post(_OVERVIEW, json=_periods(("801", "802"), as_of="2026-09-30"))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert data["report"] == "overview" and data["complete"] is True
    kpis = data["by_account"]["801"]["kpis"]
    assert (kpis["revenue"], kpis["gross_profit"], kpis["net_profit"]) == (2000, 800, 500)
    assert kpis["gross_margin_pct"] == 40.0 and kpis["cash"] == 1000 and kpis["inventory_value"] == 700
    assert (kpis["ar_balance"], kpis["ar_overdue"], kpis["ap_balance"]) == (1500, 200, 300)
    assert (kpis["dso_days"], kpis["dpo_days"]) == (50.0, 30.0)
    total = data["consolidated"]["kpis"]
    assert total["revenue"] == 2500 and total["cash"] == 1300 and total["ar_balance"] == 1365
    assert total["dso_days"] == 45.5 and total["ap_balance"] == 400
    assert {item["report"] for item in data["eliminations"]} == {"arap", "cash_stock"}
    assert fake.sent(_B_CASH, "801")[0]["period"] == 2
    assert fake.sent(_B_ARAP, "801")[0]["as_of"] == "2026-09-30"
    # 第二次命中缓存，不再调各报表。
    before = len(fake.calls)
    again = client.post(_OVERVIEW, json=_periods(("801", "802"), as_of="2026-09-30")).json()
    assert again["cache"]["hit"] is True and again["by_account"] == data["by_account"]
    assert [path for path, _ in fake.calls[before:]] == [_B_META, _B_META]


def test_overview_with_one_part_failing_everywhere_keeps_the_rest() -> None:
    fake = _overview_fake()
    fake.fail[("801", _B_CASH)] = unavailable("CO 桥不可达")
    fake.fail[("802", _B_CASH)] = unavailable("CO 桥不可达")
    data = _client_for(fake).post(_OVERVIEW, json=_periods(("801", "802"))).json()
    assert data["complete"] is False and data["consolidated"] is None
    found = data["by_account"]["801"]
    assert found["kpis"]["revenue"] == 2000 and found["kpis"]["cash"] is None
    assert found["missing"] == ["资金与存货"]
    assert any(line.startswith("资金与存货全部账套读取失败") for line in data["warnings"])


# ---------- 权限与参数 ----------


def test_overview_overdue_kpis_use_net_amounts() -> None:
    fake = _unapplied_fake()
    fake.data.update(_cash_fake().data)
    fake.data[("801", _B_PNL)] = _pnl_rows(2000, 1200, 300)
    fake.data[("802", _B_PNL)] = _pnl_rows(500, 400, 50)
    data = _client_for(fake).post(_OVERVIEW, json=_periods(("801", "802"))).json()
    kpis = data["by_account"]["801"]["kpis"]
    assert (kpis["ar_overdue"], kpis["ap_overdue"], kpis["dpo_days"]) == (70, 0, None)
    total = data["consolidated"]["kpis"]
    assert (total["ar_overdue"], total["ap_overdue"]) == (60, 40)
    assert NOTE_NEGATIVE in data["notes"]


# ---------- 资金与存货 ----------
