"""mgmt/sales 与 mgmt/cash_stock 的存货分类（class_grade、inv_class、inventory_class 维度）、关联方标注（ic_account、ic_totals）
和 stock/current 的 nonzero。桥用按账套应答的假桥，不访问任何网络。"""

from __future__ import annotations

import pytest
from tests.ic_fakes import map_data
from tests.test_co_core_routes import _auth
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_mgmt_reports import (
    _B_CASH,
    _B_SALES,
    _CASH,
    _SALES,
    ReportFake,
    _client_for,
    _fresh_cache,  # noqa: F401  (autouse fixture, reused so the cache is cleared between tests)
    _periods,
    _sales,
    _srow,
)
from u8co_api.co_ic_map import parse_ic_map
from u8co_api.co_mgmt_sales_ic import IC_CUSTOMERS_MAX, group_partners, ic_params, plan_partners
from u8co_api.errors import bad_request

_KEYS = ("qty", "revenue", "revenue_tax_incl", "cost_qty", "cogs")


def _ic(qty: float, revenue: float, cogs: float | None) -> dict:
    return {"qty": qty, "revenue": revenue, "revenue_tax_incl": revenue * 1.13, "cost_qty": qty, "cogs": cogs}


def _class_row(code: str, name: str, revenue: float, cogs: float, qty: float = 1) -> dict:
    return {**_srow("", revenue, cogs, qty), "inventory_class_code": code, "inventory_class_name": name}


def test_class_parameters_are_forwarded_to_the_bridge() -> None:
    row = _class_row("K0101", "类甲细分", 1000, 600, 10)
    fake = ReportFake({("801", _B_SALES): _sales([row])})
    body = _periods(("801",), group_by=["inventory_class"], class_grade=2, inv_class="K01")
    data = _client_for(fake, ic_map=None).post(_SALES, json=body).json()
    (sent,) = fake.sent(_B_SALES)
    assert (sent["group_by"], sent["class_grade"], sent["inv_class"]) == (["inventory_class"], 2, "K01")
    assert data["by_account"]["801"]["rows"][0]["inventory_class_name"] == "类甲细分"


def test_class_parameters_are_left_out_when_not_given() -> None:
    fake = ReportFake({("801", _B_SALES): _sales([_srow("C900021", 100, 60)])})
    _client_for(fake, ic_map=None).post(_SALES, json=_periods(("801",)))
    (sent,) = fake.sent(_B_SALES)
    assert "class_grade" not in sent and "inv_class" not in sent and "ic_customers" not in sent


@pytest.mark.parametrize(
    "change",
    [
        {"class_grade": 0},
        {"class_grade": 10},
        {"class_grade": "2"},
        {"class_grade": True},
        {"inv_class": ""},
        {"inv_class": "  "},
        {"inv_class": 1001},
        {"inv_class": "x" * 61},
        {"group_by": ["inventory_class", "inventory_class"]},
        {"group_by": ["warehouse"]},
    ],
)
def test_bad_class_requests_are_400_before_any_bridge_call(change: dict) -> None:
    fake = ReportFake()
    reply = _client_for(fake).post(_SALES, json=_periods(("801",), **change))
    assert reply.status_code == 400, reply.text
    assert fake.calls == []


def test_class_dimension_combines_with_the_other_dimensions() -> None:
    fake = ReportFake({("801", _B_SALES): _sales([])})
    body = _periods(("801",), group_by=["period", "customer", "inventory", "inventory_class", "person", "department"])
    assert _client_for(fake).post(_SALES, json=body).status_code == 200
    assert fake.sent(_B_SALES)[0]["group_by"][3] == "inventory_class"


