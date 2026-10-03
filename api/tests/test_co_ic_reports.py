"""多账套汇总（reports/aggregate）和合并报表（reports/consolidation）：对照合计、部分失败、授权全有或全无、
抵销算法。桥用按账套返回数据的假桥，不访问任何网络。"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from tests.support import audit_line, base_claims, entry
from tests.test_co_gate import _SECRET, _client
from u8co_api.co_ic_consol import _amount
from u8co_api.co_ic_map import load_ic_map
from u8co_api.errors import ApiError, unavailable

_AGG = "/v1/co/reports/aggregate"
_CONSOL = "/v1/co/reports/consolidation"
_STOCK = "/v1/stock/current"
_ARAP = "/v1/reports/arap_balance"
_AGING = "/v1/reports/arap_aging"
_GL = "/v1/reports/gl_balance"
_AUX = "/v1/reports/gl_aux_balance"
_ACCS = ("801", "802", "803", "804")
_MAP = {
    "groups": [
        {
            "id": "grp1",
            "accounts": ["801", "802", "803"],
            "names": {"801": "甲公司", "802": "乙公司", "803": "丙公司"},
            "as_customer": {
                "801": {"802": "C900001", "803": "C900002"},
                "802": {"801": "C900001"},
                "803": {"801": "C900001"},
            },
            "as_vendor": {
                "801": {"802": "S900001", "803": "S900002"},
                "802": {"801": "S900002"},
                "803": {"801": "S900001"},
            },
            "inventory": [
                {"id": "inv1", "codes": {"801": "A901", "802": "A901"}},
                {"id": "lig", "codes": {"801": "A01", "803": "B01"}, "match": "qty_date"},
            ],
            "gl": [
                {"logical": "ic_ar", "codes": {"801": "112201", "802": "112201", "803": "112201"}},
                {"logical": "ic_ap", "codes": {"801": "220201", "802": "220201", "803": "220201"}},
            ],
            "elim": [
                {
                    "rule": "ar_ap",
                    "pairs": [
                        {"ar": ["803", "ic_ar", "C900001"], "ap": ["801", "ic_ap", "S900002"]},
                        {"ar": ["802", "ic_ar", "C900001"], "ap": ["801", "ic_ap", "S900001"]},
                    ],
                }
            ],
        }
    ]
}


class IcFake:
    """按（账套、桥路由）返回预置的 items，按 limit / after 分页；fail 里的账套抛出给定错误。"""

    def __init__(self, data: dict[tuple[str, str], list[dict]], fail: dict[str, ApiError] | None = None) -> None:
        self.data = data
        self.fail = fail or {}
        self.calls: list[tuple[str, dict]] = []

    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        acc = payload["acc"]
        if acc in self.fail:
            raise self.fail[acc]
        rows = [row for row in self.data.get((acc, path), []) if _aux_hit(path, row, payload)]
        start = int(payload.get("after") or 0)
        end = start + payload["limit"]
        return {"ok": True, "items": rows[start:end], "next": str(end) if end < len(rows) else None}

    def health(self) -> dict:
        return {"ok": True}


def _aux_hit(path: str, row: dict, payload: dict) -> bool:
    if path != _AUX:
        return True
    return (
        row.get("dim") == payload["dim"]
        and row.get("dim_code") == payload.get("dim_code")
        and str(row.get("code")).startswith(payload["code_prefix"])
    )


def _login(acc: str) -> dict:
    return {"acc": acc, "operator": "op001", "password": _SECRET, "date": "2026-09-30"}


def _ic_client(fake, tmp_path, claims=None, with_map=True, **flags):
    path = tmp_path / "ic.json"
    path.write_text(json.dumps(_MAP), encoding="utf-8")
    ic_map = load_ic_map(str(path)) if with_map else None
    # 多账套汇总、合并报表要经营管理权限。
    flags.setdefault("trust", (entry(mgmt_claim="u8co_mgmt"),))
    claims = claims or base_claims(u8co_write=True, u8co_mgmt=True)
    return _client(fake, claims=claims, co_accounts=_ACCS, ic_map=ic_map, **flags)


def _agg(report: str, accs: tuple[str, ...], params: dict | None = None) -> dict:
    return {"report": report, "logins": [_login(acc) for acc in accs], "params": params or {}}


def test_stock_totals_follow_the_inventory_map_and_read_every_page(tmp_path) -> None:
    filler = [{"inv_code": "X1", "qty": 1}] * 500
    fake = IcFake(
        {
            ("801", _STOCK): [
                *filler,
                {"inv_code": "A901", "qty": 10, "qty_available": 8},
                {"inv_code": "A01", "qty": 4.5, "qty_available": 4.5},
            ],
            ("802", _STOCK): [{"inv_code": "A901", "qty": 7, "qty_available": 6}],
            ("803", _STOCK): [{"inv_code": "B01", "qty": 2, "qty_available": 2}, {"inv_code": "A901", "qty": 99}],
        }
    )
    client = _ic_client(fake, tmp_path)
    reply = client.post(_AGG, json=_agg("stock_current", ("801", "802", "803"), {"wh": "01"}))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert data["group"] == "grp1" and data["warnings"] == []
    totals = {row["inventory"]: row for row in data["totals"]}
    assert totals["inv1"]["qty"] == 17 and totals["inv1"]["qty_available"] == 14
    assert totals["inv1"]["by_account"] == {
        "801": {"inv_code": "A901", "qty": 10, "qty_available": 8},
        "802": {"inv_code": "A901", "qty": 7, "qty_available": 6},
    }
    assert totals["lig"]["qty"] == 6.5
    # 803 的 A901 不在对照里（同编码不等于同存货），不相加。
    unmapped = {item["acc"]: item for item in data["unmapped"]}
    assert unmapped["801"]["codes"] == ["X1"] and unmapped["803"]["codes"] == ["A901"]
    assert len(data["by_account"]["801"]["items"]) == 502
    pages = [payload for path, payload in fake.calls if payload["acc"] == "801"]
    assert [page.get("after") for page in pages] == [None, "500"]
    assert all(page["wh"] == "01" and page["limit"] == 500 for page in pages)


def test_arap_totals_by_company_with_a_failed_account(tmp_path, capsys) -> None:
    fake = IcFake(
        {
            ("801", _ARAP): [
                {"partner": "S900001", "debit": 0, "credit": 100, "balance": 100},
                {"partner": "S900002", "debit": 10, "credit": 60, "balance": 50},
                {"partner": "S000009", "debit": 0, "credit": 30.25, "balance": 30.25},
            ],
            ("802", _ARAP): [{"partner": "S900002", "debit": 0, "credit": 40, "balance": 40}],
        },
        fail={"803": unavailable("CO 桥不可达")},
    )
    client = _ic_client(fake, tmp_path)
    reply = client.post(_AGG, json=_agg("arap_balance", ("801", "802", "803"), {"side": "ap"}))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    totals = {row["company"]: row for row in data["totals"]}
    assert totals["802"]["balance"] == 100 and totals["802"]["name"] == "乙公司"
    # 801 欠丙公司 50，802 欠甲公司 40。
    assert totals["803"]["balance"] == 50 and totals["803"]["debit"] == 10
    assert totals["801"]["by_account"] == {"802": {"partner": "S900002", "debit": 0, "credit": 40, "balance": 40}}
    assert data["unmapped"] == [{"acc": "801", "kind": "partner", "count": 1, "codes": ["S000009"], "balance": 30.25}]
    assert data["by_account"]["803"] == {
        "ok": False,
        "status": 503,
        "error": {"code": "unavailable", "message": "CO 桥不可达"},
    }
    assert any("803" in line for line in data["warnings"])
    line = audit_line(capsys.readouterr().out, _AGG)
    assert line["accs"] == ["801", "802", "803"]
    assert line["action"] == "co:reports/aggregate#arap_balance:801=ok,802=ok,803=unavailable"


def test_aging_buckets_are_summed_per_company(tmp_path) -> None:
    row = {"partner": "C900001", "balance": 90, "prepaid": 10, "aging": [50, 30, 20]}
    fake = IcFake({("802", _AGING): [row], ("803", _AGING): [dict(row, aging=[1, 2, 97], balance=100, prepaid=0)]})
    client = _ic_client(fake, tmp_path)
    reply = client.post(_AGG, json=_agg("arap_aging", ("802", "803"), {"side": "ar", "buckets": [30, 60]}))
    assert reply.status_code == 200, reply.text
    (total,) = reply.json()["totals"]
    assert total["company"] == "801" and total["aging"] == [51, 32, 117]
    assert total["balance"] == 190 and total["prepaid"] == 10


def test_aging_grouped_by_person_is_not_totalled(tmp_path) -> None:
    fake = IcFake({("802", _AGING): [{"person_code": "P1", "balance": 5, "aging": [5]}]})
    client = _ic_client(fake, tmp_path)
    reply = client.post(_AGG, json=_agg("arap_aging", ("802",), {"side": "ar", "group_by": "person"}))
    assert reply.status_code == 200, reply.text
    assert reply.json()["totals"] == [] and "group_by=person" in reply.json()["warnings"][0]


def test_gl_totals_net_the_balances_before_splitting(tmp_path) -> None:
    fake = IcFake(
        {
            ("801", _GL): [
                {"code": "112201", "leaf": True, "close_debit": 100, "close_credit": 0, "period_debit": 120},
                {"code": "1001", "leaf": True, "close_debit": 5},
                {"code": "10", "leaf": False, "close_debit": 5},
            ],
            ("802", _GL): [{"code": "112201", "leaf": True, "close_debit": 0, "close_credit": 30, "period_credit": 30}],
        }
    )
    client = _ic_client(fake, tmp_path)
    reply = client.post(_AGG, json=_agg("gl_balance", ("801", "802"), {"period_from": 1, "period_to": 8}))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    (total,) = data["totals"]
    assert total["logical"] == "ic_ar"
    assert (total["close_dir"], total["close_debit"], total["close_credit"]) == ("借", 70, 0)
    assert (total["period_debit"], total["period_credit"]) == (120, 30)
    assert total["by_account"]["802"]["close_net"] == -30
    assert data["unmapped"] == [{"acc": "801", "kind": "subject", "count": 1, "codes": ["1001"]}]
    assert fake.calls[0][1]["period_from"] == 1 and "after" not in fake.calls[0][1]


def test_all_accounts_failed_is_503(tmp_path) -> None:
    fake = IcFake({}, fail={"801": unavailable("CO 桥不可达"), "802": unavailable("CO 桥不可达")})
    client = _ic_client(fake, tmp_path)
    reply = client.post(_AGG, json=_agg("stock_current", ("801", "802")))
    assert reply.status_code == 503
    assert "801=unavailable" in reply.json()["error"]["message"]


def test_token_must_cover_every_account(tmp_path) -> None:
    fake = IcFake({})
    trust = (entry(accounts_claim="u8co_accounts", mgmt_claim="u8co_mgmt"),)
    claims = base_claims(u8co_read=True, u8co_mgmt=True, u8co_accounts=["801", "802"])
    client = _ic_client(fake, tmp_path, claims=claims, trust=trust)
    denied = client.post(_AGG, json=_agg("stock_current", ("801", "803")))
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "account_not_allowed"
    denied = client.post(_CONSOL, json=_consol(("801", "803")))
    assert denied.status_code == 403
    assert fake.calls == []
    assert client.post(_AGG, json=_agg("stock_current", ("801", "802"))).status_code == 200


def test_unconfigured_map_and_group_mismatch(tmp_path) -> None:
    fake = IcFake({})
    missing = _ic_client(fake, tmp_path, with_map=False).post(_AGG, json=_agg("stock_current", ("801",)))
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "ic_not_configured"
    mismatch = _ic_client(fake, tmp_path).post(_AGG, json=_agg("stock_current", ("801", "804")))
    assert mismatch.status_code == 400 and mismatch.json()["error"]["code"] == "ic_group_mismatch"
    assert fake.calls == []


@pytest.mark.parametrize(
    ("report", "params", "field"),
    [
        ("stock_current", {"acc": "802"}, "params.acc"),
        ("stock_current", {"after": 3}, "params.after"),
        ("stock_current", {"nope": 1}, "params.nope"),
        ("gl_balance", {"period_to": 3}, "params.period_from"),
        ("arap_balance", {}, "params.side"),
    ],
)
def test_params_are_checked_against_the_inner_report(tmp_path, report: str, params: dict, field: str) -> None:
    fake = IcFake({})
    reply = _ic_client(fake, tmp_path).post(_AGG, json=_agg(report, ("801", "802"), params))
    assert reply.status_code == 400
    assert reply.json()["error"]["field"] == field
    assert fake.calls == []


def test_duplicate_logins_are_rejected(tmp_path) -> None:
    reply = _ic_client(IcFake({}), tmp_path).post(_AGG, json=_agg("stock_current", ("801", "801")))
    assert reply.status_code == 400


def _consol(accs: tuple[str, ...], **extra) -> dict:
    body = {"logins": [_login(acc) for acc in accs], "fiscal_year": 2026, "period_from": 1, "period_to": 8}
    body.update(extra)
    return body


def _consol_data(ar: float = 300, ap: float = 280) -> dict:
    ar_debit, ar_credit = (ar, 0) if ar >= 0 else (0, -ar)
    ap_debit, ap_credit = (0, ap) if ap >= 0 else (-ap, 0)
    return {
        ("801", _GL): [
            {
                "code": "220201",
                "name": "内部往来供应商",
                "leaf": True,
                "close_debit": ap_debit,
                "close_credit": ap_credit,
            },
            {"code": "1001", "leaf": True, "close_debit": ap_credit, "close_credit": ap_debit},
        ],
        ("803", _GL): [
            {"code": "112201", "name": "关联方客户", "leaf": True, "close_debit": ar_debit, "close_credit": ar_credit},
            {"code": "4001", "leaf": True, "close_debit": ar_credit, "close_credit": ar_debit + 1},
        ],
        ("801", _AUX): [
            {
                "code": "220201",
                "dim": "vendor",
                "dim_code": "S900002",
                "close_debit": ap_debit,
                "close_credit": ap_credit,
            }
        ],
        ("803", _AUX): [
            {
                "code": "112201",
                "dim": "customer",
                "dim_code": "C900001",
                "close_debit": ar_debit,
                "close_credit": ar_credit,
            }
        ],
    }


def test_consolidation_eliminates_the_smaller_balance(tmp_path) -> None:
    fake = IcFake(_consol_data())
    client = _ic_client(fake, tmp_path, claims=base_claims(u8co_read=True, u8co_mgmt=True))
    reply = client.post(_CONSOL, json=_consol(("801", "803")))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert data["complete"] is True and data["warnings"] == []
    (elim,) = data["eliminations"]
    assert elim["ar"]["balance"] == 300 and elim["ap"]["balance"] == 280
    assert (elim["amount"], elim["difference"]) == (280, 20)
    assert elim["entries"] == [
        {"logical": "ic_ap", "debit": 280, "credit": 0},
        {"logical": "ic_ar", "debit": 0, "credit": 280},
    ]
    rows = {row["logical"]: row for row in data["consolidated"]}
    assert rows["ic_ar"]["close_net"] == 300 and rows["ic_ar"]["elim_credit"] == 280
    assert (rows["ic_ar"]["close_dir"], rows["ic_ar"]["close_debit"]) == ("借", 20)
    assert (rows["ic_ap"]["close_dir"], rows["ic_ap"]["after_net"]) == ("平", 0)
    trial = {item["acc"]: item for item in data["trial"]}
    assert trial["801"]["balanced"] is True and trial["803"]["balanced"] is False
    assert {item["acc"]: item["codes"] for item in data["unmapped"]} == {"801": ["1001"], "803": ["4001"]}
    assert any("未实现内部利润" in note for note in data["notes"])
    aux = [payload for path, payload in fake.calls if path == _AUX]
    assert sorted((p["acc"], p["dim"], p["code_prefix"], p["dim_code"]) for p in aux) == [
        ("801", "vendor", "220201", "S900002"),
        ("803", "customer", "112201", "C900001"),
    ]
    gl = [payload for path, payload in fake.calls if path == _GL]
    assert all(p["fiscal_year"] == 2026 and p["nonzero"] is True for p in gl)


def test_opposite_signs_are_not_eliminated(tmp_path) -> None:
    client = _ic_client(IcFake(_consol_data(ar=300, ap=-50)), tmp_path)
    data = client.post(_CONSOL, json=_consol(("801", "803"))).json()
    (elim,) = data["eliminations"]
    assert elim["skipped"] == "opposite_sign" and elim["amount"] == 0 and elim["entries"] == []
    assert any("方向相反" in line for line in data["warnings"])


def test_consolidation_with_a_failed_account_skips_its_pairs(tmp_path) -> None:
    fake = IcFake(_consol_data(), fail={"803": unavailable("CO 桥不可达")})
    data = _ic_client(fake, tmp_path).post(_CONSOL, json=_consol(("801", "803"))).json()
    assert data["complete"] is False and data["eliminations"] == []
    assert data["consolidated"] is None
    assert data["by_account"]["803"]["ok"] is False
    assert any("未抵销" in line for line in data["warnings"])


def test_consolidation_checks_the_request(tmp_path) -> None:
    client = _ic_client(IcFake({}), tmp_path)
    assert client.post(_CONSOL, json=_consol(("801",))).status_code == 400
    assert client.post(_CONSOL, json=_consol(("801", "803"), period_from=9)).status_code == 400
    assert client.post(_CONSOL, json=_consol(("801", "803"), fiscal_year="2026")).status_code == 400


@pytest.mark.parametrize(
    ("ar", "ap", "amount"),
    [("300", "280", "280"), ("100", "250", "100"), ("-40", "-60", "-40"), ("0", "50", "0"), ("10", "-5", None)],
)
def test_elimination_amount(ar: str, ap: str, amount: str | None) -> None:
    expected = None if amount is None else Decimal(amount)
    assert _amount(Decimal(ar), Decimal(ap)) == expected
