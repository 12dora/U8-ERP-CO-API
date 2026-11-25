"""采购入库参照来料不良品处理单用例（purchase_in_qm_reject）的登记、互斥和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import purchase_in_qm_reject as pq
from co.e2e.casespec import qm_reject as qr
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.qm_reject_sql import reject_in_state_sql, reject_line_sql, reject_wh_sql
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.tests import support

_NAME = "purchase-in-from-qm-reject"
_SPEC = CaseSpec(_NAME, "s")
_STEP = Step("custom")
_CLEAN = "ROW:0|0|||||||0|0.0000\n"
_CODE = qr.INCOMING.prefix + "1"
_MADE = f"ROW:1|1|501|{_CODE}|355|QM05||0|1|2.0000\n"
_WF = f"ROW:1|1|501|{_CODE}|355|QM05||1|1|2.0000\n"
_NONE = "ROW:0|0.0000|||0\n"
_IN = "ROW:1|2.0000|601|来料不良品处理单|1\n"


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


def _sql(states: list[str], in_states: list[str], wh: str = "ROW:01\n", dispose: str = "ROW:Sys03\n") -> _Sql:
    return _Sql([
        ("FROM dbo.QMCHECKVOUCHER AS c\nWHERE", ["ROW:77|2.0000|I01\n"]),
        ("FROM dbo.Reason", ["ROW:01\n"]),
        ("FROM dbo.QMSCRAPDISPOSE", [dispose]),
        ("FROM dbo.voucheritems", [""]),
        ("FROM dbo.Warehouse", [wh]),
        ("FROM dbo.QMREJECTVOUCHERS AS s", ["ROW:9001|2.0000|0\n"]),
        ("FROM dbo.rdrecords01 AS b", in_states),
        ("OUTER APPLY", states),
    ])


def _refuse(text: str) -> U8CoError:
    return U8CoError(409, "state_mismatch", text)


_GEN = [
    {"ok": True, "id": 501}, _refuse("参照不良品处理单须一次入库全部处理后数量，本行应为 2"), _refuse("超过可生单数量"),
    {"ok": True, "id": 601}, _refuse("不良品处理单已入库完毕"),
]


def _client(gen: list | None = None, verify: list | None = None) -> _Replay:
    return _Replay({
        "/v1/vouchers/generate": gen if gen is not None else _GEN,
        "/v1/vouchers/verify": verify if verify is not None else [{"ok": True}, {"ok": True}],
        "/v1/vouchers/delete": [{"ok": True, "deleted": True}, {"ok": True, "deleted": True}],
    })


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(qr, "run_guarded", sql), mock.patch.object(pq, "run_guarded", sql):
        return pq.purchase_in_qm_reject(_env(client), _SPEC, _STEP)


class RegistrationTests(unittest.TestCase):
    def test_registered_with_op(self) -> None:
        (spec,) = pq.purchase_in_qm_reject_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertEqual(known_names().count(_NAME), 1)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))

    def test_conflicts_with_the_incoming_cycle_and_stock(self) -> None:
        (spec,) = pq.purchase_in_qm_reject_cases()
        tags = auto_tags(spec)
        self.assertTrue(conflicts(tags, auto_tags(qr.qm_reject_cases()[0])))
        self.assertIn("stock", tags)
        self.assertIn("chain:pu", tags)

    def test_bridge_change_selects_the_case(self) -> None:
        for path in ("co/bridge/src/StockGenRej.cs", "co/bridge/src/RejSrc.cs"):
            self.assertIn(_NAME, select_changed([path], list(known_names())), path)


class SqlTests(unittest.TestCase):
    def test_shapes(self) -> None:
        self.assertIn("WHERE s.ID = 100", reject_line_sql("100"))
        self.assertIn("b.iRejectIds = 100", reject_in_state_sql("100"))
        self.assertIn("FROM dbo.Warehouse", reject_wh_sql("I01"))
        for bad in ("0", "1;--"):
            with self.assertRaises(ValueError):
                reject_line_sql(bad)


class CycleTests(unittest.TestCase):
    def test_cycle_passes(self) -> None:
        client = _client()
        sql = _sql([_CLEAN, _MADE, _CLEAN], [_IN, _NONE])
        status, detail = _run(client, sql)
        self.assertEqual(status, "PASS", detail)
        steps = [
            (route.rsplit("/", 1)[1], fields.get("type"), fields.get("action", "")) for route, fields in client.calls
        ]
        self.assertEqual(steps, [
            ("generate", "qm_incoming_reject", ""), ("verify", "qm_incoming_reject", "verify"),
            ("generate", "purchase_in", ""), ("generate", "purchase_in", ""), ("generate", "purchase_in", ""),
            ("generate", "purchase_in", ""), ("delete", "purchase_in", ""),
            ("verify", "qm_incoming_reject", "unverify"), ("delete", "qm_incoming_reject", ""),
        ])
        gen = client.calls[0][1]
        self.assertEqual(gen["lines"][0]["cdiminvcode"], "I01")
        part, over, whole = client.calls[2][1], client.calls[3][1], client.calls[4][1]
        quantities = tuple(call["lines"][0]["quantity"] for call in (part, over, whole))
        self.assertEqual(quantities, (1.0, 3.0, 2.0))
        self.assertEqual(whole["source_type"], "qm_incoming_reject")
        self.assertEqual(whole["head"]["cWhCode"], "01")

    def test_flag_not_set_fails_and_cleans_up(self) -> None:
        client = _client(gen=_GEN[:4], verify=[{"ok": True}, {"ok": True}])
        bad = "ROW:1|2.0000|601|来料不良品处理单|0\n"
        client.answers["/v1/vouchers/delete"] = [{"ok": True, "deleted": True}] * 2
        status, detail = _run(client, _sql([_CLEAN, _MADE], [bad]))
        self.assertEqual(status, "FAIL")
        self.assertIn("BFLAG", detail)
        self.assertIn("已清理", detail)

    def test_timeout_after_commit_adopts_and_deletes_the_receipt(self) -> None:
        gen = _GEN[:3] + [U8CoError(504, "outcome_unknown", "x")]
        client = _client(gen=gen, verify=[{"ok": True}, {"ok": True}])
        status, detail = _run(client, _sql([_CLEAN, _MADE], [_IN]))
        self.assertEqual(status, "FAIL")
        self.assertIn("采购入库 601 已清理", detail)
        self.assertIn(("/v1/vouchers/delete", "purchase_in"), [(route, f.get("type")) for route, f in client.calls])

    def test_workflow_controlled_is_na(self) -> None:
        client = _client(gen=[{"ok": True, "id": 501}], verify=[U8CoError(409, "workflow_enabled", "x"), {"ok": True}])
        status, detail = _run(client, _sql([_CLEAN, _WF], []))
        self.assertEqual(status, "N/A")
        self.assertIn("审批流", detail)

    def test_no_warehouse_is_na(self) -> None:
        client = _client()
        status, detail = _run(client, _sql([], [], wh=""))
        self.assertEqual(status, "N/A")
        self.assertIn("仓库", detail)
        self.assertEqual(client.calls, [])

    def test_no_flow_two_dispose_is_na(self) -> None:
        client = _client()
        status, detail = _run(client, _sql([], [], dispose=""))
        self.assertEqual(status, "N/A")
        self.assertIn("QMSCRAPDISPOSE", detail)
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
