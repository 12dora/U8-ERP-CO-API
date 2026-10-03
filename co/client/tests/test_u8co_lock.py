"""锁定、解锁的请求正文和命令行解析。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any

from co.client.tests.test_u8co_update_close_gen import (
    _AUTH,
    SECRET,
    _argv,
    _assert_signed,
    make_call,
    _run_main,
    _Running,
)
from co.client.u8co_cli import _parser
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_lock import LOCKABLE_KINDS


class LockTests(unittest.TestCase):
    def _send(self, kind: str, action: str) -> tuple[dict[str, Any], dict[str, Any]]:
        with _Running() as bridge:
            U8CoClient(bridge.base_url, SECRET, timeout=5).lock_voucher(make_call(3), kind, action)
            sent = json.loads(bridge.capture["body"].decode("utf-8"))
            _assert_signed(self, bridge.capture)
            captured = bridge.capture
        self.assertNotIn("password", sent)
        return sent, captured

    def test_wire_body(self) -> None:
        for kind in LOCKABLE_KINDS:
            for action in ("lock", "unlock"):
                sent, captured = self._send(kind, action)
                self.assertEqual(captured["path"], "/u8co/v1/vouchers/lock")
                self.assertEqual(list(sent), _AUTH + ["type", "id", "action"])
                self.assertEqual((sent["type"], sent["id"], sent["action"]), (kind, 3, action))

    def test_rejects_bad_kind_action_and_id(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError) as kind:
            client.lock_voucher(make_call(3), "dispatch", "lock")
        self.assertIn("不支持锁定", str(kind.exception))
        with self.assertRaises(ValueError):
            client.lock_voucher(make_call(3), "purchase_order", "lock")
        with self.assertRaises(ValueError) as action:
            client.lock_voucher(make_call(3), "sale_order", "close")
        self.assertIn("lock 或 unlock", str(action.exception))
        for doc_id in (None, 0, 2147483648):
            with self.assertRaises(ValueError):
                client.lock_voucher(make_call(doc_id), "sale_order", "lock")

    def test_parse_lock(self) -> None:
        parsed = _parser().parse_args(_argv("lock", "--type", "sale_order", "--id", "7", "--action", "unlock"))
        self.assertEqual((parsed.type, parsed.id, parsed.action), ("sale_order", 7, "unlock"))
        for bad in (
            ("--type", "dispatch", "--id", "1", "--action", "lock"),
            ("--type", "purchase_order", "--id", "1", "--action", "lock"),
            ("--type", "sale_order", "--id", "1", "--action", "close"),
            ("--type", "sale_order", "--action", "lock"),
        ):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                _parser().parse_args(_argv("lock", *bad))

    def test_main_wires_lock(self) -> None:
        seen: dict[str, Any] = {}

        def _lock(self: U8CoClient, call: U8Call, kind: str, action: str) -> dict[str, Any]:
            seen["lock"] = (call.doc_id, kind, action)
            return {"ok": True}

        code = _run_main(
            _argv("lock", "--type", "sale_order", "--id", "5", "--action", "lock"),
            "lock_voucher",
            _lock,
        )
        self.assertEqual(code, 0)
        self.assertEqual(seen["lock"], (5, "sale_order", "lock"))


if __name__ == "__main__":
    unittest.main()
