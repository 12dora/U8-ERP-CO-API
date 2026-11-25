"""无来源销售出库单新增（sale_out_nosrc）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import sale_out_nosrc as m
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.sale_out_nosrc_sql import head_sql, sale_out_nosrc_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("sale-out-nosrc", "dry-sale-out-nosrc")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_CREATE = "/v1/vouchers/create"
_LOAD = "/v1/vouchers/load"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_PICK = "05|A001|C001|D01|101|XS"
_REFUSED = U8CoError(409, "state_mismatch", "账套已启用销售管理，销售出库单须参照发货单生成")


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
    """按 SQL 里的特征串依次回答（按登记顺序找第一个还有回答的键），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="803", year="2024", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2024", today="2024-12-20", bag={})


def _rows(*values: str) -> str:
    return "".join(f"ROW:{value}\n" for value in values)


def _sql(gate: str, pick: str, *more: tuple[str, list[str]]) -> _Sql:
    answers = [("dSaleStartDate", [_rows(gate)]), ("CROSS APPLY", [_rows(pick) if pick else ""])]
    return _Sql(answers + list(more))


def _run(op, client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(m, "run_guarded", sql):
        return op(_env(client), _SPEC, _STEP)


class RegistryTests(unittest.TestCase):
    def test_registered_with_op_and_tags(self) -> None:
        names = known_names()
        cases = m.sale_out_nosrc_cases()
        self.assertEqual(tuple(spec.name for spec in cases), _NAMES)
        for spec in cases:
            with self.subTest(case=spec.name):
                self.assertEqual(names.count(spec.name), 1)
                self.assertTrue(spec.requires_login and spec.optional and spec.routes)
                self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
                self.assertIn("new:sale_out", auto_tags(spec))

    def test_coverage_reaches_cases(self) -> None:
        for path in ("co/bridge/src/StockSaleOut.cs", "co/bridge/src/SrcLess.cs"):
            picked = select_changed([path], list(known_names()))
            with self.subTest(path=path):
                self.assertTrue(set(_NAMES) <= set(picked))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in sale_out_nosrc_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        with self.assertRaises(ValueError):
            head_sql("0")


class ReplayTests(unittest.TestCase):
    def test_forbidden_gives_409_and_no_doc(self) -> None:
        client = _Replay({_CREATE: [_REFUSED]})
        status, detail = _run(m.sale_out_nosrc, client, _sql("2024-12-01|False", _PICK, ("MAX(h.ID)", [_rows("9"), _rows("9")])))
        self.assertEqual(status, "PASS", detail)
        sent = client.calls[0][1]
        self.assertEqual((sent["type"], sent["head"]["ccuscode"], sent["lines"][0]["iquantity"]), ("sale_out", "C001", 1))

    def test_forbidden_but_created_fails_and_drops(self) -> None:
        client = _Replay({_CREATE: [{"ok": True, "id": 77}], _DELETE: [{"deleted": True}], _LOAD: [U8CoError(404, "not_found", "")]})
        status, detail = _run(m.sale_out_nosrc, client, _sql("|True", "", ("MAX(h.ID)", [_rows("9")])))
        self.assertEqual(status, "FAIL")
        self.assertIn("77", detail)
        self.assertEqual(client.calls[0][1]["head"]["cwhcode"], "01")  # 挑不到数据时用占位正文

    def test_open_without_pick_is_na(self) -> None:
        status, _detail = _run(m.sale_out_nosrc, _Replay({}), _sql("|False", ""))
        self.assertEqual(status, "N/A")

    def test_happy_cycle(self) -> None:
        client = _Replay({
            _CREATE: [{"ok": True, "id": 88}],
            _LOAD: [{"ok": True}, U8CoError(404, "not_found", "")],
            _VERIFY: [{"ok": True}, {"ok": True}],
            _DELETE: [{"ok": True, "deleted": True}],
        })
        sql = _sql("|False", _PICK, ("rdrecords32", [_rows("库存|普通销售|87|0|1")]))
        status, detail = _run(m.sale_out_nosrc, client, sql)
        self.assertEqual(status, "PASS", detail)
        order = [(route.rsplit("/", 1)[1], fields.get("action", "")) for route, fields in client.calls]
        self.assertEqual(order, [("create", ""), ("load", ""), ("verify", "verify"), ("verify", "unverify"),
                                 ("delete", ""), ("load", "")])
        head = client.calls[0][1]["head"]
        self.assertEqual((head["crdcode"], head["cstcode"]), ("101", "XS"))

    def test_wrong_head_fails_and_still_drops(self) -> None:
        client = _Replay({
            _CREATE: [{"ok": True, "id": 88}],
            _LOAD: [{"ok": True}, U8CoError(404, "not_found", "")],
            _DELETE: [{"ok": True, "deleted": True}],
        })
        sql = _sql("|False", _PICK, ("rdrecords32", [_rows("发货单|普通销售|87|1|1")]))
        status, detail = _run(m.sale_out_nosrc, client, sql)
        self.assertEqual(status, "FAIL")
        self.assertIn("期望", detail)
        self.assertEqual(client.calls[-2][0], _DELETE)

    def test_dry_run_rolls_back(self) -> None:
        dry = {"dry_run": True, "mode": "rollback", "docs": [{"type": "sale_out", "id": 90, "state": "exists",
                                                              "head": {"csource": "库存"}}]}
        client = _Replay({_CREATE: [dry]})
        status, detail = _run(m.dry_sale_out_nosrc, client, _sql("|0", _PICK, ("MAX(h.ID)", [_rows("9"), _rows("9")])))
        self.assertEqual(status, "PASS", detail)
        self.assertIs(client.calls[0][1]["dry_run"], True)

    def test_dry_run_left_doc_fails(self) -> None:
        dry = {"dry_run": True, "mode": "rollback", "docs": [{"type": "sale_out", "id": 90, "state": "exists",
                                                              "head": {"csource": "库存"}}]}
        client = _Replay({_CREATE: [dry]})
        status, _detail = _run(m.dry_sale_out_nosrc, client, _sql("|0", _PICK, ("MAX(h.ID)", [_rows("9"), _rows("90")])))
        self.assertEqual(status, "FAIL")


if __name__ == "__main__":
    unittest.main()
