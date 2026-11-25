"""订单执行、单据追溯用例（report_trace）的挑选 SQL 和回放。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec.report_trace import (
    TRACE_OPS,
    qm_other_pick_sql,
    trace_cases,
    trace_exec,
    trace_graph,
    trace_pick_sql,
    trace_qm_other,
)
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_EXEC = "/v1/reports/order_execution"
_TRACE = "/v1/reports/doc_trace"
_PICK = "ROW:11|22\n"
_QM_PICK = "ROW:41|31\n"
_QM_CASE = "report-doc-trace-qm-other"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    """按路由回放：值是 dict 原样返回，是 U8CoError 就抛出。记录调用的路由和正文。"""

    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


def _env(client: _Replay) -> SimpleNamespace:
    operator = SimpleNamespace(acc="801", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28")


def _run(op, name: str, client: _Replay, sql_out: str) -> tuple[str, str]:
    spec = next(spec for spec in trace_cases() if spec.name == name)
    with mock.patch("co.e2e.casespec.report_trace.run_guarded", return_value=sql_out):
        return op(_env(client), spec, spec.steps[0])


def _sale_row(**extra) -> dict:
    row = {
        "id": 11, "code": "SO1", "line_id": 5, "qty": 10.0, "shipped_qty": 10.0, "out_qty": 10.0,
        "invoiced_qty": 0.0, "received_amount": 0.0, "open": True,
    }
    row.update(extra)
    return row


def _po_page(**extra) -> dict:
    row = {"id": 3, "open": True, "closed": False, "in_qty": 0.0, "paid_amount": 0.0}
    row.update(extra)
    return {"ok": True, "type": "purchase_order", "only_open": True, "items": [row], "next": None}


def _node(kind: str, doc: int, level: int) -> dict:
    return {"type": kind, "id": doc, "code": "C", "date": "2026-09-01", "state": "verified", "level": level}


def _edge(src: tuple[str, int], dst: tuple[str, int]) -> dict:
    return {"from_type": src[0], "from_id": src[1], "to_type": dst[0], "to_id": dst[1], "lines": 1}


def _graph(nodes: list[dict], edges: list[dict]) -> dict:
    return {"ok": True, "nodes": nodes, "edges": edges, "omitted": 0, "truncated": False}


_DOWN = _graph(
    [_node("sale_order", 11, 0), _node("dispatch", 22, 1), _node("sale_out", 33, 2)],
    [_edge(("sale_order", 11), ("dispatch", 22)), _edge(("dispatch", 22), ("sale_out", 33))],
)
_UP = _graph([_node("dispatch", 22, 0), _node("sale_order", 11, -1)], [_edge(("sale_order", 11), ("dispatch", 22))])
_MISSING = U8CoError(404, "not_found", "单据不存在")


class ReportTraceCaseTests(unittest.TestCase):
    def test_cases_and_ops(self) -> None:
        names = [spec.name for spec in trace_cases()]
        self.assertEqual(names, ["report-order-execution", "report-doc-trace", _QM_CASE])
        for spec in trace_cases():
            self.assertTrue(spec.requires_login)
            self.assertIn(spec.steps[0].sql_name, TRACE_OPS)
            self.assertEqual(spec.steps[0].kind, "custom")

    def test_pick_sql_passes_the_guard(self) -> None:
        sql = trace_pick_sql()
        self.assertEqual(sql_block_reason(sql), "")
        self.assertIn("ROW:", sql)

    def test_qm_other_pick_uses_inspect_autoid(self) -> None:
        sql = qm_other_pick_sql()
        self.assertEqual(sql_block_reason(sql), "")
        self.assertIn("s.AUTOID = c.INSPECTAUTOID", sql)
        self.assertNotIn("SOURCEAUTOID", sql)
        self.assertIn("N'QM15'", sql)
        self.assertIn("N'QM11'", sql)

    def test_qm_other_optional(self) -> None:
        spec = next(spec for spec in trace_cases() if spec.name == _QM_CASE)
        self.assertTrue(spec.optional)
        self.assertIn("rpt:trace", auto_tags(spec))


class ReportTraceReplayTests(unittest.TestCase):
    def test_exec_reads_the_order_and_open_purchase_lines(self) -> None:
        answers = {_EXEC: [{"ok": True, "type": "sale_order", "items": [_sale_row()], "next": None}, _po_page()]}
        client = _Replay(answers)
        status, detail = _run(trace_exec, "report-order-execution", client, _PICK)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(client.calls[0][1]["ids"], [11])
        self.assertEqual(client.calls[1][1]["limit"], 5)

    def test_exec_flags_closed_rows_in_open_list(self) -> None:
        answers = {_EXEC: [{"ok": True, "items": [_sale_row()]}, _po_page(closed=True)]}
        status, detail = _run(trace_exec, "report-order-execution", _Replay(answers), _PICK)
        self.assertEqual(status, "FAIL")
        self.assertIn("only_open", detail)

    def test_exec_flags_other_orders(self) -> None:
        answers = {_EXEC: [{"ok": True, "items": [_sale_row(id=12)]}]}
        status, detail = _run(trace_exec, "report-order-execution", _Replay(answers), _PICK)
        self.assertEqual(status, "FAIL")
        self.assertIn("别的订单", detail)

    def test_graph_down_up_and_missing(self) -> None:
        client = _Replay({_TRACE: [_DOWN, _UP, _MISSING]})
        status, detail = _run(trace_graph, "report-doc-trace", client, _PICK)
        self.assertEqual(status, "PASS", detail)
        sent = [fields for _route, fields in client.calls]
        self.assertEqual((sent[0]["type"], sent[0]["id"], sent[0]["direction"]), ("sale_order", 11, "down"))
        self.assertEqual((sent[1]["type"], sent[1]["id"], sent[1]["direction"]), ("dispatch", 22, "up"))
        self.assertEqual(sent[2]["id"], 2147483647)

    def test_graph_flags_dangling_edges(self) -> None:
        broken = _graph([_node("sale_order", 11, 0)], [_edge(("sale_order", 11), ("dispatch", 22))])
        status, detail = _run(trace_graph, "report-doc-trace", _Replay({_TRACE: [broken]}), _PICK)
        self.assertEqual(status, "FAIL")
        self.assertIn("不在图里", detail)

    def test_graph_requires_404_for_missing(self) -> None:
        client = _Replay({_TRACE: [_DOWN, _UP, _graph([], [])]})
        status, detail = _run(trace_graph, "report-doc-trace", client, _PICK)
        self.assertEqual(status, "FAIL")
        self.assertIn("404", detail)

    def test_nothing_picked_is_not_applicable(self) -> None:
        for op, name in ((trace_exec, "report-order-execution"), (trace_graph, "report-doc-trace")):
            status, _detail = _run(op, name, _Replay({}), "")
            self.assertEqual(status, "N/A")


_QM_UP = _graph([_node("qm_other_check", 41, 0), _node("qm_other_inspect", 31, -1)],
                [_edge(("qm_other_inspect", 31), ("qm_other_check", 41))])
_QM_DOWN = _graph([_node("qm_other_inspect", 31, 0), _node("qm_other_check", 41, 1)],
                  [_edge(("qm_other_inspect", 31), ("qm_other_check", 41))])


class TraceQmOtherReplayTests(unittest.TestCase):
    def test_up_down_and_missing(self) -> None:
        client = _Replay({_TRACE: [_QM_UP, _QM_DOWN, _MISSING]})
        status, detail = _run(trace_qm_other, _QM_CASE, client, _QM_PICK)
        self.assertEqual(status, "PASS", detail)
        sent = [fields for _route, fields in client.calls]
        self.assertEqual((sent[0]["type"], sent[0]["id"], sent[0]["direction"]), ("qm_other_check", 41, "up"))
        self.assertEqual((sent[1]["type"], sent[1]["id"], sent[1]["direction"]), ("qm_other_inspect", 31, "down"))
        self.assertEqual((sent[2]["type"], sent[2]["id"]), ("qm_other_check", 2147483647))

    def test_missing_inspect_upstream_fails(self) -> None:
        lonely = _graph([_node("qm_other_check", 41, 0)], [])
        status, detail = _run(trace_qm_other, _QM_CASE, _Replay({_TRACE: [lonely]}), _QM_PICK)
        self.assertEqual(status, "FAIL")
        self.assertIn("qm_other_inspect 31", detail)

    def test_reversed_edge_fails(self) -> None:
        reversed_up = _graph([_node("qm_other_check", 41, 0), _node("qm_other_inspect", 31, -1)],
                             [_edge(("qm_other_check", 41), ("qm_other_inspect", 31))])
        status, detail = _run(trace_qm_other, _QM_CASE, _Replay({_TRACE: [reversed_up]}), _QM_PICK)
        self.assertEqual(status, "FAIL")
        self.assertIn("边", detail)

    def test_nothing_picked_is_not_applicable(self) -> None:
        status, _detail = _run(trace_qm_other, _QM_CASE, _Replay({}), "")
        self.assertEqual(status, "N/A")


if __name__ == "__main__":
    unittest.main()
