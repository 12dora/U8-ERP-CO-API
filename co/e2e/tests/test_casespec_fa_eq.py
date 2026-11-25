"""固定资产卡片、变动单与设备台账（fa_eq、fa_eq_more）的登记、分片、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import fa_eq as fa
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.fa_eq_sql import eq_count_sql, fa_depr_sql, fa_eq_samples, fa_mark_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import bind_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = (
    "fa-card-cycle", "fa-card-period-refuse", "fa-dispose-refused", "archive-read-equipment",
    "equipment-create",
)
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_DAY = "2026-09-01"
_DRY = {"ok": True, "dry_run": True, "mode": "validate", "warnings": ["validate_only"]}
_STATE = "9|2026-09-01|0|8"
_REF = "50|101|1001|3|D01|60"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])


def _rows(*values: str) -> list[str]:
    return [f"ROW:{value}\n" for value in values]


def _env(client: object, year: str = "2026") -> SimpleNamespace:
    operator = SimpleNamespace(acc="803", year=year, code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year=year, today="2026-10-03", bag={})


def _run(op: str, client: _Replay, sql: _Sql, year: str = "2026") -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return fa.FA_EQ_OPS[op](_env(client, year), _SPEC, _STEP)


def _order(client: _Replay) -> list[str]:
    return [route.split("/v1/", 1)[1] + (":dry" if fields.get("dry_run") else "") for route, fields in client.calls]


def _err(status: int, code: str) -> U8CoError:
    return U8CoError(status, code, "拒绝")


def _made(asset: str = "") -> dict:
    return {"ok": True, "archive": "fa_card", "code": "00508", "asset_num": asset, "card_id": 2001}


class _Echo(_Replay):
    """新增卡片时把请求里的资产编号原样放进响应（资产编号带时间戳，测试里预先不知道）。"""

    def call(self, route: str, fields: dict) -> dict:
        got = super().call(route, fields)
        if route.endswith("/archives/create") and not fields.get("dry_run") and "asset_num" in got:
            got = dict(got, asset_num=fields["code"])
        if route.endswith("/archives/get") and isinstance(got.get("fields"), dict):
            got = dict(got, fields=dict(got["fields"], asset_num=self.asset))
        if route.endswith("/vouchers/create") and not fields.get("dry_run") and "card_code" in got:
            got = dict(got, asset_num=self.asset)
        return got

    @property
    def asset(self) -> str:
        made = [f for r, f in self.calls if r.endswith("/archives/create")]
        return str(made[0]["code"]) if made else ""


class _EqEcho(_Replay):
    """新增设备的响应带回请求里的编码。"""

    def call(self, route: str, fields: dict) -> dict:
        got = super().call(route, fields)
        if route.endswith("/archives/create") and not fields.get("dry_run"):
            got = dict(got, code=fields["code"])
        return got


def _card_sql(after: list[str], state: str = _STATE, depr: str = "0") -> _Sql:
    return _Sql([
        ("iLastPeriod", _rows(state)),
        ("fa_DeprList", _rows(depr)),
        ("fa_DeptScale AS sc", _rows(_REF)),
        ("MAX(c.sCardID)", _rows("2000|0|", "2000|0|", *after)),
        ("fa_Vouchers", _rows("0", "1")),
    ])


def _get_card() -> dict:
    return {"ok": True, "archive": "fa_card", "code": "00508", "fields": {"name": "E2E回归卡片", "entry_date": _DAY}}


class RegistryTests(unittest.TestCase):
    def test_registered_once_with_ops(self) -> None:
        cases = fa.fa_eq_cases()
        self.assertEqual(tuple(spec.name for spec in cases), _NAMES)
        for spec in cases:
            self.assertEqual(known_names().count(spec.name), 1)
            self.assertTrue(spec.requires_login and spec.routes)
            self.assertTrue(ops_run.has_op(spec.steps[-1].sql_name))

    def test_tags_keep_card_writers_apart(self) -> None:
        cases = {spec.name: spec for spec in build_cases({})}

        def clash(left: str, right: str) -> bool:
            a, b = cases[left], cases[right]
            return conflicts(bind_tags(a, {}), bind_tags(b, {}), a.exclusive, b.exclusive)

        self.assertTrue(clash("fa-card-cycle", "fa-card-period-refuse"))
        self.assertTrue(clash("archive-read-fa-card", "fa-card-cycle"))
        self.assertTrue(clash("archive-read-equipment", "equipment-create"))
        self.assertFalse(clash("fa-dispose-refused", "fa-card-cycle"))
        self.assertNotIn("fa-change-cycle", cases)

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in fa_eq_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
        self.assertIn("sAssetNum = N'91031123'", fa_mark_sql("91031123"))
        self.assertIn("l.iyear = 2026 AND l.iPeriod = 7", fa_depr_sql(2026, 7))
        for call in (lambda: fa_mark_sql("9'--"), lambda: fa_mark_sql(""), lambda: eq_count_sql("ZZ 1"), lambda: fa_depr_sql(2026, 13)):
            with self.assertRaises(ValueError):
                call()


class CardTests(unittest.TestCase):
    def test_card_cycle_passes(self) -> None:
        client = _Echo({
            "/v1/archives/create": [_DRY, _made("x")],
            "/v1/archives/get": [_get_card(), _err(404, "not_found")],
            "/v1/archives/delete": [{"ok": True, "deleted": True}],
        })
        status, detail = _run("fa_card_cycle", client, _card_sql(["2001|0|"]))
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_order(client), [
            "archives/create:dry", "archives/create", "archives/get", "archives/delete", "archives/get",
        ])
        create = client.calls[1][1]
        self.assertEqual(create["date"], _DAY)
        self.assertTrue(create["code"].startswith("91"))
        self.assertEqual(create["fields"]["start_date"], "2026-08-01")
        self.assertEqual(create["fields"]["dept_code"], "D01")
        self.assertEqual(create["fields"]["useful_life_months"], 60)
        self.assertNotIn("currency", create["fields"])
        self.assertEqual(client.calls[3][1]["code"], "00508")

    def test_card_cycle_runs_when_db_year_precedes_fa_year(self) -> None:
        """账套库年度 2024、固定资产当前期间 2026 年 9 期（库年度早于固定资产年度的账套形态）：不按库年度判 N/A，登录日期仍取可写日期。"""
        client = _Echo({
            "/v1/archives/create": [_DRY, _made("x")],
            "/v1/archives/get": [_get_card(), _err(404, "not_found")],
            "/v1/archives/delete": [{"ok": True, "deleted": True}],
        })
        status, detail = _run("fa_card_cycle", client, _card_sql(["2001|0|"]), year="2024")
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[1][1]["date"], _DAY)

    def test_writable_month_mismatch_is_na(self) -> None:
        client = _Replay({})
        status, _detail = _run("fa_card_cycle", client, _card_sql([], state="9|2026-08-01|0|8"))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])

    def test_depreciated_period_is_na(self) -> None:
        # 当前期间已计提折旧：桥会拒绝撤销新增，新增前就 N/A，不建卡片。
        client = _Replay({})
        status, detail = _run("fa_card_cycle", client, _card_sql([], depr="12"))
        self.assertEqual(status, "N/A")
        self.assertIn("固定资产当前期间已计提折旧，不能演示撤销新增", detail)
        self.assertEqual(client.calls, [])

    def test_fa_not_enabled_is_na(self) -> None:
        client = _Replay({})
        status, _detail = _run("fa_card_cycle", client, _card_sql([], state="|||"))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])

    def test_unknown_outcome_deletes_by_asset(self) -> None:
        # 新增 504 但卡片已落库：按资产编号查到卡片编号再撤销。
        client = _Replay({
            "/v1/archives/create": [_DRY, _err(504, "outcome_unknown")],
            "/v1/archives/delete": [{"ok": True, "deleted": True}],
        })
        status, detail = _run("fa_card_cycle", client, _card_sql(["2001|1|00508"]))
        self.assertEqual(status, "FAIL")
        self.assertIn("504", detail)
        self.assertEqual(client.calls[-1][0], "/v1/archives/delete")
        self.assertEqual(client.calls[-1][1]["code"], "00508")

    def test_period_refuse(self) -> None:
        client = _Replay({"/v1/archives/create": [_err(409, "state_mismatch")]})
        status, detail = _run("fa_period_refuse", client, _card_sql(["2000|0|"]))
        self.assertEqual(status, "PASS", detail)
        body = client.calls[0][1]
        self.assertEqual(body["date"], "2026-08-01")
        self.assertEqual(body["fields"]["start_date"], "2026-07-01")
        client = _Replay({})
        self.assertEqual(_run("fa_period_refuse", client, _card_sql([], state="9|2026-09-01|0|"))[0], "N/A")


class MoreTests(unittest.TestCase):
    def test_refusals(self) -> None:
        bad = [_err(400, "bad_request")] * 2
        client = _Replay({"/v1/vouchers/create": bad, "/v1/archives/update": bad[:1], "/v1/vouchers/load": bad[:1]})
        status, detail = _run("fa_refused", client, _Sql([]))
        self.assertEqual(status, "PASS", detail)
        self.assertTrue(all(fields.get("dry_run") is True for _route, fields in client.calls))
        self.assertEqual([f.get("type") for r, f in client.calls if r.endswith("/vouchers/create")], ["fa_change", "fa_dispose"])
        client = _Replay({"/v1/vouchers/create": bad, "/v1/archives/update": [{"ok": True}], "/v1/vouchers/load": bad[:1]})
        status, detail = _run("fa_refused", client, _Sql([]))
        self.assertEqual(status, "FAIL")
        self.assertIn("修改卡片 200", detail)

    def test_equipment_read(self) -> None:
        items = {"ok": True, "items": [{"code": "E001"}, {"code": "E002"}]}
        client = _Replay({
            "/v1/archives/list": [items, items, {"ok": True, "items": [{"code": "E001"}]}],
            "/v1/archives/get": [{"ok": True, "archive": "equipment", "code": "E001", "fields": {"cEQName": "设备"}}],
        })
        self.assertEqual(_run("eq_read", client, _Sql([]))[0], "PASS")
        empty = _Replay({"/v1/archives/list": [{"ok": True, "items": []}]})
        self.assertEqual(_run("eq_read", empty, _Sql([]))[0], "N/A")

    def test_equipment_create(self) -> None:
        client = _EqEcho({
            "/v1/archives/create": [_DRY, {"ok": True, "archive": "equipment"}],
            "/v1/archives/get": [{"ok": True, "archive": "equipment", "fields": {"cEQName": "E2E回归设备"}}],
            "/v1/archives/update": [_err(400, "bad_request")],
            "/v1/archives/delete": [_err(400, "bad_request")],
        })
        sql = _Sql([("ORDER BY e.AutoID", _rows("645|LB1|D01|01")), ("e.cEQCode", _rows("0", "0", "1"))])
        status, detail = _run("eq_create", client, sql)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_order(client), [
            "archives/create:dry", "archives/create", "archives/get", "archives/update", "archives/delete",
        ])
        made = client.calls[1][1]
        self.assertTrue(made["code"].startswith("ZZ"))
        self.assertEqual(made["fields"], {"name": "E2E回归设备", "ceqtypecode": "LB1", "cdepcode": "D01", "cstacode": "01"})
        empty = _Replay({})
        self.assertEqual(_run("eq_create", empty, _Sql([("ORDER BY e.AutoID", _rows("0||"))]))[0], "N/A")

if __name__ == "__main__":
    unittest.main()
