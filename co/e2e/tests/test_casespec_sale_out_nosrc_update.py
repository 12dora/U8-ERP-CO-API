"""来源库存的销售出库单修改（sale_out_nosrc_update）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import sale_out_nosrc_update as m
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.sale_out_nosrc_update_sql import head_sql, lines_sql, sale_out_nosrc_update_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_UPDATE = "/v1/vouchers/update"
_LOAD = "/v1/vouchers/load"


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
    """按 SQL 里的特征串依次回答（每个键一个队列，按登记顺序找第一个还有回答的键），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:120])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="803", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-03", bag={})


def _ok(**extra: object) -> dict:
    body: dict = {"ok": True}
    body.update(extra)
    return body


def _rows(*values: str) -> str:
    return "".join(f"ROW:{value}\n" for value in values)


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return m.sale_out_nosrc_update(_env(client), _SPEC, _STEP)


def _order(client: _Replay) -> list[str]:
    return [route.rsplit("/", 1)[1] for route, _fields in client.calls]


_HEAD0 = _rows("库存|普通销售|87|U8CO-E2E-OUT-NOSRC|W1")
_HEAD1 = _rows("库存|普通销售|87|U8CO-E2E-OUT-NOSRC-改|W1")
_ONE = _rows("31|2.0000||0")
_TWO = _rows("31|1.0000|改|0", "32|1.0000|加|0")
_LEFT = _rows("31|1.0000|改|0")


def _sql(gate: str = "ROW:|False\n", pick: str = "ROW:W1|I1|C1|D1|101|XS|W2\n", two: str = _TWO) -> _Sql:
    return _Sql([
        ("dSaleStartDate", [gate]),
        ("AS w2", [pick]),
        ("rdrecord32 AS h", [_HEAD0, _HEAD1]),
        ("rdrecords32 AS b", [_ONE, two, _LEFT]),
    ])


def _client(updates: list | None = None) -> _Replay:
    refused = U8CoError(409, "state_mismatch", "不能修改销售出库单的仓库")
    return _Replay({
        "/v1/vouchers/create": [_ok(id=5)],
        _UPDATE: updates if updates is not None else [_ok(id=5, lines=2), _ok(id=5, lines=1), refused],
        _LOAD: [_ok(id=5), U8CoError(404, "not_found", "单据不存在")],
        "/v1/vouchers/delete": [_ok(deleted=True)],
    })


class RegistryTests(unittest.TestCase):
    def test_registered_with_op_and_tags(self) -> None:
        cases = m.sale_out_nosrc_update_cases()
        self.assertEqual([spec.name for spec in cases], [m.NAME])
        spec = cases[0]
        self.assertEqual(known_names().count(m.NAME), 1)
        self.assertTrue(spec.requires_login and spec.routes and spec.optional)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertIn("stock", auto_tags(spec))
        self.assertIn("new:sale_out", auto_tags(spec))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in sale_out_nosrc_update_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("WHERE h.ID = 7", head_sql("7"))
        self.assertIn("WHERE b.ID = 7 ORDER BY b.AutoID", lines_sql("7"))
        for bad in ("0", "x", "7;"):
            with self.assertRaises(ValueError):
                lines_sql(bad)


class FlowTests(unittest.TestCase):
    def test_happy_path_edits_adds_drops_refuses_wh_and_deletes(self) -> None:
        client = _client()
        status, detail = _run(client, _sql())
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_order(client), ["create", "update", "update", "update", "load", "delete", "load"])
        create = client.calls[0][1]
        self.assertEqual(create["head"]["cwhcode"], "W1")
        self.assertEqual((create["head"]["crdcode"], create["head"]["cstcode"]), ("101", "XS"))
        self.assertEqual(create["lines"], [{"cinvcode": "I1", "iquantity": 2}])
        edit = client.calls[1][1]
        self.assertEqual([line["op"] for line in edit["lines"]], ["update", "add"])
        self.assertEqual(edit["lines"][0]["line_id"], 31)
        self.assertEqual(client.calls[2][1]["lines"], [{"op": "delete", "line_id": 32}])
        self.assertEqual(client.calls[3][1]["head"], {"cWhCode": "W2"})

    def test_sale_managed_account_is_not_applicable(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(gate="ROW:2024-12-01|False\n"))
        self.assertEqual(status, "N/A")
        self.assertIn("销售管理", detail)
        self.assertEqual(client.calls, [])

    def test_sacreat_option_is_not_applicable(self) -> None:
        client = _client()
        status, _detail = _run(client, _sql(gate="ROW:|True\n"))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])

    def test_no_pick_is_not_applicable(self) -> None:
        client = _client()
        status, _detail = _run(client, _sql(pick=""))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])

    def test_single_warehouse_skips_the_wh_refusal(self) -> None:
        client = _client([_ok(id=5, lines=2), _ok(id=5, lines=1)])
        status, detail = _run(client, _sql(pick="ROW:W1|I1|C1|D1|||\n"))
        self.assertEqual(status, "PASS", detail)
        self.assertNotIn("crdcode", client.calls[0][1]["head"])
        self.assertEqual(_order(client), ["create", "update", "update", "load", "delete", "load"])

    def test_refused_add_fails_and_still_deletes(self) -> None:
        client = _client([U8CoError(400, "bad_request", "该单据类型修改不能新增行")])
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("改数量并加行", detail)
        self.assertEqual(_order(client), ["create", "update", "delete"])

    def test_linked_dispatch_line_fails(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(two=_rows("31|1.0000|改|0", "32|1.0000|加|1")))
        self.assertEqual(status, "FAIL")
        self.assertIn("不挂发货单行", detail)
        self.assertEqual(_order(client)[-1], "delete")

    def test_wh_change_accepted_fails(self) -> None:
        client = _client([_ok(id=5, lines=2), _ok(id=5, lines=1), _ok(id=5, lines=1)])
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("不能修改销售出库单的仓库", detail)


if __name__ == "__main__":
    unittest.main()
