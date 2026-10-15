"""经营管理报表的角色化权限：销售分析、往来账期不依赖总账（meta 因 no_permission 失败的账套照样出报表）、
全部账套功能权限不足时 403 no_permission（不是 503）、资金存货的 sections（只要产量不读 meta、不要总账权限）。
桥用按账套应答的假桥，不访问任何网络。"""

from __future__ import annotations

import pytest
from tests.test_co_mgmt_reports import (
    _ARAP,
    _B_ARAP,
    _B_CASH,
    _B_META,
    _B_PNL,
    _B_SALES,
    _CASH,
    _OVERVIEW,
    _SALES,
    _SALES_801,
    _SALES_802,
    ReportFake,
    _arap_fake,
    _body,
    _client_for,
    _periods,
)
from tests.test_co_mgmt import _META, _PNL, MgmtFake, _mgmt_client, _pnl
from tests.test_co_mgmt import _B_META as _PNL_B_META
from tests.test_co_mgmt import _B_PNL as _PNL_B_PNL
from u8co_api import co_mgmt_cache
from tests.test_co_mgmt_cash import _cash
from u8co_api.errors import bad_request, forbidden, unavailable

_DENIED = "没有经营管理销售分析权限"


@pytest.fixture(autouse=True)
def _fresh_cache():
    co_mgmt_cache.CACHE.clear()
    yield
    co_mgmt_cache.CACHE.clear()


def _no_permission(message: str = _DENIED):
    return forbidden(message, "no_permission")


def _production(rows: list[dict]) -> dict:
    return {
        "period": 2,
        "sections": ["production"],
        "production": {
            "items": rows,
            "total_qty": sum(row["qty"] for row in rows),
            "count": len(rows),
            "others": {"count": 0, "qty": 0},
        },
    }


# ---------- 销售、往来：meta 没有权限也能出 ----------


def test_sales_runs_without_the_gl_watermark_and_is_not_cached() -> None:
    fake = ReportFake({("801", _B_SALES): _SALES_801})
    fake.fail[("801", _B_META)] = _no_permission("没有经营管理数据水位权限")
    client = _client_for(fake)
    for _ in range(2):
        reply = client.post(_SALES, json=_periods(("801",)))
        assert reply.status_code == 200, reply.text
        data = reply.json()
        assert data["complete"] is True and data["by_account"]["801"]["ok"] is True
        assert data["by_account"]["801"]["totals"]["revenue"] == 1300
        assert data["accounts"][0]["acc"] == "801" and data["accounts"][0]["close"] is None
        assert data["warnings"] == [] and data["cache"]["hit"] is False
    assert len(fake.sent(_B_SALES)) == 2


def test_sales_mixes_an_account_with_gl_and_one_without() -> None:
    fake = ReportFake({("801", _B_SALES): _SALES_801, ("802", _B_SALES): _SALES_802})
    fake.fail[("802", _B_META)] = _no_permission()
    data = _client_for(fake).post(_SALES, json=_periods(("801", "802"), consolidate=False)).json()
    closes = {item["acc"]: item["close"] for item in data["accounts"]}
    assert closes["801"] and closes["802"] is None
    assert data["by_account"]["801"]["ok"] and data["by_account"]["802"]["ok"] and data["complete"] is True


def test_other_meta_failures_keep_the_old_behaviour_for_sales() -> None:
    fake = ReportFake({("801", _B_SALES): _SALES_801, ("802", _B_SALES): _SALES_802})
    fake.fail[("802", _B_META)] = unavailable("CO 桥不可达")
    data = _client_for(fake).post(_SALES, json=_periods(("801", "802"))).json()
    assert data["by_account"]["802"]["ok"] is False and data["by_account"]["802"]["error"]["code"] == "unavailable"
    assert fake.sent(_B_SALES, "802") == [] and data["complete"] is False and data["consolidated"] is None


