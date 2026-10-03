"""公司间对账：贪心 1:1 配对的边界、发票按月、截止日期，以及路由经假桥取数的全过程。不访问网络。"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from tests.ic_fakes import IcBridge, ic_client, login, sample_map
from u8co_api.co_ic_greedy import IcLine, clip, left_out, match_lines, match_months
from u8co_api.errors import unavailable

_MATCH = "/v1/co/reports/intercompany_match"
_GROUP = sample_map().groups[0]
_S19A = _GROUP.inventory[0]
_LIG = _GROUP.inventory[1]


def _line(acc: str, day: int, qty: str | None, **extra) -> IcLine:
    doc, amount = extra.get("doc", 1), extra.get("amount")
    return IcLine(
        acc=acc,
        type="sale_out" if acc == "802" else "purchase_in",
        id=doc,
        code=f"D{doc}",
        date=dt.date(2026, 8, day),
        line_id=doc * 10,
        inv_code="X",
        qty=None if qty is None else Decimal(qty),
        amount=None if amount is None else Decimal(amount),
        inv=extra.get("inv", _S19A),
    )


def _s(day: int, qty: str | None, **extra) -> IcLine:
    return _line("802", day, qty, **extra)


def _b(day: int, qty: str | None, **extra) -> IcLine:
    return _line("801", day, qty, **extra)


def test_exact_pairs_are_one_to_one() -> None:
    found = match_lines([_s(3, "10", doc=1), _s(3, "10", doc=2)], [_b(3, "10.004", doc=5)], 0)
    assert [(pair.seller.id, pair.buyer.id, pair.way) for pair in found.pairs] == [(1, 5, "exact")]
    assert [line.id for line in found.seller_left] == [2]
    assert found.buyer_left == []


def test_window_pass_only_inside_the_window() -> None:
    seller, buyer = [_s(1, "10")], [_b(2, "10")]
    assert match_lines(seller, buyer, 0).pairs == []
    pair = match_lines(seller, buyer, 1).pairs[0]
    assert (pair.way, pair.out()["days"]) == ("window", 1)


def test_window_prefers_the_nearest_then_the_earlier_day() -> None:
    found = match_lines([_s(5, "10")], [_b(7, "10", doc=1), _b(6, "10", doc=2), _b(4, "10", doc=3)], 3)
    assert found.pairs[0].buyer.id == 3
    assert [line.id for line in found.buyer_left] == [2, 1]


def test_exact_matches_win_over_window_matches() -> None:
    found = match_lines([_s(1, "10", doc=1), _s(2, "10", doc=2)], [_b(2, "10", doc=7), _b(4, "10", doc=8)], 1)
    assert [(pair.seller.id, pair.buyer.id, pair.way) for pair in found.pairs] == [(2, 7, "exact")]
    assert [line.id for line in found.seller_left] == [1]
    assert [line.id for line in found.buyer_left] == [8]


def test_qty_date_inventory_never_uses_the_window() -> None:
    assert match_lines([_s(1, "10", inv=_LIG)], [_b(2, "10", inv=_LIG)], 7).pairs == []
    assert len(match_lines([_s(2, "10", inv=_LIG)], [_b(2, "10", inv=_LIG)], 0).pairs) == 1


def test_different_inventory_or_missing_qty_never_match() -> None:
    found = match_lines([_s(1, "10", inv=_LIG), _s(1, None)], [_b(1, "10"), _b(1, None)], 3)
    assert found.pairs == []
    assert left_out(_s(1, "10", inv=None))["reason"] == "unmapped"
    assert left_out(_s(1, "10"))["reason"] == "no_counterpart"
    assert left_out(_s(1, "10"))["inv_id"] == "inv1"


def test_clip_is_the_earlier_last_day() -> None:
    assert clip([_s(3, "1"), _s(9, "1")], [_b(5, "1")]) == dt.date(2026, 8, 5)
    assert clip([], [_b(5, "1")]) is None


def test_months_compare_amounts_within_five_cents() -> None:
    seller = [_s(3, "1", doc=1, amount="113.00"), _s(20, "1", doc=2, amount="50.02")]
    buyer = [_b(31, "1", doc=9, amount="163.07")]
    matched, left_s, left_b = match_months(seller, buyer)
    assert matched == [
        {"month": "2026-08", "seller_amount": "163.02", "buyer_amount": "163.07", "diff": "-0.05",
         "seller_docs": 2, "buyer_docs": 1}
    ]
    assert left_s == left_b == []
    _matched, left_s, left_b = match_months(seller, [_b(31, "1", amount="163.08")])
    assert left_s[0]["reason"] == left_b[0]["reason"] == "amount_differs"
    _matched, left_s, left_b = match_months(seller, [])
    assert (left_s[0]["reason"], left_b) == ("no_counterpart", [])


def _body(**extra) -> dict:
    body = {
        "logins": [login("802"), login("801")],
        "seller": {"acc": "802", "type": "sale_out"},
        "buyer": {"acc": "801", "type": "purchase_in"},
        "date_from": "2026-08-01",
        "date_to": "2026-08-31",
    }
    body.update(extra)
    return body


def _goods() -> IcBridge:
    fake = IcBridge()
    fake.heads[("802", "sale_out")] = [
        {"id": 1, "code": "XS1", "doc_date": "2026-08-03"},
        {"id": 2, "code": "XS2", "doc_date": "2026-08-05"},
    ]
    fake.ledger[("802", "A901")] = [
        {"date": "2026-08-03", "type": "sale_out", "id": 1, "line_id": 11, "code": "XS1", "out_qty": "100"},
        {"date": "2026-08-04", "type": "sale_out", "id": 99, "line_id": 991, "code": "XS99", "out_qty": "100"},
        {"date": "2026-08-04", "type": "purchase_in", "id": 1, "line_id": 5, "code": "RK", "in_qty": "100"},
        {"date": "2026-08-05", "type": "sale_out", "id": 2, "line_id": 21, "code": "XS2", "out_qty": "50"},
    ]
    fake.heads[("801", "purchase_in")] = [
        {"id": 7, "code": "RK7", "doc_date": "2026-08-03"},
        {"id": 8, "code": "RK8", "doc_date": "2026-08-04"},
        {"id": 9, "code": "RK9", "doc_date": "2026-08-20"},
    ]
    fake.ledger[("801", "A901")] = [
        {"date": "2026-08-03", "type": "purchase_in", "id": 7, "line_id": 71, "code": "RK7", "in_qty": 100},
        {"date": "2026-08-04", "type": "purchase_in", "id": 8, "line_id": 81, "code": "RK8", "in_qty": 50},
        {"date": "2026-08-20", "type": "purchase_in", "id": 9, "line_id": 91, "code": "RK9", "in_qty": 5},
    ]
    return fake


def test_route_matches_sale_out_with_purchase_in() -> None:
    fake = _goods()
    ok = ic_client(fake).post(_MATCH, json=_body())
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["group"] == "grp1" and data["mode"] == "line"
    assert data["seller"] == {"acc": "802", "type": "sale_out", "partner": "C900001"}
    assert data["buyer"] == {"acc": "801", "type": "purchase_in", "partner": "S900001"}
    assert data["clip_date"] == "2026-08-05"
    assert data["clipped"] == {"seller": 0, "buyer": 1}
    assert [(m["seller"]["code"], m["buyer"]["code"], m["pass"], m["days"]) for m in data["matched"]] == [
        ("XS1", "RK7", "exact", 0),
        ("XS2", "RK8", "window", -1),
    ]
    assert data["matched"][0]["inv_id"] == "inv1" and data["matched"][0]["qty"] == "100"
    assert data["unmatched_seller"] == [] and data["unmatched_buyer"] == []
    assert data["rates"] == {"seller": 1.0, "buyer": 1.0}
    assert "mapped_inventory_only:802" in data["warnings"]
    searches = [payload for path, payload in fake.calls if path == "/v1/vouchers/search"]
    assert {(item["acc"], item["partner"], item["limit"]) for item in searches} == {("802", "C900001", 200), ("801", "S900001", 200)}
    ledgers = sorted((payload["acc"], payload["inv"]) for path, payload in fake.calls if path == "/v1/reports/stock_ledger")
    assert ledgers == [("801", "A0001"), ("801", "A901"), ("802", "A901")]
    assert all(payload["include_unverified"] is True for path, payload in fake.calls if "ledger" in path)


def test_route_with_no_window_leaves_the_late_pair_unmatched() -> None:
    data = ic_client(_goods()).post(_MATCH, json=_body(window_days=0)).json()
    assert len(data["matched"]) == 1
    assert data["unmatched_seller"][0]["code"] == "XS2" and data["unmatched_seller"][0]["reason"] == "no_counterpart"
    assert data["rates"] == {"seller": 0.5, "buyer": 0.5}


def _invoices() -> IcBridge:
    fake = IcBridge()
    fake.heads[("802", "sale_invoice")] = [{"id": n, "code": f"XP{n}", "doc_date": "2026-08-31"} for n in range(1, 8)]
    for n in range(1, 7):
        line = {"cInvCode": "A901", "iQuantity": "1", "iSum": "100.00" if n < 6 else "13.00"}
        fake.docs[("802", n)] = {"ok": True, "id": n, "head": {}, "lines": [line]}
    fake.heads[("801", "purchase_invoice")] = [{"id": 40, "code": "26350001", "doc_date": "2026-08-31"}]
    fake.docs[("801", 40)] = {"ok": True, "id": 40, "head": {}, "lines": [{"cinvcode": "A901", "iquantity": 6, "isum": 513.02}]}
    return fake


def test_route_compares_invoices_by_month() -> None:
    fake = _invoices()
    body = _body(seller={"acc": "802", "type": "sale_invoice"}, buyer={"acc": "801", "type": "purchase_invoice"})
    ok = ic_client(fake).post(_MATCH, json=body)
    assert ok.status_code == 200, ok.text
    data = ok.json()
    assert data["mode"] == "month"
    assert data["matched"][0]["seller_amount"] == "513.00" and data["matched"][0]["seller_docs"] == 6
    assert data["rates"] == {"seller": 1.0, "buyer": 1.0}
    assert "load_failed:802:7" in data["warnings"]
    loads = [(payload["acc"], payload["ids"]) for path, payload in fake.calls if path == "/v1/vouchers/load_many"]
    assert sorted(loads) == [("801", [40]), ("802", [1, 2, 3, 4, 5]), ("802", [6, 7])]


def test_route_compares_invoices_line_by_line_on_request() -> None:
    body = _body(
        seller={"acc": "802", "type": "sale_invoice"}, buyer={"acc": "801", "type": "purchase_invoice"}, invoice_mode="line"
    )
    data = ic_client(_invoices()).post(_MATCH, json=body).json()
    assert data["mode"] == "line"
    assert data["matched"] == []
    assert len(data["unmatched_seller"]) == 6 and data["unmatched_seller"][0]["amount"] == "100"


def test_route_fails_with_the_failing_account() -> None:
    fake = _goods()
    fake.errors["801"] = unavailable("CO 桥不可达")
    denied = ic_client(fake).post(_MATCH, json=_body())
    assert denied.status_code == 503
    assert denied.json()["error"]["code"] == "unavailable"
    assert denied.json()["error"]["message"] == "账套 801：CO 桥不可达"


def test_route_needs_both_party_codes() -> None:
    fake = IcBridge()
    body = _body(
        logins=[login("801"), login("803")], seller={"acc": "801", "type": "sale_out"}, buyer={"acc": "803", "type": "purchase_in"}
    )
    denied = ic_client(fake).post(_MATCH, json=body)
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "ic_party_unmapped"
    assert fake.calls == []


@pytest.mark.parametrize(
    "extra",
    [
        {"date_to": "2026-11-15"},
        {"date_from": "2026-09-01"},
        {"date_to": "2026-02-30"},
        {"window_days": 8},
        {"invoice_mode": "month"},
        {"buyer": {"acc": "801", "type": "purchase_invoice"}},
        {"seller": {"acc": "803", "type": "sale_out"}},
        {"seller": {"acc": "801", "type": "sale_out"}},
        {"logins": [login("802"), login("802")]},
        {"logins": [login("802")]},
        {"buyer": {"acc": "801", "type": "other_in"}},
    ],
)
def test_bad_requests_are_400_before_any_bridge_call(extra: dict) -> None:
    fake = IcBridge()
    denied = ic_client(fake).post(_MATCH, json=_body(**extra))
    assert denied.status_code == 400, denied.text
    assert fake.calls == []
