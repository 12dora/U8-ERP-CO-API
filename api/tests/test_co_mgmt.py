"""经营管理查询（/v1/co/mgmt/meta、mgmt/pnl）：经营管理权限、账套授权、利润表行、缓存、部分失败、
合并时的内部收入成本抵销（rev_cogs）。桥用按账套应答的假桥，不访问任何网络。"""

from __future__ import annotations

import pytest
from tests.ic_fakes import map_data
from tests.support import audit_line, base_claims, entry
from tests.test_co_gate import _SECRET, _client
from u8co_api import co_mgmt_cache
from u8co_api.co_ic_map import parse_ic_map
from u8co_api.co_mgmt_lines import parse_mgmt_lines
from u8co_api.errors import ApiError, unavailable

_PNL = "/v1/co/mgmt/pnl"
_META = "/v1/co/mgmt/meta"
_B_META = "/v1/reports/mgmt/meta"
_B_PNL = "/v1/reports/mgmt/pnl"
_B_SALES = "/v1/reports/mgmt/sales"
_ACCS = ("801", "802", "803")
_MGMT_ENTRY = entry(mgmt_claim="u8co_mgmt")


def _row(period: int, code: str, debit: float = 0, credit: float = 0, **extra) -> dict:
    return {"period": period, "code": code, "name": f"科目{code}", "debit": debit, "credit": credit, **extra}


_ROWS_801 = [
    _row(1, "6001", credit=1000),
    _row(1, "6401", debit=600),
    _row(1, "6602", debit=100),
    _row(1, "6801", debit=50),
    _row(1, "6901", credit=5),
    _row(2, "6001", credit=500),
    _row(2, "6401", debit=300),
]
_ROWS_802 = [_row(1, "6001", credit=400), _row(1, "6401", debit=250), _row(2, "6001", credit=200)]


def _meta(closed: bool = False, unposted: int = 0, mark: str = "2026-02-28") -> dict:
    periods = [{"period": p, "closed": {"GL": closed, "SA": closed}, "unposted": unposted} for p in range(1, 13)]
    # 桥给不含金额的内容校验和（gl_content_checksum）和操作员权限指纹（perm_fingerprint）；缺一个时缓存只留 60 秒。
    marks = {"gl_max_date": mark, "gl_content_checksum": 7}
    return {"fiscal_year": 2026, "periods": periods, "watermarks": marks, "perm_fingerprint": "fp-demo"}