def test_sales_without_any_permission_is_403_no_permission() -> None:
    fake = ReportFake()
    for acc in ("801", "802"):
        fake.fail[(acc, _B_META)] = _no_permission("没有经营管理数据水位权限")
        fake.fail[(acc, _B_SALES)] = _no_permission()
    reply = _client_for(fake).post(_SALES, json=_periods(("801", "802")))
    assert reply.status_code == 403 and reply.json()["error"]["code"] == "no_permission"


def test_sales_denied_for_one_account_stays_in_the_body() -> None:
    fake = ReportFake({("801", _B_SALES): _SALES_801})
    for acc in ("801", "802"):
        fake.fail[(acc, _B_META)] = _no_permission()
    fake.fail[("802", _B_SALES)] = _no_permission()
    reply = _client_for(fake).post(_SALES, json=_periods(("801", "802")))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert data["by_account"]["801"]["ok"] is True
    assert data["by_account"]["802"]["ok"] is False and data["by_account"]["802"]["error"]["code"] == "no_permission"
    assert data["complete"] is False and data["consolidated"] is None


def test_arap_runs_without_the_gl_watermark_and_all_denied_is_403() -> None:
    fake = _arap_fake()
    fake.fail[("801", _B_META)] = _no_permission("没有经营管理数据水位权限")
    client = _client_for(fake)
    reply = client.post(_ARAP, json=_periods(("801",), consolidate=False))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert data["by_account"]["801"]["ok"] is True and data["accounts"][0]["close"] is None
    assert [payload["side"] for payload in fake.sent(_B_ARAP)] == ["ar", "ap"]
    fake.fail[("801", _B_ARAP)] = _no_permission("没有经营管理应收账期权限")
    reply = client.post(_ARAP, json=_periods(("801",)))
    assert reply.status_code == 403 and reply.json()["error"]["code"] == "no_permission"


# ---------- 总账口径的报表：全部没有权限 403，部分在正文里 ----------


def test_pnl_all_accounts_denied_is_403_not_503() -> None:
    fake = MgmtFake()
    for acc in ("801", "802"):
        fake.fail[(acc, _PNL_B_META)] = _no_permission("没有经营管理数据水位权限")
    client = _mgmt_client(fake)
    meta_body = {"logins": _pnl(("801", "802"))["logins"], "fiscal_year": 2026}
    for path, body in ((_PNL, _pnl(("801", "802"))), (_META, meta_body)):
        reply = client.post(path, json=body)
        assert reply.status_code == 403 and reply.json()["error"]["code"] == "no_permission", path
    assert fake.count(_PNL_B_PNL) == 0


def test_pnl_partial_denial_stays_in_the_body() -> None:
    fake = MgmtFake()
    fake.fail[("802", _PNL_B_META)] = _no_permission("没有经营管理数据水位权限")
    data = _mgmt_client(fake).post(_PNL, json=_pnl(("801", "802"))).json()
    assert data["by_account"]["802"]["ok"] is False and data["by_account"]["802"]["error"]["code"] == "no_permission"
    assert data["by_account"]["801"]["ok"] is True and data["complete"] is False
    assert fake.count(_PNL_B_PNL, "802") == 0


def test_mixed_failure_codes_stay_503() -> None:
    fake = MgmtFake()
    fake.fail[("801", _PNL_B_META)] = _no_permission("没有经营管理数据水位权限")
    fake.fail[("802", _PNL_B_META)] = unavailable("CO 桥不可达")
    assert _mgmt_client(fake).post(_PNL, json=_pnl(("801", "802"))).status_code == 503


def test_overview_is_403_only_when_every_part_is_denied() -> None:
    fake = ReportFake()
    for path in (_B_META, _B_PNL, _B_CASH, _B_ARAP):
        fake.fail[("801", path)] = _no_permission()
    assert _client_for(fake).post(_OVERVIEW, json=_periods(("801",))).status_code == 403
    fake.fail[("801", _B_ARAP)] = unavailable("CO 桥不可达")
    assert _client_for(fake).post(_OVERVIEW, json=_periods(("801",))).status_code == 503