def test_customers_of_group_companies_carry_ic_account_in_a_single_set_request() -> None:
    # 只查 802：对照里 802 的客户 C900001 代表 801、C900002 代表 803，与本次是否选了它们无关。
    rows = [_srow("C900022", 500, 450, 5), _srow("C900001", 300, 200, 3), _srow("C900002", 100, 80, 1)]
    ic = _ic(4, 400, 280)
    fake = ReportFake({("802", _B_SALES): {**_sales(rows), "ic_totals": ic}})
    data = _client_for(fake).post(_SALES, json=_periods(("802",))).json()
    (sent,) = fake.sent(_B_SALES)
    assert sent["ic_customers"] == ["C900001", "C900002"]
    marks = {row["customer_code"]: row["ic_account"] for row in data["by_account"]["802"]["rows"]}
    assert marks == {"C900022": None, "C900001": "801", "C900002": "803"}
    assert data["by_account"]["802"]["ic_totals"] == {key: ic[key] for key in _KEYS}


def test_ic_totals_stay_exact_when_the_customers_fall_outside_top() -> None:
    # top 之外的关联方客户并在 others 里，rows 里看不到，ic_totals 由桥的专门一遍给出。
    others = {"qty": 4, "revenue": 400, "revenue_tax_incl": 452, "cost_qty": 4, "cogs": 280}
    reply = {**_sales([_srow("C900022", 500, 450, 5)], others), "ic_totals": _ic(4, 400, 280)}
    fake = ReportFake({("802", _B_SALES): reply})
    data = _client_for(fake).post(_SALES, json=_periods(("802",), top=1, consolidate=False)).json()
    assert data["by_account"]["802"]["ic_totals"]["revenue"] == 400
    assert data["by_account"]["802"]["others"]["revenue"] == 400


def test_masked_ic_measures_stay_null() -> None:
    fake = ReportFake({("802", _B_SALES): {**_sales([_srow("C900022", 500, 450, 5)]), "ic_totals": _ic(4, 400, None)}})
    totals = _client_for(fake).post(_SALES, json=_periods(("802",))).json()["by_account"]["802"]["ic_totals"]
    assert totals["cogs"] is None and totals["revenue"] == 400


def test_rows_without_the_customer_dimension_get_no_ic_account() -> None:
    row = {key: value for key, value in _srow("", 500, 450, 5).items() if not key.startswith("customer")}
    fake = ReportFake({("802", _B_SALES): _sales([{**row, "inventory_code": "A901"}])})
    body = _periods(("802",), group_by=["inventory"])
    data = _client_for(fake).post(_SALES, json=body).json()
    assert "ic_account" not in data["by_account"]["802"]["rows"][0]
    # ic_totals 是客户视角的数：不按客户分组时不请求桥多跑一遍。
    assert "ic_customers" not in fake.sent(_B_SALES)[0]
    assert data["by_account"]["802"]["ic_totals"] is None


def test_ic_fields_are_null_without_an_ic_map() -> None:
    fake = ReportFake({("801", _B_SALES): _sales([_srow("C900001", 300, 200, 3)])})
    data = _client_for(fake, ic_map=None).post(_SALES, json=_periods(("801",))).json()
    assert data["by_account"]["801"]["ic_totals"] is None
    assert data["by_account"]["801"]["rows"][0]["ic_account"] is None
    assert "ic_customers" not in fake.sent(_B_SALES)[0]


def test_bridge_without_ic_totals_gives_null() -> None:
    fake = ReportFake({("802", _B_SALES): _sales([_srow("C900022", 500, 450, 5)])})
    data = _client_for(fake).post(_SALES, json=_periods(("802",))).json()
    assert data["by_account"]["802"]["ic_totals"] is None


