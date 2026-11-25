"""出入库调整单、存货调价单、固定资产报表只读核对（ia_sa_fa）的登记、分片、标签和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import ia_sa_fa as isf
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.tags import auto_tags
from co.e2e.tests import support

_NAMES = ("ia-adjust-read", "list-inv-price-adjust", "report-fa-changes", "report-fa-depreciation")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
# 登录日期 2026-09-28：缺省年度 2026，上一年度 2025。
_LOGIN_YEAR = 2026


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _ia_row(doc: int, posted: int = 1) -> dict:
    return {"id": doc, "code": f"{doc:010d}", "ufts": str(doc), "vouch_type": "21", "line_count": "1",
            "posted_lines": str(posted), "verified": posted == 1}


class _Replay:
    def __init__(self) -> None:
        self.ia: list[dict] = []
        self.price: list[dict] = []
        self.changes: list[dict] = []
        self.depr: list[dict] = []
        self.posted = [1, 8]
        self.card = {"fiscal_year": _LOGIN_YEAR, "depr_period": 8,
                     "fields": {"accumulated_depreciation": 3000.0, "disposed": False}}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, _env: object, route: str, body: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(body)))
        year = body.get("fiscal_year", _LOGIN_YEAR)
        if route.endswith("/fa_changes"):
            rows = [r for r in self.changes if body.get("card") in (None, r["card_code"]) and body.get("code") in (None, r["code"])]
            return 200, {"ok": True, "items": [r for r in rows if r.get("year", _LOGIN_YEAR) == year]}
        if route.endswith("/fa_depreciation"):
            rows = [r for r in self.depr if body.get("card") in (None, r["card_code"]) and body.get("period") in (None, r["period"])]
            rows = [r for r in rows if r.get("year", _LOGIN_YEAR) == year]
            return 200, {"ok": True, "items": rows, "posted_periods": self.posted}
        if route.endswith("/archives/get"):
            return 200, dict(self.card, ok=True)
        return 200, self._voucher(route, body)

    def _voucher(self, route: str, body: dict) -> dict:
        rows = self.ia if body["type"] == isf.IA else self.price
        if route.endswith("/list"):
            items = [{k: row[k] for k in ("id", "code", "ufts")} for row in rows] if body.get("keys_only") else rows
            return {"ok": True, "items": items}
        if route.endswith("/search"):
            return {"ok": True, "items": [row for row in rows if body["code_like"] in row["code"]]}
        row = next(row for row in rows if row["id"] == body["id"])
        posted = row.get("verified", False)
        return {"ok": True, "code": row["code"], "lines": [{"AutoID": 1}], "state": {"posted": posted, "verified": posted}}


def _run(replay: _Replay, op: str) -> tuple[str, str]:
    env = SimpleNamespace(client=object(), bag={}, today=f"{_LOGIN_YEAR}-09-28")
    with mock.patch.object(isf, "send", replay):
        return isf.IA_SA_FA_OPS[op](env, _SPEC, _STEP)  # type: ignore[arg-type]


def _change(code: str, card: str, year: int = _LOGIN_YEAR) -> dict:
    return {"code": code, "card_code": card, "depts": [], "change_date": f"{year}-03-31", "year": year}


class RegistryTests(unittest.TestCase):
    def test_registered_once_with_ops(self) -> None:
        names = known_names()
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
        for spec in isf.ia_sa_fa_cases():
            self.assertTrue(spec.requires_login and spec.optional and spec.routes, spec.name)
            self.assertTrue(ops_run.has_op(spec.steps[0].sql_name), spec.name)

    def test_tags(self) -> None:
        cases = {spec.name: spec for spec in isf.ia_sa_fa_cases()}
        self.assertIn("list:ia_adjust", auto_tags(cases["ia-adjust-read"]))
        self.assertIn("list:inventory_price_adjust", auto_tags(cases["list-inv-price-adjust"]))
        for name in ("report-fa-changes", "report-fa-depreciation"):
            self.assertTrue({"wave:2", "rpt:fa"} <= auto_tags(cases[name]), name)


class IaPriceTests(unittest.TestCase):
    def test_ia_pass_and_na(self) -> None:
        replay = _Replay()
        self.assertEqual(_run(replay, "ia_adjust_read")[0], "N/A")
        replay.ia = [_ia_row(5), _ia_row(6, posted=0)]
        status, detail = _run(replay, "ia_adjust_read")
        self.assertEqual(status, "PASS", detail)
        self.assertIn(("/v1/vouchers/load", {"type": isf.IA, "id": 5}), replay.calls)

    def test_ia_verified_mismatch_fails(self) -> None:
        replay = _Replay()
        replay.ia = [dict(_ia_row(5), verified=False)]
        status, detail = _run(replay, "ia_adjust_read")
        self.assertEqual(status, "FAIL")
        self.assertIn("verified", detail)

    def test_price_empty_passes(self) -> None:
        replay = _Replay()
        self.assertEqual(_run(replay, "price_adjust_read")[0], "PASS")
        replay.price = [{"id": 3, "code": "TJ001", "ufts": "1"}]
        self.assertEqual(_run(replay, "price_adjust_read")[0], "PASS")

    def test_bridge_error_raises(self) -> None:
        def broken(_env: object, _route: str, _body: dict) -> tuple[int, dict]:
            return 403, {"ok": False, "code": "no_permission", "message": "没有权限"}

        env = SimpleNamespace(client=object(), bag={})
        with mock.patch.object(isf, "send", broken), self.assertRaises(ValueError):
            isf.ia_adjust_read(env, _SPEC, _STEP)  # type: ignore[arg-type]


class FaTests(unittest.TestCase):
    def test_changes(self) -> None:
        replay = _Replay()
        self.assertEqual(_run(replay, "fa_changes_read")[0], "N/A")
        replay.changes = [_change("00001", "00021"), _change("00002", "00022")]
        status, detail = _run(replay, "fa_changes_read")
        self.assertEqual(status, "PASS", detail)
        years = [body.get("fiscal_year") for route, body in replay.calls if route.endswith("/fa_changes") and "limit" in body]
        self.assertIn(None, years)
        self.assertIn(_LOGIN_YEAR - 1, years)

    def test_changes_only_in_previous_year_pass(self) -> None:
        replay = _Replay()
        replay.changes = [_change("00001", "00021", _LOGIN_YEAR - 1)]
        status, detail = _run(replay, "fa_changes_read")
        self.assertEqual(status, "PASS", detail)
        self.assertIn(str(_LOGIN_YEAR - 1), detail)
        by_code = [body for route, body in replay.calls if route.endswith("/fa_changes") and "code" in body]
        self.assertEqual(by_code[-1]["fiscal_year"], _LOGIN_YEAR - 1)

    def test_change_date_outside_year_fails(self) -> None:
        replay = _Replay()
        replay.changes = [dict(_change("00001", "00021"), change_date=f"{_LOGIN_YEAR - 1}-12-31")]
        status, detail = _run(replay, "fa_changes_read")
        self.assertEqual(status, "FAIL")
        self.assertIn("不在", detail)

    def test_depreciation_matches_card(self) -> None:
        replay = _Replay()
        self.assertEqual(_run(replay, "fa_depr_read")[0], "N/A")
        replay.depr = [{"card_code": "00021", "period": 1, "accumulated": 2900.0},
                       {"card_code": "00021", "period": 8, "accumulated": 3000.0}]
        self.assertEqual(_run(replay, "fa_depr_read")[0], "PASS")
        replay.card = dict(replay.card, fields={"accumulated_depreciation": 2999.0, "disposed": False})
        status, detail = _run(replay, "fa_depr_read")
        self.assertEqual(status, "FAIL")
        self.assertIn("累计折旧", detail)

    def test_depreciation_only_in_previous_year_skips_card(self) -> None:
        replay = _Replay()
        replay.depr = [{"card_code": "00021", "period": 8, "accumulated": 1.0, "year": _LOGIN_YEAR - 1}]
        status, detail = _run(replay, "fa_depr_read")
        self.assertEqual(status, "PASS", detail)
        self.assertIn(str(_LOGIN_YEAR - 1), detail)
        # fa_card 的 fiscal_year 是登录年度，与 2025 年的折旧行不比累计折旧。
        self.assertFalse(any(route.endswith("/fa_depreciation") and "card" in body for route, body in replay.calls))

    def test_period_outside_posted_fails(self) -> None:
        replay = _Replay()
        replay.depr = [{"card_code": "00021", "period": 9, "accumulated": 0}]
        self.assertEqual(_run(replay, "fa_depr_read")[0], "FAIL")


if __name__ == "__main__":
    unittest.main()
