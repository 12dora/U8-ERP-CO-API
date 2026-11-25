"""参照合并检验的产品检验单生成产成品入库（qm_merged_in）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import qm_merged_in as qmm
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_merged_in_sql import gone_sql, levels_sql, line_sql, merged_pick_sql, qm_merged_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "product-in-merged-check"
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_PICK = "ROW:100|200|300|MO1|10|201\n"
_SOURCE = {"source_line_id": 200, "mo_detail_id": 300, "remaining": 5.0, "done": False}


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, refuse: tuple[int, dict] = (400, {"ok": False, "code": "bad_request", "message": "明细行不存在"})):
        self.refuse = refuse
        self.load = {"ok": True, "merge_sources": [_SOURCE, dict(_SOURCE, source_line_id=199)]}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(fields)))
        if route.endswith("/load"):
            return 200, self.load
        if route.endswith("/generate"):
            if fields["lines"][0]["source_line_id"] == 201:
                return self.refuse
            return 200, {"ok": True, "id": 5}
        return 200, {"ok": True}


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:80])


def _rows(*values: str) -> list[str]:
    return [f"ROW:{value}\n" for value in values]


def _env() -> SimpleNamespace:
    operator = SimpleNamespace(acc="803", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=object(), operator=operator, year="2026", today="2026-10-02", bag={})


def _sql(line: str = "1|200|300|MO1|100|", after: str = "3.0000|6.0000|3.0000", gone: str = "0") -> _Sql:
    return _Sql([
        ("ORDER BY q.ID DESC, m.AUTOID", [_PICK]),
        ("QMMergeCheckDetail AS m WHERE m.AUTOID", _rows("3.0000|6.0000|3.0000", "4.0000|7.0000|4.0000", after)),
        ("rdrecords10 AS b JOIN", _rows(line)),
        ("RdRecord10 AS h WHERE h.ID", _rows(gone)),
    ])


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    def invoke(_client: object, route: str, fields: dict) -> tuple[int, dict]:
        return client.call(route, fields)

    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(sp, "invoke", invoke), \
            mock.patch.object(qmm, "td", lambda key: "RD-" + key.rsplit(".", 1)[-1]):
        return qmm.product_in_merged_check(_env(), _SPEC, _STEP)  # type: ignore[arg-type]


def _order(client: _Replay) -> list[tuple[str, str]]:
    return [(route.rsplit("/", 1)[1], fields.get("action", "")) for route, fields in client.calls]


class QmMergedRegistryTests(unittest.TestCase):
    def test_registered_with_op_and_stock_tag(self) -> None:
        names = known_names()
        self.assertEqual([spec.name for spec in qmm.qm_merged_cases()], [_NAME])
        self.assertEqual(names.count(_NAME), 1)
        spec = qmm.qm_merged_cases()[0]
        self.assertTrue(spec.requires_login and spec.routes)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertTrue({"stock", "new:product_in"} <= auto_tags(spec))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in qm_merged_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("BMERGECHECKFLAG, 0) = 1", merged_pick_sql())
        self.assertIn("m.AUTOID = 2", levels_sql("1", "2", "3"))
        self.assertIn("b.ID = 7", line_sql("7"))
        self.assertIn("h.ID = 7", gone_sql("7"))
        for bad in (lambda: line_sql("x"), lambda: levels_sql("1", "0", "3")):
            with self.assertRaises(ValueError):
                bad()


class ProductInMergedTests(unittest.TestCase):
    def test_generate_one_source_then_rolled_back(self) -> None:
        client = _Replay()
        status, detail = _run(client, _sql())
        self.assertEqual(status, "PASS", detail)
        gens = [fields for route, fields in client.calls if route.endswith("/generate")]
        self.assertEqual([body["lines"][0]["source_line_id"] for body in gens], [201, 200])
        body = gens[1]
        self.assertEqual((body["type"], body["id"], body["source_type"]), ("product_in", 100, "qm_product_check"))
        self.assertEqual((body["head"]["cwhcode"], body["head"]["crdcode"]), ("10", "RD-product_in_rd"))
        self.assertEqual(body["lines"], [{"source_line_id": 200, "quantity": 1}])
        self.assertEqual(
            _order(client),
            [("load", ""), ("generate", ""), ("generate", ""), ("verify", "verify"), ("verify", "unverify"), ("delete", "")],
        )

    def test_missing_merge_sources_fails_before_writing(self) -> None:
        client = _Replay()
        client.load = {"ok": True}
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("merge_sources", detail)
        self.assertEqual(_order(client), [("load", "")])

    def test_other_source_accepted_fails_and_cleans_up(self) -> None:
        client = _Replay(refuse=(200, {"ok": True, "id": 9}))
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("没有被拒绝", detail)
        route, fields = client.calls[-1]
        self.assertEqual((route, fields["type"], fields["id"]), ("/v1/vouchers/delete", "product_in", 9))

    def test_header_mo_code_written_fails_after_cleanup(self) -> None:
        client = _Replay()
        status, detail = _run(client, _sql(line="1|200|300|MO1|100|MO1"))
        self.assertEqual(status, "FAIL")
        self.assertIn("表头无订单号", detail)
        self.assertEqual(_order(client)[-1], ("delete", ""))

    def test_levels_not_back_fails(self) -> None:
        status, detail = _run(_Replay(), _sql(after="4.0000|6.0000|3.0000"))
        self.assertEqual(status, "FAIL")
        self.assertIn("删除后来源累计入库数", detail)

    def test_nothing_to_pick_is_na_without_calls(self) -> None:
        client = _Replay()
        status, _detail = _run(client, _Sql([("ORDER BY q.ID DESC, m.AUTOID", [""])]))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
