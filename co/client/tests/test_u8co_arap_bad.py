"""应收坏账处理（/v1/arap/bad_debt）的请求正文、预演、幂等键和本地校验。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import _AUTH, SECRET, _assert_signed, make_call, _Running
from co.client.u8co_arap_bad import ARAP_BAD_DEBT_ROUTE, BadDebt
from co.client.u8co_client import U8CoClient
from co.client.u8co_idem import WRITE_ROUTES

_KEY = "arap-bad-2026"
_LINES = [{"type": "26", "id": "0000000012", "line_id": 1001, "amount": 100}, {"type": "R0", "id": "34", "amount": 5}]


def _capture(case: unittest.TestCase, invoke: Callable[[U8CoClient], object]) -> tuple[dict[str, Any], str]:
    with _Running() as bridge:
        invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
        captured = bridge.capture
    _assert_signed(case, captured)
    sent = json.loads(captured["body"].decode("utf-8"))
    case.assertNotIn("password", sent)
    return sent, captured["path"]


class WireTests(unittest.TestCase):
    def test_occur(self) -> None:
        ask = BadDebt("occur", "C001", _LINES, currency="人民币", digest="客户破产", dept="D01", person="P01")
        sent, path = _capture(self, lambda client: client.arap_bad_debt(make_call(), ask))
        self.assertEqual(path, "/u8co/v1/arap/bad_debt")
        keys = ["action", "customer", "currency", "lines", "digest", "dept", "person"]
        self.assertEqual(list(sent), _AUTH + keys)
        self.assertEqual(sent["lines"], _LINES)

    def test_recover(self) -> None:
        ask = BadDebt("recover", "C001", receipt="0000000056", amount=300)
        sent, _path = _capture(self, lambda client: client.arap_bad_debt(make_call(), ask))
        self.assertEqual(list(sent), _AUTH + ["action", "customer", "receipt", "amount"])
        self.assertEqual((sent["receipt"], sent["amount"]), ("0000000056", 300))

    def test_provision_sends_only_the_action(self) -> None:
        sent, _path = _capture(self, lambda client: client.arap_bad_debt(make_call(), BadDebt("provision")))
        self.assertEqual(list(sent), _AUTH + ["action"])
        self.assertEqual(sent["date"], "2026-09-26")

    def test_dry_and_keyed_clients(self) -> None:
        sent, _path = _capture(self, lambda client: client.dry().arap_bad_debt(make_call(), BadDebt("provision")))
        self.assertIs(sent["dry_run"], True)
        self.assertNotIn("idempotency_key", sent)
        ask = BadDebt("occur", "C001", _LINES)
        sent, _path = _capture(self, lambda client: client.keyed(_KEY).arap_bad_debt(make_call(), ask))
        self.assertEqual(sent["idempotency_key"], _KEY)

    def test_cancel_and_voucher_take_bad_debt_numbers(self) -> None:
        no = "HZAR0000000014"
        sent, path = _capture(self, lambda client: client.arap_process_cancel(make_call(), "AR", no))
        self.assertEqual((path, sent["cancel_no"]), ("/u8co/v1/arap/process/cancel", no))

    def test_route_is_a_write_route(self) -> None:
        self.assertIn(ARAP_BAD_DEBT_ROUTE, WRITE_ROUTES)


class LocalCheckTests(unittest.TestCase):
    def _bad(self, ask: BadDebt, word: str) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError) as caught:
            client.arap_bad_debt(make_call(), ask)
        self.assertIn(word, str(caught.exception))

    def test_shapes(self) -> None:
        bad = (
            (BadDebt("write_off"), "action"),
            (BadDebt("occur", "C001"), "必须给 lines"),
            (BadDebt("occur", lines=_LINES), "必须给 customer"),
            (BadDebt("occur", "C001", []), "lines"),
            (BadDebt("occur", "C001", _LINES * 26), "lines"),
            (BadDebt("occur", "C001", [{"type": "26", "id": "1"}]), "amount"),
            (BadDebt("occur", "C001", _LINES, receipt="1"), "不能带 receipt"),
            (BadDebt("occur", "", _LINES), "customer"),
            (BadDebt("recover", "C001", receipt="1"), "必须给 amount"),
            (BadDebt("recover", "C001", receipt="1", amount=0), "amount"),
            (BadDebt("recover", "C001", receipt="1", amount=True), "amount"),
            (BadDebt("recover", "C001", receipt="1", amount=5, dept="D01"), "不能带 dept"),
            (BadDebt("provision", "C001"), "不能带 customer"),
            (BadDebt("provision", digest="计提"), "不能带 digest"),
        )
        for ask, word in bad:
            with self.subTest(ask=ask):
                self._bad(ask, word)


if __name__ == "__main__":
    unittest.main()