def test_multi_set_request_marks_each_set_by_its_own_codes_and_keeps_the_filter_on_the_wide_read() -> None:
    others = {"qty": 1, "revenue": 10, "revenue_tax_incl": 11.3, "cost_qty": 1, "cogs": 8}
    wide = _sales([_srow("C900021", 1000, 600, 10), _srow("C900001", 300, 200, 3)])
    top = {**_sales([_srow("C900021", 1000, 600, 10)], others), "ic_totals": _ic(3, 300, 200)}
    fake = ReportFake(
        {
            ("801", _B_SALES): lambda payload: {**wide, "ic_totals": _ic(3, 300, 200)} if payload["top"] == 500 else top,
            ("802", _B_SALES): {**_sales([_srow("C900022", 500, 450, 5)]), "ic_totals": _ic(0, 0, 0)},
        }
    )
    body = _periods(("801", "802"), inv_class="K01", class_grade=2)
    data = _client_for(fake).post(_SALES, json=body).json()
    assert data["complete"] is True
    # 801 里代表 802 的是 C900001，802 里代表 801 的也是 C900001、代表 803 的是 C900002。
    assert fake.sent(_B_SALES, "801")[0]["ic_customers"] == ["C900001", "C900002"]
    assert data["by_account"]["801"]["ic_totals"]["revenue"] == 300
    assert data["by_account"]["802"]["ic_totals"]["revenue"] == 0
    # 内部销售的补读（按客户取 500 组）带同样的分类过滤，不带 ic_customers。
    wide_read = [p for p in fake.sent(_B_SALES, "801") if p["top"] == 500]
    assert wide_read and all(p["inv_class"] == "K01" and p["class_grade"] == 2 for p in wide_read)
    assert all("ic_customers" not in p for p in wide_read)


def _many_partners_run() -> object:
    class _Group:
        accounts = ("801",)
        as_customer = {"801": {f"{900 + n}": f"C{n:06d}" for n in range(IC_CUSTOMERS_MAX + 5)}}

    class _Run:
        group = _Group()

    return _Run()


def test_more_than_the_bridge_limit_of_partners_is_cut_with_a_warning() -> None:
    run = _many_partners_run()
    partners, warnings = plan_partners(run, ["801"])  # type: ignore[arg-type]
    assert len(partners["801"]) == IC_CUSTOMERS_MAX + 5
    assert len(warnings) == 1 and "801" in warnings[0] and str(IC_CUSTOMERS_MAX) in warnings[0]
    sent = ic_params({"group_by": ["customer"]}, partners["801"])
    assert len(sent["ic_customers"]) == IC_CUSTOMERS_MAX
    assert "ic_customers" not in ic_params({"group_by": ["inventory"]}, partners["801"])
    assert group_partners(run, "801") == partners["801"]  # type: ignore[arg-type]


def test_old_bridge_without_ic_customers_is_retried_without_it() -> None:
    def reply(payload: dict) -> dict:
        if "ic_customers" in payload:
            raise bad_request("含未知字段", "bad_request")
        return _sales([_srow("C900001", 300, 200, 3)])

    fake = ReportFake({("802", _B_SALES): reply})
    data = _client_for(fake).post(_SALES, json=_periods(("802",))).json()
    assert data["complete"] is True
    assert data["by_account"]["802"]["ic_totals"] is None
    assert data["by_account"]["802"]["rows"][0]["ic_account"] == "801"
    assert any("ic_customers" in line and "802" in line for line in data["warnings"])
    assert ["ic_customers" in payload for payload in fake.sent(_B_SALES)] == [True, False]


def test_other_bad_requests_are_not_retried() -> None:
    def reply(payload: dict) -> dict:
        raise bad_request("period_from 不能大于 period_to", "bad_request")

    fake = ReportFake({("802", _B_SALES): reply})
    data = _client_for(fake).post(_SALES, json=_periods(("802",)))
    assert data.status_code == 503, data.text
    assert len(fake.sent(_B_SALES)) == 1


def test_sets_from_different_groups_are_marked_each_by_its_own_group() -> None:
    data = map_data()
    data["groups"][1]["as_customer"] = {"998": {"999": "C998"}, "999": {"998": "C999"}}
    fake = ReportFake(
        {
            ("801", _B_SALES): {**_sales([_srow("C900002", 100, 60)]), "ic_totals": _ic(1, 100, 60)},
            ("998", _B_SALES): {**_sales([_srow("C998", 200, 150)]), "ic_totals": _ic(2, 200, 150)},
        }
    )
    client = _client_for(fake, ic_map=parse_ic_map(data), co_accounts=("801", "998"))
    reply = client.post(_SALES, json=_periods(("801", "998"), consolidate=False)).json()
    assert fake.sent(_B_SALES, "801")[0]["ic_customers"] == ["C900001", "C900002"]
    assert fake.sent(_B_SALES, "998")[0]["ic_customers"] == ["C998"]
    assert reply["by_account"]["801"]["rows"][0]["ic_account"] == "803"
    assert reply["by_account"]["998"]["rows"][0]["ic_account"] == "999"
    assert reply["by_account"]["998"]["ic_totals"]["revenue"] == 200


