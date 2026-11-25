"""不良品处理单修改用例（qm_reject_update）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import qm_reject_update as qu
from co.e2e.casespec import qm_reject as qr
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_reject_update_sql import edit_state_sql, first_line_sql, qm_reject_update_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "qm-reject-update"
_SPEC = CaseSpec(_NAME, "s")
_STEP = Step("custom")
_CLEAN = "ROW:0|0|||||||0|0.0000\n"
_CODE = qr.PRODUCT.prefix + "1"
_MADE = f"ROW:1|1|501|{_CODE}|356|QM06||0|1|1.0000\n"
_E0 = "ROW:1||||1|1|1.0000\n"
_E1 = "ROW:2|DEMO-001|张三||2|2|1.0000\n"
_E2 = "ROW:3|DEMO-001|张三|张三|2|2|1.0000\n"


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
        if isinstance(got, Exception):
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
        raise AssertionError("没有准备回答：" + query[:80])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-10-02", bag={})


def _sql(states: list[str], edits: list[str], other: str = "ROW:Sys01\n") -> _Sql:
    return _Sql([
        ("FROM dbo.QMCHECKVOUCHER AS c\nWHERE", ["ROW:77|1.0000|I01\n"]),
        ("FROM dbo.Reason", ["ROW:01\n"]),
        ("FROM dbo.QMSCRAPDISPOSE", ["ROW:Sys03\n", other]),
        ("FROM dbo.voucheritems", [""]),
        ("ORDER BY s.AUTOID", ["ROW:9001|1.0000\n"]),
        ("CONVERT(bigint, r.UFTS)", edits),
        ("OUTER APPLY", states),
    ])


def _client(update: list | None = None, verify: list | None = None) -> _Replay:
    return _Replay({
        "/v1/vouchers/generate": [{"ok": True, "dry_run": True}, {"ok": True, "id": 501}],
        "/v1/vouchers/update": update if update is not None else [
            {"ok": True, "dry_run": True}, {"ok": True, "lines": 2}, U8CoError(409, "state_mismatch", "单据已审核，不能修改")],
        "/v1/vouchers/load": [{"ok": True, "lines": [{}, {}]}],
        "/v1/vouchers/verify": verify if verify is not None else [{"ok": True}, {"ok": True}],
        "/v1/vouchers/delete": [{"ok": True, "deleted": True}],
    })


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(qr, "run_guarded", sql), mock.patch.object(qu, "run_guarded", sql):
        return qu.qm_reject_update(_env(client), _SPEC, _STEP)


_HAPPY_EDITS = [_E0, _E0, _E0, _E1, _E2, _E2]


class RegistrationTests(unittest.TestCase):
    def test_registered_with_op(self) -> None:
        (spec,) = qu.qm_reject_update_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertIn("/v1/vouchers/update", spec.routes)

    def test_conflicts_with_the_product_cycle(self) -> None:
        (spec,) = qu.qm_reject_update_cases()
        product = auto_tags(qr.qm_reject_cases()[1])
        self.assertTrue(conflicts(auto_tags(spec), product))

    def test_bridge_change_selects_the_case(self) -> None:
        self.assertIn(_NAME, select_changed(["co/bridge/src/QmRejEdit.cs"], list(known_names())))


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in qm_reject_update_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)

    def test_shapes(self) -> None:
        self.assertIn("WHERE s.ID = 100", first_line_sql("100"))
        self.assertIn("r.CDEFINE11", edit_state_sql("100"))
        for bad in ("0", "1;--"):
            with self.assertRaises(ValueError):
                edit_state_sql(bad)


class CycleTests(unittest.TestCase):
    def test_update_cycle_passes(self) -> None:
        client = _client()
        status, detail = _run(client, _sql([_CLEAN, _MADE, _CLEAN], list(_HAPPY_EDITS)))
        self.assertEqual(status, "PASS", detail)
        steps = [(route.rsplit("/", 1)[1], fields.get("action", ""), fields.get("dry_run", False))
                 for route, fields in client.calls]
        self.assertEqual(steps, [
            ("generate", "", True), ("generate", "", False), ("update", "", True), ("update", "", False),
            ("load", "", False), ("verify", "verify", False), ("update", "", False), ("verify", "unverify", False),
            ("delete", "", False),
        ])
        sent = client.calls[3][1]
        self.assertEqual(sent["head"], {"cDefine11": "DEMO-001"})
        self.assertEqual(sent["lines"], [
            {"op": "update", "line_id": 9001, "quantity": 0.5},
            {"op": "add", "quantity": 0.5, "cScrapDisCode": "Sys01", "cReasonCode": "01"},
        ])

    def test_unchanged_ufts_fails_and_cleans_up(self) -> None:
        client = _client(verify=[{"ok": True}])
        status, detail = _run(client, _sql([_CLEAN, _MADE], [_E0, _E0, _E0, _E0]))
        self.assertEqual(status, "FAIL")
        self.assertIn("UFTS", detail)
        self.assertIn("已清理", detail)
        self.assertEqual(client.calls[-1][0], "/v1/vouchers/delete")

    def test_verified_update_accepted_is_fail(self) -> None:
        client = _client(update=[{"ok": True, "dry_run": True}, {"ok": True, "lines": 2}, {"ok": True, "lines": 2}],
                         verify=[{"ok": True}, {"ok": True}])
        status, detail = _run(client, _sql([_CLEAN, _MADE], list(_HAPPY_EDITS)))
        self.assertEqual(status, "FAIL")
        self.assertIn("409", detail)

    def test_workflow_controlled_skips_the_verified_step(self) -> None:
        client = _client(verify=[U8CoError(409, "workflow_enabled", "请用 workflow/*")])
        wf = f"ROW:1|1|501|{_CODE}|356|QM06||1|2|1.0000\n"
        status, detail = _run(client, _sql([_CLEAN, wf, _CLEAN], [_E0, _E0, _E0, _E1]))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("跳过", detail)

    def test_no_second_dispose_is_na(self) -> None:
        client = _client()
        status, detail = _run(client, _sql([], [], other=""))
        self.assertEqual(status, "N/A")
        self.assertIn("QMSCRAPDISPOSE", detail)
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
