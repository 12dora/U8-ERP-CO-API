"""订单执行、单据追溯（order_execution、doc_trace）：正文、本地校验和命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _argv, _assert_signed, _run_main, _Running
from co.client.tests.test_u8co_reports import _call, _offline
from co.client.u8co_cli import _parser
from co.client.u8co_cli_reports_trace import doc_trace_query, order_exec_query
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_reports_trace import DocTraceQuery, OrderExecQuery


class WireTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_order_execution(self) -> None:
        path, sent = self._send(lambda client: client.report_order_execution(_call(), OrderExecQuery("sale_order")))
        self.assertEqual(path, "/u8co/v1/reports/order_execution")
        self.assertEqual(list(sent), _AUTH + ["type"])
        by_ids = OrderExecQuery("purchase_order", ids=(7, 1000000001), only_open=False)
        _path, sent = self._send(lambda client: client.report_order_execution(_call(), by_ids))
        self.assertEqual(list(sent), _AUTH + ["type", "ids", "only_open"])
        self.assertEqual(sent["ids"], [7, 1000000001])
        self.assertIs(sent["only_open"], False)
        full = OrderExecQuery(
            "sale_order", code="SO1", date_from="2026-01-01", date_to="2026-09-30", partner="C001", only_open=True,
            after="opaque", limit=1000,
        )
        _path, sent = self._send(lambda client: client.report_order_execution(_call(), full))
        self.assertEqual(
            list(sent),
            _AUTH + ["type", "code", "date_from", "date_to", "partner", "only_open", "after", "limit"],
        )

    def test_doc_trace(self) -> None:
        path, sent = self._send(lambda client: client.report_doc_trace(_call(), DocTraceQuery("sale_order", 9)))
        self.assertEqual(path, "/u8co/v1/reports/doc_trace")
        self.assertEqual(list(sent), _AUTH + ["type", "id"])
        full = DocTraceQuery("qm_product_reject", 128, depth=1, direction="up", max_nodes=200)
        _path, sent = self._send(lambda client: client.report_doc_trace(_call(), full))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "depth", "direction", "max_nodes"])
        self.assertEqual(sent["id"], 128)


class ValidationTests(unittest.TestCase):
    def _refused(self, invoke: Callable[[U8CoClient], object], text: str) -> None:
        with self.assertRaises(ValueError) as caught:
            invoke(_offline())
        self.assertIn(text, str(caught.exception))

    def test_order_rules(self) -> None:
        cases = (
            (OrderExecQuery("dispatch"), "type"),
            (OrderExecQuery("sale_order", ids=()), "ids"),
            (OrderExecQuery("sale_order", ids=(0,)), "ids"),
            (OrderExecQuery("sale_order", ids=tuple(range(1, 102))), "ids"),
            (OrderExecQuery("sale_order", ids=(1,), code="SO1"), "ids"),
            (OrderExecQuery("sale_order", code="x" * 31), "code"),
            (OrderExecQuery("sale_order", date_from="2026-9-1"), "date_from"),
            (OrderExecQuery("sale_order", date_from="2026-09-02", date_to="2026-09-01"), "date_from"),
            (OrderExecQuery("sale_order", partner="a\tb"), "partner"),
            (OrderExecQuery("sale_order", limit=1001), "limit"),
        )
        for query, text in cases:
            self._refused(lambda client, q=query: client.report_order_execution(_call(), q), text)

    def test_trace_rules(self) -> None:
        cases = (
            (DocTraceQuery("transfer", 1), "type"),
            (DocTraceQuery("sale_order", 0), "id"),
            (DocTraceQuery("sale_order", 1, depth=4), "depth"),
            (DocTraceQuery("sale_order", 1, direction="left"), "direction"),
            (DocTraceQuery("sale_order", 1, max_nodes=201), "max_nodes"),
        )
        for query, text in cases:
            self._refused(lambda client, q=query: client.report_doc_trace(_call(), q), text)


class CliTests(unittest.TestCase):
    def test_parse_order_exec(self) -> None:
        argv = _argv(
            "report-order-exec", "--type", "purchase_order", "--id", "7", "--id", "8", "--only-open", "--limit", "5",
        )
        query = order_exec_query(_parser().parse_args(argv))
        self.assertEqual(query, OrderExecQuery("purchase_order", ids=(7, 8), only_open=True, limit=5))
        plain = order_exec_query(_parser().parse_args(_argv("report-order-exec", "--type", "sale_order")))
        self.assertEqual(plain, OrderExecQuery("sale_order"))

    def test_parse_doc_trace(self) -> None:
        argv = _argv("report-doc-trace", "--type", "arrival", "--id", "42", "--depth", "2", "--direction", "down")
        query = doc_trace_query(_parser().parse_args(argv))
        self.assertEqual(query, DocTraceQuery("arrival", 42, 2, "down", None))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-doc-trace", "--type", "arrival"))
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("report-doc-trace", "--type", "transfer", "--id", "1"))

    def test_main_wires_trace_reports(self) -> None:
        seen: dict[str, Any] = {}

        def _fake(name: str) -> Callable[..., dict[str, Any]]:
            def invoke(self: U8CoClient, call: U8Call, query: object = None) -> dict[str, Any]:
                seen[name] = query
                return {"ok": True}

            return invoke

        cases = (
            (_argv("report-order-exec", "--type", "sale_order"), "report_order_execution", OrderExecQuery("sale_order")),
            (
                _argv("report-doc-trace", "--type", "sale_order", "--id", "9"),
                "report_doc_trace",
                DocTraceQuery("sale_order", 9),
            ),
        )
        for argv, method, want in cases:
            self.assertEqual(_run_main(argv, method, _fake(method)), 0)
            self.assertEqual(seen[method], want)


if __name__ == "__main__":
    unittest.main()
