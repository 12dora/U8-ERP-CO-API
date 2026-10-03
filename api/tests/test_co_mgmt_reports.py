"""经营管理查询 mgmt/sales、mgmt/arap、mgmt/cash_stock、mgmt/overview：按账套结果、合并时的公司间抵销
（内部客户、内部往来、内部供应商）、按对照的存货相加、部分失败。桥用按账套应答的假桥，不访问任何网络。"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from tests.ic_fakes import map_data
from tests.support import base_claims, entry
from tests.test_co_gate import _SECRET, _client
from u8co_api import co_mgmt_cache
from u8co_api.co_ic_map import parse_ic_map
from u8co_api.co_mgmt_arap import NOTE_NEGATIVE, history_days, net_aging, turnover_days
from u8co_api.errors import ApiError, unavailable

_SALES = "/v1/co/mgmt/sales"
_ARAP = "/v1/co/mgmt/arap"
_CASH = "/v1/co/mgmt/cash_stock"
_OVERVIEW = "/v1/co/mgmt/overview"
_B_META = "/v1/reports/mgmt/meta"
_B_PNL = "/v1/reports/mgmt/pnl"
_B_SALES = "/v1/reports/mgmt/sales"
_B_ARAP = "/v1/reports/mgmt/arap_terms"
_B_CASH = "/v1/reports/mgmt/cash_stock"
_ACCS = ("801", "802", "803")

Reply = dict | Callable[[dict], dict]


def _meta(closed: bool = False) -> dict:
    periods = [{"period": p, "closed": {"GL": closed}, "unposted": 0} for p in range(1, 13)]
    return {"fiscal_year": 2026, "periods": periods, "watermarks": {"gl_max_date": "2026-02-28"}}


class ReportFake:
    """按（账套、桥路由）应答预置的响应体（或按请求体算出的响应体）；fail 里的（账套、路由）抛出给定错误。"""

    def __init__(self, data: dict[tuple[str, str], Reply] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.data: dict[tuple[str, str], Reply] = dict(data or {})
        self.fail: dict[tuple[str, str], ApiError] = {}

    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        acc = payload["acc"]
        if (acc, path) in self.fail:
            raise self.fail[(acc, path)]
        if path == _B_META:
            return {"ok": True, **_meta()}
        found = self.data.get((acc, path))
        if found is None:
            raise AssertionError((acc, path))
        body = found(payload) if callable(found) else found
        return {"ok": True, **body}

    def health(self) -> dict:
        return {"ok": True}

    def sent(self, path: str, acc: str | None = None) -> list[dict]:
        return [payload for called, payload in self.calls if called == path and acc in (None, payload["acc"])]


@pytest.fixture(autouse=True)
def _fresh_cache():
    co_mgmt_cache.CACHE.clear()
    yield
    co_mgmt_cache.CACHE.clear()


def _client_for(fake, claims=None, **flags):
    flags.setdefault("co_accounts", _ACCS)
    flags.setdefault("trust", (entry(mgmt_claim="u8co_mgmt"),))
    flags.setdefault("ic_map", parse_ic_map(map_data()))
    return _client(fake, claims=claims or base_claims(u8co_mgmt=True), **flags)


def _login(acc: str) -> dict:
    return {"acc": acc, "operator": "op001", "password": _SECRET, "date": "2026-09-30"}


def _body(accs: tuple[str, ...], **extra) -> dict:
    return {"logins": [_login(acc) for acc in accs], "fiscal_year": 2026, **extra}


def _periods(accs: tuple[str, ...], **extra) -> dict:
    return _body(accs, period_from=1, period_to=2, **extra)


# ---------- 销售 ----------


def _srow(customer: str, revenue: float, cogs: float, qty: float = 1, **extra) -> dict:
    return {
        "customer_code": customer,
        "customer_name": f"客户{customer}",
        "qty": qty,
        "revenue": revenue,
        "revenue_tax_incl": revenue * 1.13,
        "cost_qty": qty,
        "cogs": cogs,
        **extra,
    }


def _sales(rows: list[dict], others: dict | None = None, enabled: bool = True) -> dict:
    keys = ("qty", "revenue", "revenue_tax_incl", "cost_qty", "cogs")
    totals = {key: sum(row[key] for row in rows) + (others or {}).get(key, 0) for key in keys}
    return {"sa_enabled": enabled, "rows": rows, "others": others, "totals": totals}


_SALES_801 = _sales([_srow("C900021", 1000, 600, 10), _srow("C900001", 300, 200, 3)])
_SALES_802 = _sales([_srow("C900022", 500, 450, 5)])


def test_single_account_sales_passes_the_bridge_result_and_parameters() -> None:
    fake = ReportFake({("801", _B_SALES): _SALES_801})
    body = _periods(("801",), group_by=["period", "customer"], top=50, include_unverified=True)
    reply = _client_for(fake).post(_SALES, json=body)
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert (data["report"], data["complete"], data["consolidated"]) == ("sales", True, None)
    assert data["by_account"]["801"]["totals"]["revenue"] == 1300
    assert len(data["by_account"]["801"]["rows"]) == 2
    (sent,) = fake.sent(_B_SALES)
    assert sent["group_by"] == ["period", "customer"] and sent["top"] == 50
    assert (sent["fiscal_year"], sent["period_from"], sent["period_to"], sent["include_unverified"]) == (
        2026,
        1,
        2,
        True,
    )


def test_sales_without_the_sa_module_warns() -> None:
    fake = ReportFake({("803", _B_SALES): _sales([], enabled=False)})
    data = _client_for(fake).post(_SALES, json=_periods(("803",))).json()
    assert data["by_account"]["803"]["sa_enabled"] is False
    assert any("803" in line and "销售管理" in line for line in data["warnings"])


def test_consolidated_sales_drop_internal_customers_and_recompute_margin() -> None:
    fake = ReportFake({("801", _B_SALES): _SALES_801, ("802", _B_SALES): _SALES_802})
    reply = _client_for(fake).post(_SALES, json=_periods(("801", "802")))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    merged = data["consolidated"]
    # 801 卖给代表乙公司（802）的客户 C900001 的 300 / 200 从合计里去掉。
    assert merged["totals"]["revenue"] == 1500 and merged["totals"]["cogs"] == 1050
    assert merged["totals"]["gross"] == 450 and merged["totals"]["gross_pct"] == 30.0
    assert [(row["acc"], row["customer_code"]) for row in merged["rows"]] == [("801", "C900021"), ("802", "C900022")]
    (elim,) = data["eliminations"]
    assert (elim["rule"], elim["seller"], elim["buyer"], elim["customer"]) == ("ic_sales", "801", "802", "C900001")
    assert elim["revenue"] == 300 and elim["cogs"] == 200 and elim["gross_pct"] == 33.33
    assert len(fake.sent(_B_SALES)) == 2


def test_internal_customer_hidden_in_others_withholds_the_consolidation() -> None:
    others = {"qty": 1, "revenue": 40, "revenue_tax_incl": 45.2, "cost_qty": 1, "cogs": 20}
    fake = ReportFake({("801", _B_SALES): _sales([_srow("C900021", 1000, 600)], others), ("802", _B_SALES): _SALES_802})
    data = _client_for(fake).post(_SALES, json=_periods(("801", "802"))).json()
    assert data["eliminations"] == [] and data["consolidated"] is None and data["complete"] is False
    assert any("C900001" in line and "others" in line for line in data["warnings"])
    # 主查询有 others 时按客户取 500 组补读一次。
    assert [(payload["group_by"], payload["top"]) for payload in fake.sent(_B_SALES, "801")] == [
        (["customer"], 200),
        (["customer"], 500),
    ]
    # 内部客户去不干净的结果不缓存，再查一次仍然访问桥。
    _client_for(fake).post(_SALES, json=_periods(("801", "802")))
    assert len(fake.sent(_B_SALES, "801")) == 4


def test_internal_customer_in_others_is_found_by_the_wide_read() -> None:
    others = {"qty": 3, "revenue": 300, "revenue_tax_incl": 339, "cost_qty": 3, "cogs": 200}
    top = _sales([_srow("C900021", 1000, 600, 10)], others)
    fake = ReportFake({("801", _B_SALES): lambda payload: _SALES_801 if payload["top"] == 500 else top})
    fake.data[("802", _B_SALES)] = _SALES_802
    data = _client_for(fake).post(_SALES, json=_periods(("801", "802"))).json()
    assert data["complete"] is True
    (elim,) = data["eliminations"]
    assert elim["customer"] == "C900001" and elim["revenue"] == 300
    assert data["consolidated"]["totals"]["revenue"] == 1500


def test_mixed_dimensions_take_internal_sales_from_the_customer_read() -> None:
    # 按期间、客户分组时客户可能被拆到多期且被 top 截断，内部销售一律从按客户的补读里取。
    by_period = _sales([_srow("C900021", 1000, 600, 10, period=1), _srow("C900001", 100, 50, 1, period=2)])
    fake = ReportFake(
        {
            ("801", _B_SALES): _by_dims({**by_period, "totals": _SALES_801["totals"]}, _SALES_801),
            ("802", _B_SALES): _by_dims(_SALES_802, _SALES_802),
        }
    )
    data = _client_for(fake).post(_SALES, json=_periods(("801", "802"), group_by=["period", "customer"])).json()
    (elim,) = data["eliminations"]
    assert elim["revenue"] == 300 and data["consolidated"]["totals"]["revenue"] == 1500
    assert [payload["group_by"] for payload in fake.sent(_B_SALES, "801")] == [["period", "customer"], ["customer"]]


def _by_dims(by_inventory: dict, by_customer: dict) -> Callable[[dict], dict]:
    return lambda payload: by_customer if payload["group_by"] == ["customer"] else by_inventory


def test_item_level_consolidation_only_sums_mapped_inventory() -> None:
    inv_801 = _sales(
        [
            {**_srow("", 900, 500, 9), "inventory_code": "A901"},
            {**_srow("", 400, 300, 4), "inventory_code": "X9"},
        ]
    )
    inv_802 = _sales([{**_srow("", 500, 450, 5), "inventory_code": "A901"}])
    fake = ReportFake(
        {
            ("801", _B_SALES): _by_dims(inv_801, _SALES_801),
            ("802", _B_SALES): _by_dims(inv_802, _SALES_802),
        }
    )
    reply = _client_for(fake).post(_SALES, json=_periods(("801", "802"), group_by=["inventory"]))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    merged = data["consolidated"]
    (row,) = merged["rows"]
    assert row["inventory"] == "inv1" and row["revenue"] == 1400 and row["qty"] == 14
    assert row["by_account"]["802"]["inventory_code"] == "A901"
    assert merged["unmapped"] == [{"acc": "801", "kind": "inventory", "count": 1, "codes": ["X9"], "revenue": 400.0}]
    assert merged["totals"]["revenue"] == 1500
    assert any("按存货合并" in note for note in data["notes"])
    # 不按客户分组时，每个账套按客户补读一次，用来找内部销售。
    assert [payload["group_by"] for payload in fake.sent(_B_SALES, "801")] == [["inventory"], ["customer"]]


def test_failed_customer_read_withholds_the_consolidation() -> None:
    fake = ReportFake({("801", _B_SALES): _by_dims(_sales([]), _SALES_801), ("802", _B_SALES): _SALES_802})

    def flaky(payload: dict) -> dict:
        if payload["group_by"] == ["customer"]:
            raise unavailable("CO 桥不可达")
        return _SALES_802

    fake.data[("802", _B_SALES)] = flaky
    data = _client_for(fake).post(_SALES, json=_periods(("801", "802"), group_by=["person"])).json()
    assert data["consolidated"] is None and data["complete"] is False
    assert any("按客户的销售统计读取失败" in line for line in data["warnings"])


# ---------- 应收应付 ----------


def _partner(code: str, balance: float, invoice_amount: float, overdue: float = 0) -> dict:
    return {
        "code": code,
        "name": f"单位{code}",
        "balance": balance,
        "prepaid": 0,
        "overdue": overdue,
        "aging": [balance - overdue, overdue, 0, 0, 0, 0],
        "open_notes": 0,
        "invoice_count": 1,
        "invoice_amount": invoice_amount,
        "paid_amount": invoice_amount - balance,
        "unpaid_amount": balance,
        "terms": [],
        "collection_days_avg": 30.0,
        "collection_days_median": 30,
    }


def _terms(side: str, partners: list[dict], others: dict | None = None, enabled: bool = True) -> dict:
    keys = ("balance", "prepaid", "overdue", "open_notes", "invoice_amount", "paid_amount", "unpaid_amount")
    totals: dict = {key: sum(row[key] for row in partners) + (others or {}).get(key, 0) for key in keys}
    totals["aging"] = [sum(row["aging"][i] for row in partners) for i in range(6)]
    return {
        "side": side,
        "enabled": enabled,
        "as_of": "2026-09-30",
        "history_from": "2025-10-01",
        "history_to": "2026-09-30",
        "partners": partners,
        "others": others,
        "totals": totals,
    }


def _side(ar: dict, ap: dict) -> Callable[[dict], dict]:
    return lambda payload: ar if payload["side"] == "ar" else ap


def _arap_fake() -> ReportFake:
    return ReportFake(
        {
            ("801", _B_ARAP): _side(
                _terms("ar", [_partner("C900021", 1000, 7300, 200), _partner("C900001", 500, 3650)]),
                _terms("ap", [_partner("S900021", 300, 3650)]),
            ),
            ("802", _B_ARAP): _side(
                _terms("ar", [_partner("C900022", 365, 3650, 65)]),
                _terms("ap", [_partner("S900002", 480, 3650), _partner("S900022", 100, 3650)]),
            ),
        }
    )


def test_turnover_days_follow_the_history_window() -> None:
    assert history_days({"history_from": "2025-10-01", "history_to": "2026-09-30"}) == 365
    assert history_days({}) == 365
    assert turnover_days(Decimal(1000), Decimal(0), 365) is None


def test_single_account_arap_reads_both_sides_with_dso_and_dpo() -> None:
    fake = _arap_fake()
    body = _periods(("801",), as_of="2026-09-30", buckets=[30, 60], default_credit_days=15, top=20)
    reply = _client_for(fake).post(_ARAP, json=body)
    assert reply.status_code == 200, reply.text
    found = reply.json()["by_account"]["801"]
    # DSO = 1500 × 365 ÷ 10950 = 50；DPO = 300 × 365 ÷ 3650 = 30。
    assert found["ar"]["dso_days"] == 50.0 and found["ap"]["dpo_days"] == 30.0
    sent = fake.sent(_B_ARAP)
    assert [payload["side"] for payload in sent] == ["ar", "ap"]
    assert sent[0]["as_of"] == "2026-09-30" and sent[0]["buckets"] == [30, 60]
    assert sent[0]["default_credit_days"] == 15 and sent[0]["top"] == 20
    assert "fiscal_year" not in sent[0]


def test_consolidated_arap_eliminates_internal_partners_and_recomputes_days() -> None:
    reply = _client_for(_arap_fake()).post(_ARAP, json=_periods(("801", "802")))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    ar = data["consolidated"]["ar"]
    # 801 应收乙公司（C900001）500 抵销；外部应收 1000 + 365，外部单据 7300 + 3650。
    assert ar["totals"]["balance"] == 1365 and ar["totals"]["invoice_amount"] == 10950
    assert ar["totals"]["dso_days"] == 45.5 and ar["totals"]["overdue"] == 265
    assert [(row["acc"], row["code"]) for row in ar["partners"]] == [("801", "C900021"), ("802", "C900022")]
    ap = data["consolidated"]["ap"]
    # 802 应付甲公司（S900002）480 抵销。
    assert ap["totals"]["balance"] == 400 and ap["totals"]["dpo_days"] == 20.0
    rules = {(item["side"], item["acc"], item["company"], item["partner"]) for item in data["eliminations"]}
    assert rules == {("ar", "801", "802", "C900001"), ("ap", "802", "801", "S900002")}


def test_one_failed_side_marks_the_account_failed_and_withholds_consolidation() -> None:
    fake = _arap_fake()
    fake.fail[("802", _B_ARAP)] = unavailable("CO 桥不可达")
    data = _client_for(fake).post(_ARAP, json=_periods(("801", "802"))).json()
    assert data["complete"] is False and data["consolidated"] is None
    assert data["by_account"]["802"]["ok"] is False
    assert data["by_account"]["801"]["ar"]["dso_days"] == 50.0


def test_arap_unused_module_warns() -> None:
    fake = ReportFake({("803", _B_ARAP): _side(_terms("ar", [], enabled=False), _terms("ap", [], enabled=False))})
    data = _client_for(fake).post(_ARAP, json=_periods(("803",), side="ar")).json()
    assert set(data["by_account"]["803"]) == {"ok", "ar"}
    assert data["by_account"]["803"]["ar"]["dso_days"] is None
    assert any("应收款管理" in line for line in data["warnings"])


def _unapplied(code: str, aging: list[float], prepaid: float, invoice_amount: float = 3650) -> dict:
    """有未核销收付款的单位：balance = aging 合计 − prepaid，overdue 是毛额。"""
    row = _partner(code, sum(aging) - prepaid, invoice_amount)
    row.update(aging=aging, prepaid=prepaid, overdue=sum(aging[1:]))
    return row


def _unapplied_fake() -> ReportFake:
    return ReportFake(
        {
            ("801", _B_ARAP): _side(
                _terms(
                    "ar",
                    [
                        _unapplied("C900021", [100, 50, 30, 0, 0, 20], 60),
                        _unapplied("C900001", [0, 0, 0, 0, 0, 40], 10),
                    ],
                ),
                _terms("ap", [_unapplied("S900021", [10, 0, 0, 0, 0, 20], 130)]),
            ),
            ("802", _B_ARAP): _side(
                _terms("ar", [_unapplied("C900022", [0, 25, 0, 0, 0, 5], 10)]),
                _terms("ap", [_partner("S900022", 100, 3650, 40)]),
            ),
        }
    )


def test_net_aging_applies_prepaid_to_the_oldest_buckets_first() -> None:
    aging = [100, 50, 30, 0, 0, 20]
    assert net_aging(aging, Decimal(60)) == [100, 40, 0, 0, 0, 0]
    assert net_aging(aging, Decimal(500)) == [0] * 6
    assert net_aging([10, -5, 20], Decimal(25)) == [5, -5, 0]
    assert net_aging(aging, Decimal(0)) == aging


def test_arap_nets_unapplied_amounts_per_partner_and_in_totals() -> None:
    data = _client_for(_unapplied_fake()).post(_ARAP, json=_periods(("801",))).json()
    ar = data["by_account"]["801"]["ar"]
    first = ar["partners"][0]
    # 部分冲抵：60 先冲最老的 20，再冲 30，余 10 冲 50。
    assert first["aging_net"] == [100, 40, 0, 0, 0, 0] and first["overdue_net"] == 40
    assert first["aging"] == [100, 50, 30, 0, 0, 20] and first["overdue"] == 100
    assert ar["partners"][1]["aging_net"] == [0, 0, 0, 0, 0, 30]
    assert ar["totals"]["aging_net"] == [100, 40, 0, 0, 0, 30] and ar["totals"]["overdue_net"] == 70
    assert ar["totals"]["overdue"] == 140 and ar["totals"]["balance"] == 170
    # 预付大于账龄：全部冲完，逾期净额 0，余额为负不算 DPO。
    ap = data["by_account"]["801"]["ap"]
    assert ap["totals"]["aging_net"] == [0] * 6 and ap["totals"]["overdue_net"] == 0
    assert ap["totals"]["balance"] == -100 and ap["dpo_days"] is None
    assert ar["dso_days"] is not None
    assert NOTE_NEGATIVE in data["notes"]
    assert any("账套 801 有 70.00 元收款未核销" in line for line in data["warnings"])
    assert any("账套 801 有 130.00 元付款未核销" in line for line in data["warnings"])


def test_consolidated_arap_sums_net_aging_without_internal_partners() -> None:
    data = _client_for(_unapplied_fake()).post(_ARAP, json=_periods(("801", "802"))).json()
    ar = data["consolidated"]["ar"]["totals"]
    # 801 的 C900001（乙公司）连同净额一起抵销：外部 [100,40,0,0,0,0] + 802 [0,20,0,0,0,0]。
    assert ar["aging_net"] == [100, 60, 0, 0, 0, 0] and ar["overdue_net"] == 60
    assert ar["overdue"] == 130
    ap = data["consolidated"]["ap"]["totals"]
    # 应付 −100 + 100 = 0：余额不为正，DPO 为 null。
    assert ap["overdue_net"] == 40 and ap["balance"] == 0 and ap["dpo_days"] is None
    elim = next(item for item in data["eliminations"] if item["partner"] == "C900001")
    assert elim["overdue_net"] == 30 and elim["overdue"] == 40


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (_SALES, _periods(("801",))),
        (_ARAP, _periods(("801",))),
        (_CASH, _periods(("801",))),
        (_OVERVIEW, _periods(("801",))),
    ],
)
def test_reports_need_the_mgmt_claim(path: str, body: dict) -> None:
    fake = ReportFake()
    denied = _client_for(fake, claims=base_claims(u8co_read=True, u8co_write=True)).post(path, json=body)
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "mgmt_forbidden"
    assert fake.calls == []


@pytest.mark.parametrize(
    ("path", "body"),
    [
        (_SALES, _periods(("801",), group_by=["customer", "customer"])),
        (_SALES, _periods(("801",), group_by=["warehouse"])),
        (_SALES, _periods(("801",), top=501)),
        (_ARAP, _periods(("801",), buckets=[60, 30])),
        (_ARAP, _periods(("801",), as_of="2026-02-30")),
        (_ARAP, _periods(("801",), side="ar", fiscal="2026")),
        (_CASH, _body(("801",), period_from=1, period_to=13)),
        (_CASH, _body(("801",), period=2, period_to=2, period_from=1)),
        (_OVERVIEW, _periods(("801",), as_of="2026/09/30")),
    ],
)
def test_bad_requests_are_400(path: str, body: dict) -> None:
    fake = ReportFake()
    reply = _client_for(fake).post(path, json=body)
    assert reply.status_code == 400, reply.text
    assert fake.calls == []
