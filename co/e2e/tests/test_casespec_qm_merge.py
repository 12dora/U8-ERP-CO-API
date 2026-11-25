"""产品检验单合并检验用例（qm_merge）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import qm_merge as qmm
from co.e2e.casespec import qm_tpl
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_merge_sql import gone_sql, line_sql, merged_sql, pick_sql, qm_merge_samples, sources_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "qm04-merge-create-delete"
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, verified: bool = True, second: tuple[int, dict] | None = None) -> None:
        self.verified = verified
        self.second = second or (409, {"ok": False, "code": "state_mismatch", "message": "已生成过检验单"})
        self.load = {"ok": True, "merge_sources": [{"source_line_id": 1}, {"source_line_id": 2}]}
        self.calls: list[tuple[str, dict]] = []
        self.inspects = 0

    def call(self, route: str, fields: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(fields)))
        if route.endswith("/load"):
            return 200, self.load
        if route.endswith("/delete"):
            return 200, {"ok": True}
        if fields["type"] == "qm_product_inspect":
            self.inspects += 1
            return 200, {"ok": True, "id": 40 + self.inspects, "state": {"verified": self.verified}}
        if len(fields["source_ids"]) < 2:
            return 400, {"ok": False, "code": "bad_request"}
        made = [route for route, sent in self.calls if sent.get("source_ids") and len(sent["source_ids"]) == 2]
        return (200, {"ok": True, "id": 70}) if len(made) == 1 else self.second


class _Sql:
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
    operator = SimpleNamespace(acc="803", year="2026", code="op001", password="pw")
    return SimpleNamespace(client=object(), operator=operator, year="2026", today="2026-10-02", bag={})


def _sql(merged: str = "1|2|2.0000|2.0000|2", after: str = "0.0000|0|0.0000|0", pick: str = "10|11|20|21|DEMO-001|P900001") -> _Sql:
    return _Sql([
        ("ORDER BY a.MoDId DESC", _rows(pick) if pick else [""]),
        ("voucheritems AS v", ["", ""]),
        ("MIN(s.AUTOID)", _rows("501", "502")),
        ("QMCHECKVOUCHER_extradefine", _rows("-|-|-")),
        ("QMMergeCheckDetail AS m WHERE m.ID = 70)", _rows(merged)),
        ("s.AUTOID = 501", _rows("1.0000|1|1.0000|1", after)),
        ("h.ID = 70", _rows("0")),
    ])


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    def invoke(_client: object, route: str, fields: dict) -> tuple[int, dict]:
        return client.call(route, fields)

    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(qm_tpl, "run_guarded", sql), \
            mock.patch.object(sp, "invoke", invoke):
        return qmm.qm04_merge_create_delete(_env(), _SPEC, _STEP)  # type: ignore[arg-type]


def _gens(client: _Replay) -> list[dict]:
    return [fields for route, fields in client.calls if route.endswith("/generate")]


class QmMergeRegistryTests(unittest.TestCase):
    def test_registered_with_op_and_tags(self) -> None:
        self.assertEqual(known_names().count(_NAME), 1)
        spec = qmm.qm_merge_cases()[0]
        self.assertEqual(spec.name, _NAME)
        self.assertTrue(spec.requires_login and spec.routes)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertTrue({"new:qm_product_check", "new:qm_product_inspect"} <= auto_tags(spec))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in qm_merge_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("b.MoDId < a.MoDId", pick_sql())
        self.assertIn("s.ID = 7", line_sql("7"))
        self.assertIn("s.AUTOID = 8", sources_sql("8", "9"))
        self.assertIn("m.INSPECTAUTOID IN (8, 9)", merged_sql("7", "8", "9"))
        self.assertIn("m.ID = 7", gone_sql("7"))
        for bad in (lambda: line_sql("x"), lambda: merged_sql("1", "0", "2")):
            with self.assertRaises(ValueError):
                bad()


class QmMergeFlowTests(unittest.TestCase):
    def test_merge_then_delete_and_clean_up(self) -> None:
        client = _Replay()
        status, detail = _run(client, _sql())
        self.assertEqual(status, "PASS", detail)
        gens = _gens(client)
        self.assertEqual([body["type"] for body in gens], ["qm_product_inspect"] * 2 + ["qm_product_check"] * 3)
        self.assertEqual(gens[0]["lines"], [{"source_line_id": 11, "quantity": 1}])
        self.assertEqual(gens[1]["id"], 20)
        self.assertEqual([body.get("source_ids") for body in gens[2:]], [[501], [501, 502], [501, 502]])
        self.assertNotIn("id", gens[3])
        self.assertEqual(gens[3]["head"], {"cCheckPersonCode": "P900001", "project_code": "DEMO-001"})
        deletes = [(fields["type"], fields["id"]) for route, fields in client.calls if route.endswith("/delete")]
        self.assertEqual(deletes, [("qm_product_check", 70), ("qm_product_inspect", 42), ("qm_product_inspect", 41)])

    def test_sources_not_back_fails_after_cleanup(self) -> None:
        client = _Replay()
        status, detail = _run(client, _sql(after="1.0000|1|0.0000|0"))
        self.assertEqual(status, "FAIL")
        self.assertIn("删除后两条报检行", detail)
        self.assertEqual(client.calls[-1][1]["type"], "qm_product_inspect")

    def test_wrong_merge_detail_fails(self) -> None:
        status, detail = _run(_Replay(), _sql(merged="1|1|1.0000|2.0000|1"))
        self.assertEqual(status, "FAIL")
        self.assertIn("合并检验单回读", detail)

    def test_second_merge_accepted_fails_and_drops_it(self) -> None:
        client = _Replay(second=(200, {"ok": True, "id": 71}))
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("没有被拒绝", detail)
        deleted = [fields["id"] for route, fields in client.calls if route.endswith("/delete")]
        self.assertEqual(deleted[0], 71)

    def test_unverified_inspect_is_na_and_cleans_up(self) -> None:
        client = _Replay(verified=False)
        status, _detail = _run(client, _sql())
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls[-1][0], "/v1/vouchers/delete")

    def test_nothing_to_pick_is_na_without_calls(self) -> None:
        client = _Replay()
        status, _detail = _run(client, _sql(pick=""))
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
