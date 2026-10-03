"""契约 B2：退货单（sale_return，红字发货单）的客户端正文与命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _argv, _assert_signed, _JsonFile, _Running
from co.client.u8co_cli import _gen_draft, _parser
from co.client.u8co_client import U8Call, U8CoClient, VoucherGen
from co.client.u8co_kinds import GENERATE_SOURCES, KIND_NAMES, VERIFIABLE_KINDS, check_source

_LINES = [{"source_line_id": 41, "quantity": 2, "cWhCode": "02"}]


def _offline() -> U8CoClient:
    return U8CoClient("http://127.0.0.1:9/u8co", SECRET, timeout=5)


class SaleReturnTests(unittest.TestCase):
    def test_generate_from_dispatch_body(self) -> None:
        draft = VoucherGen("sale_return", {"cMemo": "退货", "invoiced": False}, _LINES, "dispatch")
        with _Running() as bridge:
            call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6)
            U8CoClient(bridge.base_url, SECRET, timeout=5).generate_voucher(call, draft)
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual(captured["path"], "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "source_type", "head", "lines"])
        self.assertEqual((sent["type"], sent["id"], sent["source_type"]), ("sale_return", 6, "dispatch"))
        self.assertEqual(sent["lines"], _LINES)
        self.assertIs(sent["head"]["invoiced"], False)

    def test_sources_and_kind_lists(self) -> None:
        self.assertEqual(GENERATE_SOURCES["sale_return"], ("dispatch", "sale_return_apply"))  # 另可参照退货申请单
        self.assertEqual(check_source("sale_return", "dispatch"), "dispatch")
        self.assertIn("sale_return", KIND_NAMES)
        self.assertIn("sale_return", VERIFIABLE_KINDS)
        call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6)
        for source in ("sale_order", "arrival"):
            with self.assertRaises(ValueError) as caught:
                _offline().generate_voucher(call, VoucherGen("sale_return", None, _LINES, source))
            self.assertIn("来源", str(caught.exception))

    def test_verify_and_delete_bodies(self) -> None:
        with _Running() as bridge:
            client = U8CoClient(bridge.base_url, SECRET, timeout=5)
            client.verify_voucher(U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6, "verify"), "sale_return")
            verified = json.loads(bridge.capture["body"].decode("utf-8"))
            client.delete_voucher(U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6), "sale_return")
            deleted = json.loads(bridge.capture["body"].decode("utf-8"))
        self.assertEqual((verified["type"], verified["action"]), ("sale_return", "verify"))
        self.assertEqual((deleted["type"], deleted["id"]), ("sale_return", 6))

    def test_close_is_refused(self) -> None:
        call = U8Call("801", "2026", "op001", PASSWORD, "2026-09-28", 6)
        with self.assertRaises(ValueError):
            _offline().close_voucher(call, "sale_return", "close")

    def test_cli_accepts_the_kind(self) -> None:
        for command, extra in (
            ("load", ()),
            ("delete", ()),
            ("verify", ("--action", "unverify")),
        ):
            parsed = _parser().parse_args(_argv(command, "--type", "sale_return", "--id", "6", *extra))
            self.assertEqual(parsed.type, "sale_return")
        with _JsonFile({"lines": _LINES}) as path:
            parsed = _parser().parse_args(
                _argv("generate", "--type", "sale_return", "--id", "6", "--file", path, "--source-type", "dispatch"),
            )
            self.assertEqual(_gen_draft(parsed).source_type, "dispatch")
            bare = _parser().parse_args(_argv("generate", "--type", "sale_return", "--id", "6", "--file", path))
            self.assertEqual(_gen_draft(bare).source_type, "")
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                _parser().parse_args(_argv("create", "--type", "sale_return", "--file", "x"))


if __name__ == "__main__":
    unittest.main()
