"""销售发票参照多张发货单 generate_invoice_from_dispatches：正文（sources 代替 id + lines）、幂等键、本地校验和命令行。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import (
    PASSWORD,
    SECRET,
    _AUTH,
    _argv,
    _assert_signed,
    _JsonFile,
    _run_main,
    _Running,
)
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_invoice import InvoiceSources, invoice_body

_SOURCES = [
    {"id": 801, "lines": [{"source_line_id": 11, "quantity": 2, "cbdefine1": "2.15"}]},
    {"id": 802, "lines": [{"source_line_id": 21, "quantity": 1}, {"source_line_id": 22, "quantity": 0.5, "cmemo": "m"}]},
]


def _call() -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", None)


class InvoiceSourcesTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_body_has_sources_and_no_id_or_lines(self) -> None:
        draft = InvoiceSources(_SOURCES, {"cVouchType": "26"})
        path, sent = self._send(lambda client: client.generate_invoice_from_dispatches(_call(), draft))
        self.assertEqual(path, "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "head", "sources"])
        self.assertEqual(sent["type"], "sale_invoice")
        self.assertEqual(sent["sources"], _SOURCES)

    def test_source_type_dispatch_and_idempotency_key(self) -> None:
        draft = InvoiceSources(_SOURCES[:1], None, "dispatch")
        _path, sent = self._send(lambda client: client.generate_invoice_from_dispatches(_call(), draft, idempotency_key="inv-1"))
        self.assertEqual(sent["source_type"], "dispatch")
        self.assertEqual(sent["idempotency_key"], "inv-1")
        self.assertNotIn("head", sent)

    def test_local_checks(self) -> None:
        bad = (
            ([], "1 到 200"),
            ([{"id": 1}], "只能有 id 和 lines"),
            ([{"id": 0, "lines": [{"source_line_id": 1, "quantity": 1}]}], "正整数"),
            ([{"id": True, "lines": [{"source_line_id": 1, "quantity": 1}]}], "正整数"),
            ([{"id": 5, "lines": [{"source_line_id": 1, "quantity": 1}]}] * 2, "重复"),
            ([{"id": 5, "lines": []}], "lines"),
            ([{"id": i + 1, "lines": [{"source_line_id": i, "quantity": 1}] * 2} for i in range(101)], "合计"),
        )
        for sources, text in bad:
            with self.subTest(text=text), self.assertRaises(ValueError) as caught:
                invoice_body(_call(), InvoiceSources(sources))
            self.assertIn(text, str(caught.exception))
        with self.assertRaises(ValueError):
            invoice_body(_call(), InvoiceSources(_SOURCES, None, "sale_return"))

    def test_cli_sources_file(self) -> None:
        seen: dict[str, Any] = {}

        def _fake(self: U8CoClient, call: U8Call, draft: InvoiceSources) -> dict[str, Any]:
            seen["call"] = (call.doc_id, draft)
            return {"ok": True}

        with _JsonFile({"head": {"cMemo": "合开"}, "sources": _SOURCES}) as path:
            code = _run_main(
                _argv("generate", "--type", "sale_invoice", "--file", path), "generate_invoice_from_dispatches", _fake
            )
        self.assertEqual(code, 0)
        self.assertIsNone(seen["call"][0])
        self.assertEqual(seen["call"][1].sources, _SOURCES)
        self.assertEqual(seen["call"][1].head, {"cMemo": "合开"})

    def test_cli_sources_refuses_id_and_other_types(self) -> None:
        def _never(*_args: Any, **_kw: Any) -> dict[str, Any]:
            raise AssertionError("不应调用")

        with _JsonFile({"sources": _SOURCES}) as path:
            for extra in (("--type", "sale_invoice", "--id", "801"), ("--type", "dispatch")):
                with self.subTest(extra=extra), self.assertRaises(SystemExit):
                    _run_main(_argv("generate", *extra, "--file", path), "generate_invoice_from_dispatches", _never)
        with _JsonFile({"lines": [{"source_line_id": 1, "quantity": 1}]}) as path, self.assertRaises(SystemExit) as caught:
            _run_main(_argv("generate", "--type", "sale_invoice", "--file", path), "generate_voucher", _never)
        self.assertIn("--id", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