def test_overview_keeps_readable_parts_and_gives_a_reason_per_missing_part() -> None:
    fake = _arap_fake()
    fake.fail[("801", _B_META)] = _no_permission("没有经营管理数据水位权限")
    fake.fail[("801", _B_PNL)] = _no_permission()
    fake.fail[("801", _B_CASH)] = unavailable("CO 桥不可达")
    reply = _client_for(fake).post(_OVERVIEW, json=_periods(("801",), consolidate=False))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    found = data["by_account"]["801"]
    assert found["missing"] == ["利润表", "资金与存货"]
    assert found["missing_reasons"] == {"利润表": "no_permission", "资金与存货": "unavailable"}
    assert found["kpis"]["ar_balance"] is not None and found["kpis"]["revenue"] is None and data["complete"] is False


def test_overview_names_the_side_that_is_denied() -> None:
    def sides(payload: dict) -> dict:
        if payload["side"] == "ap":
            raise _no_permission("没有经营管理应付账期权限")
        return _arap_fake().data[("801", _B_ARAP)](payload)

    fake = _arap_fake()
    fake.data[("801", _B_ARAP)] = sides
    for path in (_B_PNL, _B_CASH):
        fake.fail[("801", path)] = _no_permission()
    data = _client_for(fake).post(_OVERVIEW, json=_periods(("801",), consolidate=False)).json()
    found = data["by_account"]["801"]
    assert found["missing_reasons"]["应付"] == "no_permission" and "应收" not in found["missing_reasons"]
    assert found["kpis"]["ar_balance"] is not None and found["kpis"]["ap_balance"] is None


def test_arap_keeps_the_readable_side_and_is_403_only_when_nothing_is_readable() -> None:
    base = _arap_fake().data[("801", _B_ARAP)]

    def sides(payload: dict) -> dict:
        if payload["side"] == "ap":
            raise _no_permission("没有经营管理应付账期权限")
        return base(payload)

    fake = _arap_fake()
    fake.data[("801", _B_ARAP)] = sides
    client = _client_for(fake)
    reply = client.post(_ARAP, json=_periods(("801",), consolidate=False))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    found = data["by_account"]["801"]
    assert found["ok"] is True and found["missing_sides"] == {"ap": "no_permission"} and "ar" in found and "ap" not in found
    assert data["complete"] is False and any("应付" in line for line in data["warnings"])
    # 另一个账套两边都能读：它的结果完整。
    both = client.post(_ARAP, json=_periods(("801", "802"), consolidate=False)).json()
    assert both["by_account"]["802"]["ok"] and "missing_sides" not in both["by_account"]["802"]
    fake.fail[("801", _B_ARAP)] = _no_permission()
    assert client.post(_ARAP, json=_periods(("801",))).status_code == 403


# ---------- 资金存货的 sections ----------


def test_production_only_skips_meta_and_sends_sections() -> None:
    fake = ReportFake({("801", _B_CASH): _production([{"code": "A901", "qty": 5}])})
    client = _client_for(fake)
    body = _body(("801",), period_from=2, period_to=2, sections=["production"], class_grade=2)
    for _ in range(2):
        reply = client.post(_CASH, json=body)
        assert reply.status_code == 200, reply.text
        data = reply.json()
        assert data["complete"] is True and data["cache"]["hit"] is False and data["warnings"] == []
        assert data["by_account"]["801"]["production"]["total_qty"] == 5
        assert "cash" not in data["by_account"]["801"] and data["accounts"][0]["close"] is None
    assert fake.sent(_B_META) == []
    sent = fake.sent(_B_CASH, "801")[0]
    assert sent["sections"] == ["production"] and sent["class_grade"] == 2 and sent["period"] == 2


def test_production_only_consolidates_only_production() -> None:
    fake = ReportFake(
        {
            ("801", _B_CASH): _production([{"code": "A901", "qty": 5}]),
            ("802", _B_CASH): _production([{"code": "A901", "qty": 3}]),
        }
    )
    data = _client_for(fake).post(_CASH, json=_body(("801", "802"), period_from=2, period_to=2, sections=["production"])).json()
    merged = data["consolidated"]
    assert sorted(merged) == ["production", "unmapped"] and merged["production"]["items"][0]["qty"] == 8
    assert data["notes"] == [] and fake.sent(_B_META) == []