class MgmtFake:
    """按（账套、桥路由）应答：meta、pnl、sales 各给预置数据；fail 里的（账套、路由）抛出给定错误。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.meta = {acc: _meta() for acc in _ACCS}
        self.pnl = {"801": list(_ROWS_801), "802": list(_ROWS_802), "803": []}
        self.sales: dict[str, list[dict]] = {}
        self.sales_others: dict[str, dict] = {}
        self.fail: dict[tuple[str, str], ApiError] = {}

    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        acc = payload["acc"]
        if (acc, path) in self.fail:
            raise self.fail[(acc, path)]
        if path == _B_META:
            return {"ok": True, **self.meta[acc]}
        if path == _B_PNL:
            return {"ok": True, "items": self.pnl[acc]}
        if path == _B_SALES:
            # 按期间读：只回这一期的行（去掉期间列）；others 只放在首期。
            period = payload["period_from"]
            rows = [
                {key: value for key, value in row.items() if key != "period"}
                for row in self.sales.get(acc, [])
                if row.get("period", period) == period
            ]
            others = self.sales_others.get(acc) if period == 1 else None
            return {"ok": True, "items": rows, "others": others}
        raise AssertionError(path)

    def health(self) -> dict:
        return {"ok": True}

    def count(self, path: str, acc: str | None = None) -> int:
        return len([1 for called, payload in self.calls if called == path and acc in (None, payload["acc"])])


@pytest.fixture(autouse=True)
def _fresh_cache():
    co_mgmt_cache.CACHE.clear()
    yield
    co_mgmt_cache.CACHE.clear()


def _login(acc: str) -> dict:
    return {"acc": acc, "operator": "op001", "password": _SECRET, "date": "2026-09-30"}


def _map(rule: dict | None = None):
    data = map_data()
    if rule is not None:
        data["groups"][0]["elim"].append(rule)
    return parse_ic_map(data)


def _mgmt_client(fake, claims=None, **flags):
    flags.setdefault("co_accounts", _ACCS)
    flags.setdefault("trust", (_MGMT_ENTRY,))
    flags.setdefault("ic_map", _map())
    return _client(fake, claims=claims or base_claims(u8co_mgmt=True), **flags)


def _pnl(accs: tuple[str, ...], **extra) -> dict:
    return {"logins": [_login(acc) for acc in accs], "fiscal_year": 2026, "period_from": 1, "period_to": 2, **extra}


def _lines(data: dict) -> dict:
    return {line["id"]: line for line in data["lines"]}


@pytest.mark.parametrize(
    "claim_kwargs",
    [
        {"u8co_write": True},
        {"u8co_read": True, "u8co_write": True},
        {"u8co_mgmt": "true"},
    ],
)
def test_without_the_mgmt_claim_is_403_before_any_bridge_call(claim_kwargs) -> None:
    # 令牌在用例里现签：收集阶段签的令牌在长时间的全量运行里会过期（401）。
    fake = MgmtFake()
    denied = _mgmt_client(fake, claims=base_claims(**claim_kwargs)).post(_PNL, json=_pnl(("801",)))
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "mgmt_forbidden"
    assert denied.json()["error"]["message"] == "无权查询经营管理数据"
    assert fake.calls == []


def test_an_issuer_without_mgmt_claim_never_grants_mgmt() -> None:
    fake = MgmtFake()
    client = _mgmt_client(fake, claims=base_claims(u8co_mgmt=True, u8co_write=True), trust=(entry(),))
    assert client.post(_PNL, json=_pnl(("801",))).json()["error"]["code"] == "mgmt_forbidden"
    assert fake.calls == []


def test_every_account_must_be_allowed() -> None:
    fake = MgmtFake()
    limited = entry(mgmt_claim="u8co_mgmt", accounts_claim="accs")
    client = _mgmt_client(fake, claims=base_claims(u8co_mgmt=True, accs=["801"]), trust=(limited,))
    denied = client.post(_PNL, json=_pnl(("801", "802")))
    assert denied.status_code == 403 and denied.json()["error"]["code"] == "account_not_allowed"
    assert fake.calls == []


def test_single_account_pnl_lines_ratios_and_unmapped(capsys) -> None:
    fake = MgmtFake()
    reply = _mgmt_client(fake).post(_PNL, json=_pnl(("801",)))
    assert reply.status_code == 200, reply.text
    data = reply.json()
    assert (data["report"], data["complete"], data["consolidated"]) == ("pnl", True, None)
    assert data["accounts"][0]["name"] == "甲公司" and len(data["accounts"][0]["close"]) == 2
    found = data["by_account"]["801"]
    lines = _lines(found)
    assert lines["revenue"]["total"] == 1500 and lines["revenue"]["periods"] == {"1": 1000, "2": 500}
    assert lines["cogs"]["total"] == 900 and lines["admin"]["total"] == 100
    assert lines["gross_profit"]["total"] == 600 and lines["gross_profit"]["kind"] == "derived"
    assert lines["operating_profit"]["total"] == 500 and lines["net_profit"]["total"] == 450
    assert found["ratios"] == {"gross_margin_pct": 40.0, "net_margin_pct": 30.0}
    assert found["unmapped"] == [{"code": "6901", "name": "科目6901", "net": 5.0}]
    assert any("6901" in item for item in data["warnings"])
    (sent,) = [payload for path, payload in fake.calls if path == _B_PNL]
    assert (sent["detail"], sent["include_unposted"], sent["period_to"]) == ("prefix4", False, 2)
    assert "dims" not in sent
    line = audit_line(capsys.readouterr().out, _PNL)
    assert line["accs"] == ["801"] and line["action"].startswith("co:mgmt/pnl#2026-1-2")
    assert line["mgmt"] == {
        "report": "pnl",
        "fiscal_year": 2026,
        "periods": [1, 2],
        "consolidate": False,
        "cache_hit": False,
    }


def test_second_call_is_served_from_cache_until_the_watermark_moves() -> None:
    fake = MgmtFake()
    client = _mgmt_client(fake)
    first = client.post(_PNL, json=_pnl(("801",))).json()
    second = client.post(_PNL, json=_pnl(("801",))).json()
    assert first["cache"] == {"hit": False, "age_s": 0} and second["cache"]["hit"] is True
    assert second["by_account"] == first["by_account"]
    assert fake.count(_B_PNL) == 1 and fake.count(_B_META) == 2
    fake.meta["801"] = _meta(mark="2026-03-01")
    third = client.post(_PNL, json=_pnl(("801",))).json()
    assert third["cache"]["hit"] is False and fake.count(_B_PNL) == 2


def test_cache_key_includes_the_operator_and_parameters() -> None:
    fake = MgmtFake()
    client = _mgmt_client(fake)
    client.post(_PNL, json=_pnl(("801",)))
    other = _pnl(("801",))
    other["logins"][0]["operator"] = "op002"
    assert client.post(_PNL, json=other).json()["cache"]["hit"] is False
    assert client.post(_PNL, json=_pnl(("801",), include_unposted=True)).json()["cache"]["hit"] is False
    assert fake.count(_B_PNL) == 3


def test_ttl_is_long_only_when_every_period_is_closed(monkeypatch) -> None:
    closed, open_ = _meta(closed=True), _meta()
    assert co_mgmt_cache.ttl_for((1, 2), [closed, closed]) == co_mgmt_cache.TTL_CLOSED
    assert co_mgmt_cache.ttl_for((1, 2), [closed, open_]) == co_mgmt_cache.TTL_OPEN
    assert co_mgmt_cache.ttl_for(None, [closed]) == co_mgmt_cache.TTL_OPEN
    now = [1000.0]
    monkeypatch.setattr(co_mgmt_cache, "_clock", lambda: now[0])
    fake = MgmtFake()
    client = _mgmt_client(fake)
    client.post(_PNL, json=_pnl(("801",)))
    now[0] += 59
    assert client.post(_PNL, json=_pnl(("801",))).json()["cache"] == {"hit": True, "age_s": 59}
    now[0] += 2
    assert client.post(_PNL, json=_pnl(("801",))).json()["cache"]["hit"] is False


def test_lru_keeps_at_most_the_configured_entries() -> None:
    cache = co_mgmt_cache.MgmtCache(size=2)
    for key in ("a", "b", "c"):
        cache.put(key, {"key": key}, 60)
    assert len(cache) == 2 and cache.get("a") is None and cache.get("c")[0] == {"key": "c"}


def test_partial_failure_is_200_without_consolidation_and_not_cached() -> None:
    fake = MgmtFake()
    fake.fail[("802", _B_PNL)] = unavailable("CO 桥不可达")
    client = _mgmt_client(fake)
    data = client.post(_PNL, json=_pnl(("801", "802"))).json()
    assert data["complete"] is False and data["consolidated"] is None
    error = {"code": "unavailable", "message": "CO 桥不可达"}
    assert data["by_account"]["802"] == {"ok": False, "status": 503, "error": error}
    assert any("802" in item for item in data["warnings"])
    assert client.post(_PNL, json=_pnl(("801", "802"))).json()["cache"]["hit"] is False


def test_failed_meta_skips_the_account_and_all_failed_is_503() -> None:
    fake = MgmtFake()
    fake.fail[("802", _B_META)] = unavailable("CO 桥不可达")
    client = _mgmt_client(fake)
    data = client.post(_PNL, json=_pnl(("801", "802"))).json()
    assert data["by_account"]["802"]["ok"] is False and data["accounts"][1]["close"] is None
    assert fake.count(_B_PNL, "802") == 0 and data["consolidated"] is None
    fake.fail[("801", _B_META)] = unavailable("CO 桥不可达")
    assert client.post(_PNL, json=_pnl(("801", "802"))).status_code == 503


def test_unposted_vouchers_are_reported() -> None:
    fake = MgmtFake()
    fake.meta["801"] = _meta(unposted=2)
    data = _mgmt_client(fake).post(_PNL, json=_pnl(("801",))).json()
    assert any("4 张未记账凭证" in item for item in data["warnings"])


def test_consolidation_needs_the_ic_map_unless_turned_off() -> None:
    fake = MgmtFake()
    client = _mgmt_client(fake, ic_map=None)
    missing = client.post(_PNL, json=_pnl(("801", "802")))
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "ic_not_configured"
    data = client.post(_PNL, json=_pnl(("801", "802"), consolidate=False)).json()
    assert data["consolidated"] is None and [item["name"] for item in data["accounts"]] == ["801", "802"]


_SALES_RULE = {
    "rule": "rev_cogs",
    "seller": "802",
    "buyer": "801",
    "revenue_source": "sales_to_customer",
    "cost_source": "ia_to_customer",
}


def test_consolidation_eliminates_internal_sales_and_recomputes_ratios() -> None:
    fake = MgmtFake()
    fake.sales["802"] = [
        {"period": 1, "customer_code": "C900001", "revenue": 300, "cogs": 200},
        {"period": 2, "customer_code": "C900001", "revenue": 100, "cogs": 50},
        {"period": 1, "customer_code": "C900011", "revenue": 100, "cogs": 50},
    ]
    client = _mgmt_client(fake, ic_map=_map(_SALES_RULE))
    data = client.post(_PNL, json=_pnl(("801", "802"))).json()
    assert data["complete"] is True
    merged = _lines(data["consolidated"])
    # 收入 1500 + 600 − 400，成本 900 + 250 − 250。
    assert merged["revenue"]["total"] == 1700 and merged["revenue"]["periods"] == {"1": 1100, "2": 600}
    assert merged["cogs"]["total"] == 900 and merged["gross_profit"]["total"] == 800
    assert data["consolidated"]["ratios"]["gross_margin_pct"] == 47.06
    (elim,) = data["eliminations"]
    assert (elim["seller"], elim["buyer"], elim["customer"]) == ("802", "801", "C900001")
    assert (elim["revenue"], elim["cogs"]) == (400, 250)
    assert elim["periods"]["1"] == {"revenue": 300, "cogs": 200}
    assert "未抵销存货中未实现的内部利润" in data["notes"]
    sent = [payload for path, payload in fake.calls if path == _B_SALES]
    assert [(item["acc"], item["period_from"], item["period_to"]) for item in sent] == [("802", 1, 1), ("802", 2, 2)]
    assert all(item["group_by"] == ["customer"] and item["top"] == 500 for item in sent)


def test_whole_line_sources_need_no_sales_call() -> None:
    fake = MgmtFake()
    rule = dict(_SALES_RULE, revenue_source="gl_revenue_all", cost_source="gl_cogs_all")
    data = _mgmt_client(fake, ic_map=_map(rule)).post(_PNL, json=_pnl(("801", "802"))).json()
    merged = _lines(data["consolidated"])
    assert merged["revenue"]["total"] == 1500 and merged["cogs"]["total"] == 900
    assert fake.count(_B_SALES) == 0


def test_failed_period_stops_further_sales_reads() -> None:
    fake = MgmtFake()
    fake.fail[("802", _B_SALES)] = unavailable("CO 桥不可达")
    data = _mgmt_client(fake, ic_map=_map(_SALES_RULE)).post(_PNL, json=_pnl(("801", "802"))).json()
    assert data["consolidated"] is None
    # 首期失败后不再读后面的期间。
    assert fake.count(_B_SALES) == 1


def test_failed_sales_read_withholds_the_consolidation() -> None:
    fake = MgmtFake()
    fake.fail[("802", _B_SALES)] = unavailable("CO 桥不可达")
    data = _mgmt_client(fake, ic_map=_map(_SALES_RULE)).post(_PNL, json=_pnl(("801", "802"))).json()
    assert data["consolidated"] is None and data["complete"] is False
    assert any("销售统计读取失败" in item for item in data["warnings"])


def test_sales_beyond_top_withhold_the_consolidation() -> None:
    fake = MgmtFake()
    fake.sales["802"] = [{"period": 1, "customer_code": "C900001", "revenue": 300, "cogs": 200}]
    fake.sales_others["802"] = {"groups": 3, "qty": 2, "revenue": 50, "cogs": 0}
    data = _mgmt_client(fake, ic_map=_map(_SALES_RULE)).post(_PNL, json=_pnl(("801", "802"))).json()
    assert data["consolidated"] is None and data["complete"] is False
    assert any("并入「其他」" in item for item in data["warnings"])
    assert [payload["top"] for path, payload in fake.calls if path == _B_SALES] == [500, 500]


def test_zero_others_keep_the_consolidation() -> None:
    fake = MgmtFake()
    fake.sales["802"] = [{"period": 1, "customer_code": "C900001", "revenue": 300, "cogs": 200}]
    fake.sales_others["802"] = {"groups": 2, "qty": 0, "revenue": 0, "cogs": 0}
    data = _mgmt_client(fake, ic_map=_map(_SALES_RULE)).post(_PNL, json=_pnl(("801", "802"))).json()
    assert data["complete"] is True and data["consolidated"] is not None


def test_group_without_rev_cogs_says_so() -> None:
    data = _mgmt_client(MgmtFake()).post(_PNL, json=_pnl(("801", "802"))).json()
    assert _lines(data["consolidated"])["revenue"]["total"] == 2100
    assert any("rev_cogs" in item for item in data["notes"]) and data["eliminations"] == []


def test_site_lines_ask_for_leaf_detail_only_where_needed() -> None:
    lines = parse_mgmt_lines(
        {
            "version": 1,
            "lines": [
                {"id": "revenue", "name": "营业收入", "codes": {"*": ["6001"]}, "sign": "income"},
                {"id": "service", "name": "其中：服务收入", "codes": {"801": ["600119"]}, "sign": "income"},
                {"id": "cogs", "name": "营业成本", "codes": {"*": ["6401"]}, "sign": "expense"},
            ],
            "derived": [{"id": "gross_profit", "name": "毛利", "plus": ["revenue"], "minus": ["cogs"]}],
        }
    )
    fake = MgmtFake()
    fake.pnl["801"] = [_row(1, "600111", credit=70), _row(1, "600119", credit=30)]
    data = _mgmt_client(fake, mgmt_lines=lines).post(_PNL, json=_pnl(("801", "802"), consolidate=False)).json()
    sent = {payload["acc"]: payload["detail"] for path, payload in fake.calls if path == _B_PNL}
    assert sent == {"801": "leaf", "802": "prefix4"}
    first = _lines(data["by_account"]["801"])
    assert (first["revenue"]["total"], first["service"]["total"], first["gross_profit"]["total"]) == (100, 30, 100)
    assert data["by_account"]["801"]["ratios"]["net_margin_pct"] is None


def test_dims_are_passed_and_split() -> None:
    fake = MgmtFake()
    fake.pnl["801"] = [_row(1, "6602", debit=60, dept_code="D01"), _row(1, "6602", debit=40, dept_code="D02")]
    data = _mgmt_client(fake).post(_PNL, json=_pnl(("801",), dims=["dept"])).json()
    assert data["by_account"]["801"]["by_dim"]["admin"] == {"D01": 60, "D02": 40}
    (sent,) = [payload for path, payload in fake.calls if path == _B_PNL]
    assert sent["dims"] == ["dept"]


@pytest.mark.parametrize(
    "change",
    [
        {"period_from": 3, "period_to": 2},
        {"logins": [_login("801"), _login("801")]},
        {"logins": [_login(acc) for acc in ("801", "802", "803", "804")]},
        {"dims": ["dept", "item"]},
        {"fiscal_year": 1999},
    ],
)
def test_bad_requests_are_400(change: dict) -> None:
    fake = MgmtFake()
    reply = _mgmt_client(fake).post(_PNL, json={**_pnl(("801",)), **change})
    assert reply.status_code == 400 and fake.calls == []


def test_meta_lists_each_account_without_caching() -> None:
    fake = MgmtFake()
    fake.meta["802"] = _meta(closed=True)
    client = _mgmt_client(fake)
    body = {"logins": [_login("801"), _login("802")], "fiscal_year": 2026}
    data = client.post(_META, json=body).json()
    assert data["report"] == "meta" and data["period_from"] is None and data["consolidated"] is None
    assert data["by_account"]["802"]["periods"][0]["closed"]["GL"] is True
    assert len(data["accounts"][0]["close"]) == 12
    assert client.post(_META, json=body).json()["cache"]["hit"] is False


def test_openapi_marks_mgmt_access() -> None:
    client = _mgmt_client(MgmtFake())
    spec = client.get("/v1/openapi.json").json()
    operation = spec["paths"][_PNL]["post"]
    assert operation["x-u8co-access"] == "mgmt" and "权限：经营管理" in operation["description"]
