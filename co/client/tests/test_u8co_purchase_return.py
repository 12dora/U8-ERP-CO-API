"""契约 B3：采购退货单（purchase_return）的客户端正文与命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _gen_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen
from co.client.u8co_kinds import GENERATE_SOURCES, check_source

_LINES = [{"source_line_id": 31, "quantity": 2}]


def _offline() -> U8CoClient:
    return U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)


class PurchaseReturnTests(unittest.TestCase):
    def test_generate_from_arrival_body(self) -> None:
        draft = VoucherGen("purchase_return", {"cWhCode": "01"}, _LINES, "arrival")
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6)
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("purchase_return", 6, "arrival"))
        self.assertEqual(sent["lines"], _LINES)

    def test_sources(self) -> None:
        self.assertEqual(GENERATE_SOURCES["purchase_return"], ("arrival", "purchase_order"))
        self.assertEqual(check_source("purchase_return", "purchase_order"), "purchase_order")
        call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6)
        for source in ("purchase_in", "sale_order"):
            with self.assertRaises(ValueError) as caught:
                _offline().generate_voucher(call, VoucherGen("purchase_return", None, _LINES, source))
            self.assertIn("来源", str(caught.exception))

    def test_verify_and_delete_bodies(self) -> None:
        with _Running() as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            client.verify_voucher(U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6, "unverify"), "purchase_return")
            verified = json.loads(bridge.capture["body"].decode("utf-8"))
            client.delete_voucher(U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6), "purchase_return")
            deleted = json.loads(bridge.capture["body"].decode("utf-8"))
        self.assertEqual((verified["type"], verified["action"]), ("purchase_return", "unverify"))
        self.assertEqual((deleted["type"], deleted["id"]), ("purchase_return", 6))

    def test_create_update_close_are_refused(self) -> None:
        call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6)
        with self.assertRaises(ValueError):
            _offline().close_voucher(call, "purchase_return", "close")

    def test_cli_accepts_the_kind(self) -> None:
        for command, extra in (
            ("load", ()),
            ("delete", ()),
            ("verify", ("--action", "verify")),
        ):
            parsed = _parser().parse_args(_argv(command, "--type", "purchase_return", "--id", "6", *extra))
            self.assertEqual(parsed.type, "purchase_return")
        with _JsonFile({"lines": _LINES}) as path:
            parsed = _parser().parse_args(
                _argv("generate", "--type", "purchase_return", "--id", "6", "--file", path, "--source-type", "arrival"),
            )
            self.assertEqual(_gen_draft(parsed).source_type, "arrival")
            bare = _parser().parse_args(_argv("generate", "--type", "purchase_return", "--id", "6", "--file", path))
            self.assertEqual(_gen_draft(bare).source_type, "")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("create", "--type", "purchase_return", "--file", "x"))


if __name__ == "__main__":
    unittest.main()