def test_production_only_denied_is_403_and_partial_stays_in_the_body() -> None:
    fake = ReportFake({("801", _B_CASH): _production([{"code": "A901", "qty": 5}])})
    for acc in ("801", "802"):
        fake.fail[(acc, _B_CASH)] = _no_permission("没有经营管理产量查询权限")
    client = _client_for(fake)
    body = _body(("801", "802"), period_from=2, period_to=2, sections=["production"], consolidate=False)
    reply = client.post(_CASH, json=body)
    assert reply.status_code == 403 and reply.json()["error"]["code"] == "no_permission"
    del fake.fail[("801", _B_CASH)]
    data = client.post(_CASH, json=body).json()
    assert data["by_account"]["801"]["ok"] and data["by_account"]["802"]["error"]["code"] == "no_permission"


def test_other_sections_still_need_meta_and_default_sends_no_sections() -> None:
    fake = ReportFake({("801", _B_CASH): _production([])})
    fake.fail[("801", _B_META)] = _no_permission("没有经营管理数据水位权限")
    client = _client_for(fake)
    body = _body(("801",), period_from=2, period_to=2, sections=["production", "cash"])
    assert client.post(_CASH, json=body).status_code == 403
    assert fake.sent(_B_CASH) == []
    del fake.fail[("801", _B_META)]
    assert client.post(_CASH, json=body).status_code == 200
    assert fake.sent(_B_CASH)[0]["sections"] == ["production", "cash"]
    client.post(_CASH, json=_body(("801",), period_from=2, period_to=2))
    assert "sections" not in fake.sent(_B_CASH)[1]


@pytest.mark.parametrize("sections", [[], ["gl"], ["cash", "cash"], "production", [1]])
def test_bad_sections_are_400(sections: object) -> None:
    fake = ReportFake()
    reply = _client_for(fake).post(_CASH, json=_body(("801",), period_from=2, period_to=2, sections=sections))
    assert reply.status_code == 400, reply.text
    assert fake.calls == []


def test_old_bridge_without_sections_is_retried_and_filtered() -> None:
    def reply(payload: dict) -> dict:
        if "sections" in payload:
            raise bad_request("含未知字段", "bad_request")
        return _cash(1000, 700, [{"code": "A901", "qty": 5}], [{"code": "S900021", "amount": 100, "qty": 1}])

    fake = ReportFake({("801", _B_CASH): reply})
    client = _client_for(fake)
    body = _body(("801",), period_from=2, period_to=2, sections=["production"])
    data = client.post(_CASH, json=body).json()
    found = data["by_account"]["801"]
    assert found["ok"] is True and found["production"]["total_qty"] == 5
    assert not {"cash", "notes_receivable", "inventory", "purchases"} & set(found)
    assert any("sections" in line and "801" in line for line in data["warnings"])
    assert ["sections" in payload for payload in fake.sent(_B_CASH)] == [True, False]


def test_old_bridge_still_refuses_an_operator_without_gl() -> None:
    def reply(payload: dict) -> dict:
        if "sections" in payload:
            raise bad_request("含未知字段", "bad_request")
        raise _no_permission("没有经营管理资金存货查询权限")

    fake = ReportFake({("801", _B_CASH): reply})
    body = _body(("801",), period_from=2, period_to=2, sections=["production"])
    assert _client_for(fake).post(_CASH, json=body).status_code == 403


def test_cache_key_without_sections_has_no_sections_field() -> None:
    from u8co_api.co_models_mgmt_reports import MgmtCashStockIn

    plain = MgmtCashStockIn.model_validate(_body(("801",), period_from=2, period_to=2))
    assert "sections" not in plain.cache_params()
    chosen = MgmtCashStockIn.model_validate(_body(("801",), period_from=2, period_to=2, sections=["production"]))
    assert chosen.cache_params()["sections"] == ["production"]