def test_consolidated_inventory_rows_keep_unit_and_class() -> None:
    def row(qty: float) -> dict:
        found = {key: value for key, value in _srow("", 500, 450, qty).items() if not key.startswith("customer")}
        return {**found, "inventory_code": "A901", "inventory_unit": "KG", "inventory_class_code": "K0101"}

    fake = ReportFake({("801", _B_SALES): _sales([row(5)]), ("802", _B_SALES): _sales([row(3)])})
    body = _periods(("801", "802"), group_by=["inventory"])
    merged = _client_for(fake).post(_SALES, json=body).json()["consolidated"]
    (item,) = merged["rows"]
    part = item["by_account"]["802"]
    assert (part["inventory_code"], part["inventory_unit"], part["inventory_class_code"]) == ("A901", "KG", "K0101")


def _cash_body(rows: list[dict]) -> dict:
    total = sum(row["qty"] for row in rows)
    return {
        "period": 2,
        "closed": {"GL": True},
        "cash": {"items": [], "total": 0},
        "notes_receivable": {"items": [], "total": 0, "open": {"count": 0, "amount": 0}},
        "inventory": {"rows": 0, "qty": 0, "amount": 0},
        "production": {"items": rows, "total_qty": total, "others": {}},
        "purchases": {"source": "invoice", "items": [], "total_amount": 0, "total_qty": 0, "others": {}},
    }


def test_cash_stock_forwards_the_class_parameters_and_keeps_item_classes() -> None:
    rows = [{"code": "A901", "qty": 5, "unit": "KG", "class_code": "K01", "class_name": "类甲"}]
    fake = ReportFake({("801", _B_CASH): _cash_body(rows)})
    body = _periods(("801",), class_grade=2, inv_class="K01")
    data = _client_for(fake).post(_CASH, json=body).json()
    (sent,) = fake.sent(_B_CASH)
    assert (sent["class_grade"], sent["inv_class"]) == (2, "K01")
    item = data["by_account"]["801"]["production"]["items"][0]
    assert (item["class_code"], item["class_name"], item["unit"]) == ("K01", "类甲", "KG")


def test_cash_stock_omits_the_class_parameters_by_default() -> None:
    fake = ReportFake({("801", _B_CASH): _cash_body([])})
    _client_for(fake).post(_CASH, json=_periods(("801",)))
    (sent,) = fake.sent(_B_CASH)
    assert "class_grade" not in sent and "inv_class" not in sent


def test_consolidated_production_keeps_each_sets_class() -> None:
    row = {"code": "A901", "qty": 5, "class_code": "K01", "class_name": "类甲"}
    fake = ReportFake({("801", _B_CASH): _cash_body([row]), ("802", _B_CASH): _cash_body([{**row, "qty": 3}])})
    data = _client_for(fake).post(_CASH, json=_periods(("801", "802"))).json()
    (merged,) = data["consolidated"]["production"]["items"]
    assert merged["qty"] == 8
    assert merged["by_account"]["802"]["class_code"] == "K01" and merged["by_account"]["801"]["class_name"] == "类甲"


@pytest.mark.parametrize("change", [{"class_grade": 0}, {"class_grade": 10}, {"inv_class": ""}, {"inv_class": 5}])
def test_bad_cash_class_requests_are_400(change: dict) -> None:
    fake = ReportFake()
    reply = _client_for(fake).post(_CASH, json=_periods(("801",), **change))
    assert reply.status_code == 400, reply.text
    assert fake.calls == []


def test_stock_current_accepts_only_a_boolean_nonzero() -> None:
    client = _client(FakeBridge())
    ok = client.post("/v1/co/stock/current", json=_auth(nonzero=True))
    assert ok.status_code == 200, ok.text
    assert client.post("/v1/co/stock/current", json=_auth(nonzero="yes")).status_code == 400
    assert client.post("/v1/co/stock/current", json=_auth(nonzero=1)).status_code == 400
