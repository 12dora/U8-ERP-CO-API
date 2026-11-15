"""应收应付处理（/v1/arap/transfer、merge、red_offset、process/cancel、process/voucher、exchange_gain[/cancel]）的请求正文、
预演、幂等键和本地校验。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import _AUTH, SECRET, _assert_signed, make_call, _Running
from co.client.u8co_arap_proc import (
    ARAP_PROC_ROUTES,
    ArapMerge,
    ArapRedOffset,
    ArapTransfer,
    ExchangeGain,
    ProcVoucher,
)
from co.client.u8co_client import U8CoClient
from co.client.u8co_idem import WRITE_ROUTES

_KEY = "arap-proc-2026"
_AR = [{"type": "26", "id": "0000000012", "line_id": 1001, "amount": 100}]
_AP = [{"type": "P0", "id": "0000000034", "amount": 60}, {"type": "49", "id": "0000000035", "amount": 40}]


def _capture(case: unittest.TestCase, invoke: Callable[[U8CoClient], object]) -> tuple[dict[str, Any], str]:
    with _Running() as bridge:
        invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
        captured = bridge.capture
    _assert_signed(case, captured)
    sent = json.loads(captured["body"].decode("utf-8"))
    case.assertNotIn("password", sent)
    return sent, captured["path"]


def _client() -> U8CoClient:
    return U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)


class WireTests(unittest.TestCase):
    def test_transfer(self) -> None:
        ask = ArapTransfer("AR", "C001", "V001", _AR, _AP, currency="人民币", digest="应收冲应付")
        sent, path = _capture(self, lambda client: client.arap_transfer(make_call(), ask))
        self.assertEqual(path, "/u8co/v1/arap/transfer")
        keys = ["flag", "customer", "vendor", "currency", "ar_lines", "ap_lines", "digest"]
        self.assertEqual(list(sent), _AUTH + keys)
        self.assertEqual((sent["ar_lines"], sent["ap_lines"]), (_AR, _AP))

    def test_transfer_omits_unset_optionals(self) -> None:
        ask = ArapTransfer("AP", "C001", "V001", _AR, _AP)
        sent, _path = _capture(self, lambda client: client.arap_transfer(make_call(), ask))
        self.assertEqual(list(sent), _AUTH + ["flag", "customer", "vendor", "ar_lines", "ap_lines"])

    def test_merge_sends_from_and_to(self) -> None:
        lines = [{"type": "R0", "id": "0000000056"}, {"type": "26", "id": "0000000012", "line_id": 7, "amount": 5.5}]
        sent, path = _capture(self, lambda client: client.arap_merge(make_call(), ArapMerge("AR", "C001", "C002", lines)))
        self.assertEqual(path, "/u8co/v1/arap/merge")
        self.assertEqual(list(sent), _AUTH + ["flag", "from", "to", "lines"])
        self.assertEqual((sent["from"], sent["to"], sent["lines"]), ("C001", "C002", lines))

    def test_red_offset(self) -> None:
        red = [{"type": "27", "id": "0000000090", "amount": 30}]
        blue = [{"type": "26", "id": "0000000012", "line_id": 1001, "amount": 30}]
        ask = ArapRedOffset("AR", "C001", red, blue)
        sent, path = _capture(self, lambda client: client.arap_red_offset(make_call(), ask))
        self.assertEqual(path, "/u8co/v1/arap/red_offset")
        self.assertEqual(list(sent), _AUTH + ["flag", "partner", "red", "blue"])

    def test_process_cancel(self) -> None:
        sent, path = _capture(self, lambda client: client.arap_process_cancel(make_call(), "AR", "YCFAP000000000001"))
        self.assertEqual(path, "/u8co/v1/arap/process/cancel")
        self.assertEqual(list(sent), _AUTH + ["flag", "cancel_no"])
        self.assertEqual(sent["cancel_no"], "YCFAP000000000001")

    def test_process_voucher(self) -> None:
        ask = ProcVoucher("AR", ["SYRAR000000000003", "SYRAR000000000004"], voucher_date="2026-09-30", pl_code="660399")
        sent, path = _capture(self, lambda client: client.arap_process_voucher(make_call(), ask))
        self.assertEqual(path, "/u8co/v1/arap/process/voucher")
        self.assertEqual(list(sent), _AUTH + ["flag", "cancel_nos", "voucher_date", "pl_code"])

    def test_process_voucher_cash_items(self) -> None:
        ask = ProcVoucher("AR", ["PJTAR000000000001"], cash_items={" 660399 ": "07"})
        sent, _path = _capture(self, lambda client: client.arap_process_voucher(make_call(), ask))
        self.assertEqual(list(sent), _AUTH + ["flag", "cancel_nos", "cash_items"])
        self.assertEqual(sent["cash_items"], {"660399": "07"})

    def test_exchange_gain(self) -> None:
        ask = ExchangeGain("AP", "美元", rate=7.1, partners=["V001"], settle_cleared=False)
        sent, path = _capture(self, lambda client: client.arap_exchange_gain(make_call(), ask))
        self.assertEqual(path, "/u8co/v1/arap/exchange_gain")
        self.assertEqual(list(sent), _AUTH + ["flag", "currency", "rate", "partners", "settle_cleared"])
        self.assertIs(sent["settle_cleared"], False)
        sent, _path = _capture(self, lambda client: client.arap_exchange_gain(make_call(), ExchangeGain("AR", "美元")))
        self.assertEqual(list(sent), _AUTH + ["flag", "currency"])

    def test_exchange_gain_cancel_by_numbers_or_date(self) -> None:
        nos = ["SYPAP000000000001"]
        sent, path = _capture(self, lambda client: client.arap_exchange_gain_cancel(make_call(), "AP", nos))
        self.assertEqual(path, "/u8co/v1/arap/exchange_gain/cancel")
        self.assertEqual(list(sent), _AUTH + ["flag", "cancel_nos"])
        sent, _path = _capture(self, lambda client: client.arap_exchange_gain_cancel(make_call(), "AP"))
        self.assertEqual(list(sent), _AUTH + ["flag"])
        self.assertEqual(sent["date"], "2026-09-26")

    def test_dry_and_keyed_clients(self) -> None:
        no = "FCYAR000000000002"
        sent, _path = _capture(self, lambda client: client.dry().arap_process_cancel(make_call(), "AP", no))
        self.assertIs(sent["dry_run"], True)
        self.assertNotIn("idempotency_key", sent)
        ask = ArapMerge("AP", "V001", "V002", [{"type": "P0", "id": "0000000034"}])
        sent, _path = _capture(self, lambda client: client.keyed(_KEY).arap_merge(make_call(), ask))
        self.assertEqual(sent["idempotency_key"], _KEY)

    def test_routes_are_write_routes(self) -> None:
        self.assertEqual(len(ARAP_PROC_ROUTES), 7)
        for route in ARAP_PROC_ROUTES:
            self.assertIn(route, WRITE_ROUTES)


class LocalCheckTests(unittest.TestCase):
    def _bad(self, invoke: Callable[[U8CoClient], object], word: str) -> None:
        with self.assertRaises(ValueError) as caught:
            invoke(_client())
        self.assertIn(word, str(caught.exception))

    def test_transfer_shapes(self) -> None:
        bad = (
            (ArapTransfer("XX", "C001", "V001", _AR, _AP), "flag"),
            (ArapTransfer("AR", "", "V001", _AR, _AP), "customer"),
            (ArapTransfer("AR", "C001", "V001", [], _AP), "ar_lines"),
            (ArapTransfer("AR", "C001", "V001", _AR * 51, _AP), "ar_lines"),
            (ArapTransfer("AR", "C001", "V001", _AR, [{"type": "01", "id": "1"}]), "amount"),
            (ArapTransfer("AR", "C001", "V001", _AR, [{"type": "01", "id": "1", "amount": -1}]), "amount"),
            (ArapTransfer("AR", "C001", "V001", _AR, [{"type": "01", "id": "1", "amount": True}]), "amount"),
            (ArapTransfer("AR", "C001", "V001", _AR, [{"type": "01", "id": "1", "amount": 1, "x": 1}]), "只能有"),
            (ArapTransfer("AR", "C001", "V001", _AR, [{"type": "01", "id": "1", "line_id": 0, "amount": 1}]),
             "line_id"),
            (ArapTransfer("AR", "C001", "V001", _AR, _AP, currency=""), "currency"),
        )
        for ask, word in bad:
            with self.subTest(ask=ask):
                self._bad(lambda client, one=ask: client.arap_transfer(make_call(), one), word)

    def test_merge_and_red_offset_shapes(self) -> None:
        self._bad(lambda client: client.arap_merge(make_call(), ArapMerge("AR", "C001", "c001", _AR)), "同一个")
        self._bad(lambda client: client.arap_merge(make_call(), ArapMerge("AR", "C001", "C002", "R0")), "lines")
        self._bad(lambda client: client.arap_red_offset(make_call(), ArapRedOffset("AR", "C001", _AR, [])), "blue")
        self._bad(lambda client: client.arap_red_offset(make_call(), ArapRedOffset("AR", None, _AR, _AR)), "partner")

    def test_process_shapes(self) -> None:
        self._bad(lambda client: client.arap_process_cancel(make_call(), "AR", "YCFAP"), "cancel_no")
        self._bad(lambda client: client.arap_process_cancel(make_call(), "ar", "YCFAP1"), "flag")
        self._bad(lambda client: client.arap_process_voucher(make_call(), ProcVoucher("AR", [])), "cancel_nos")
        self._bad(lambda client: client.arap_process_voucher(make_call(), ProcVoucher("AR", ["BZAR1", "BZAR1"])), "重复")
        self._bad(lambda client: client.arap_process_voucher(make_call(), ProcVoucher("AR", ["bzar1"])), "处理号")
        many = [f"BZAR{n}" for n in range(51)]
        self._bad(lambda client: client.arap_process_voucher(make_call(), ProcVoucher("AR", many)), "1 到 50")
        for items in ({}, ["660399"], {"660399": 7}, {"660399": " "}, {"6603 99": "07"}, {str(n): "07" for n in range(21)}):
            with self.subTest(items=items):
                ask = ProcVoucher("AR", ["PJTAR1"], cash_items=items)
                self._bad(lambda client, one=ask: client.arap_process_voucher(make_call(), one), "cash_items")

    def test_exchange_gain_shapes(self) -> None:
        self._bad(lambda client: client.arap_exchange_gain(make_call(), ExchangeGain("AR", "")), "currency")
        self._bad(lambda client: client.arap_exchange_gain(make_call(), ExchangeGain("AR", "美元", rate=0)), "rate")
        self._bad(lambda client: client.arap_exchange_gain(make_call(), ExchangeGain("AR", "美元", partners=[])), "partners")
        self._bad(
            lambda client: client.arap_exchange_gain(make_call(), ExchangeGain("AR", "美元", settle_cleared=1)), "布尔"
        )
        self._bad(lambda client: client.arap_exchange_gain_cancel(make_call(), "AR", []), "cancel_nos")
        self._bad(lambda client: client.arap_exchange_gain_cancel(make_call(), "AR", "SYRAR1"), "cancel_nos")


if __name__ == "__main__":
    unittest.main()
