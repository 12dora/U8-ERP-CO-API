"""采购结算单写入用例（pu_settle_write）的登记、标签、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import pu_settle_read as read
from co.e2e.casespec import pu_settle_write as sw
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.pu_settle_write_sql import pu_settle_write_samples, settle_pick_write_sql, settle_state_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "purchase-settle-cycle"
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_PICK = "ROW:1000000001|0000001|2|2026-09-30\n"
_NONE = "ROW:0||0|0|2||0|\n"
_DONE = "ROW:1|2773|2|2|2|2026-09-30|2|\n"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, states: list[str], pick: str = _PICK) -> None:
        self.pick = pick
        self.states = list(states)
        self.calls: list[tuple[str, dict]] = []
        self.gen = (200, {"ok": True, "id": 2773, "code": "000000000002773", "lines": 2, "settle_date": "2026-09-30"})
        self.again = (409, {"ok": False, "code": "state_mismatch", "message": "发票已结算"})
        self.delete = (200, {"ok": True, "deleted": True})

    def call(self, route: str, fields: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(fields)))
        if route.endswith("/delete"):
            return self.delete
        if fields.get("dry_run"):
            return 200, {"ok": True, "dry_run": True}
        made = sum(1 for r, f in self.calls if r.endswith("/generate") and not f.get("dry_run"))
        return self.gen if made == 1 else self.again

    def sql(self, query: str) -> str:
        if "FROM dbo.GL_mend AS g" in query:
            return self.pick
        if "FROM dbo.PurBillVouch AS h WHERE h.PBVID = 1000000001" in query:
            return self.states.pop(0) if self.states else _NONE
        raise AssertionError("没有准备回答：" + query[:80])


def _env() -> SimpleNamespace:
    operator = SimpleNamespace(acc="803", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=object(), operator=operator, year="2026", today="2026-10-03", bag={})


def _run(client: _Replay) -> tuple[str, str]:
    def invoke(_client: object, route: str, fields: dict) -> tuple[int, dict]:
        return client.call(route, fields)

    def sql(_env: object, query: str, _label: str) -> str:
        return client.sql(query)

    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(sp, "invoke", invoke):
        return sw.pu_settle_cycle(_env(), _SPEC, _STEP)  # type: ignore[arg-type]


def _routes(client: _Replay) -> list[str]:
    return [route.rsplit("/", 1)[1] + ("?dry" if fields.get("dry_run") else "") for route, fields in client.calls]


class RegistrationTests(unittest.TestCase):
    def test_registered_once(self) -> None:
        (spec,) = sw.pu_settle_write_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertTrue(spec.optional and spec.routes)

    def test_tags_keep_the_read_case_and_pu_chain_apart(self) -> None:
        tags = auto_tags(sw.pu_settle_write_cases()[0])
        self.assertTrue({"new:purchase_settle", "w:purchase_settle", "w:purchase_invoice", "opening:pu"} <= tags)
        self.assertTrue(conflicts(tags, auto_tags(read.pu_settle_cases()[0])))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in pu_settle_write_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("ISNULL(g.bflag_PU, 0) = 0", settle_pick_write_sql())
        self.assertIn("ORDER BY h.PBVID DESC", settle_pick_write_sql())
        self.assertIn("h.PBVID = 77", settle_state_sql(77))


class CycleTests(unittest.TestCase):
    def test_happy_path_settles_and_restores(self) -> None:
        client = _Replay([_NONE, _DONE, _NONE])
        status, detail = _run(client)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(_routes(client), ["generate?dry", "generate", "generate", "delete"])
        gen = client.calls[1][1]
        self.assertEqual((gen["type"], gen["source_type"], gen["id"]), ("purchase_settle", "purchase_invoice", 1000000001))
        self.assertEqual(gen["head"], {"settle_date": "2026-09-30"})
        self.assertNotIn("lines", gen)
        self.assertEqual((client.calls[3][1]["id"], client.calls[3][1]["date"]), (2773, "2026-09-30"))

    def test_mispaired_settle_fails_and_cleans_up(self) -> None:
        client = _Replay([_NONE, "ROW:1|2773|2|1|2|2026-09-30|2|\n", _DONE, _NONE])
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("配对一致 1", detail)
        self.assertEqual(_routes(client), ["generate?dry", "generate", "delete"])

    def test_second_settle_accepted_fails(self) -> None:
        client = _Replay([_NONE, _DONE, _DONE, _NONE])
        client.again = (200, {"ok": True, "id": 2774})
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("再结算应 409", detail)

    def test_stamp_left_after_delete_fails(self) -> None:
        client = _Replay([_NONE, _DONE, "ROW:0||0|0|2|2026-09-30|0|\n"])
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("没有清空", detail)

    def test_dry_run_that_writes_fails(self) -> None:
        client = _Replay([_DONE, _DONE, _NONE])
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("预演之后", detail)
        self.assertEqual(_routes(client), ["generate?dry", "delete"])

    def test_cleanup_failure_is_reported(self) -> None:
        client = _Replay([_NONE, "ROW:2|2773|2|2|2|2026-09-30|2|\n", _DONE])
        client.delete = (409, {"ok": False, "message": "存货核算已记账，先取消记账"})
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("清理失败", detail)

    def test_unknown_outcome_waits_and_cleans_every_settle(self) -> None:
        client = _Replay([_NONE, "ROW:2|2774|2|2|2|2026-09-30|2|\n", _DONE, _NONE])
        client.gen = (504, {"ok": False, "code": "outcome_unknown", "message": "结果未知"})
        with mock.patch.object(sw.time, "sleep") as slept:
            status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("504", detail)
        slept.assert_called_once()
        deleted = [fields["id"] for route, fields in client.calls if route.endswith("/delete")]
        self.assertEqual(deleted, [2774, 2773])

    def test_nothing_to_pick_is_na_without_calls(self) -> None:
        client = _Replay([], pick="")
        status, _detail = _run(client)
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
