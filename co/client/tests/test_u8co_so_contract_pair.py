"""销售订单合同号成对设置 voucher_so_contract_pair（mode=pair）：正文、cascade、本地校验和命令行。只监听 127.0.0.1。"""

from __future__ import annotations

import unittest
from typing import Any

from co.client.tests.test_u8co_so_contract import _ID, SoContractTests, _call
from co.client.tests.test_u8co_update_close_gen import _AUTH, SECRET, _argv, _run_main
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_so_contract import SoContractPair

_NO = "KH-2026-0001"
_INTERNAL = "HT202601001"
_BLANK = {"contract_no": None, "internal_contract_no": None}


def _pair(**over: Any) -> SoContractPair:
    values: dict[str, Any] = {
        "kind": "sale_order",
        "contract_no": _NO,
        "internal_contract_no": _INTERNAL,
        "expect": dict(_BLANK),
    }
    values.update(over)
    return SoContractPair(**values)


class SoContractPairTests(unittest.TestCase):
    _send = SoContractTests._send

    def test_pair_body(self) -> None:
        path, sent = self._send(lambda client: client.voucher_so_contract_pair(_call(), _pair()))
        self.assertEqual(path, "/u8co/v1/vouchers/so_contract")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "mode", "contract_no", "internal_contract_no", "expect"])
        self.assertEqual((sent["mode"], sent["internal_contract_no"]), ("pair", _INTERNAL))
        self.assertEqual(sent["expect"], _BLANK)

    def test_cascade_is_sent_only_when_true(self) -> None:
        _path, sent = self._send(lambda client: client.voucher_so_contract_pair(_call(), _pair(cascade=True)))
        self.assertIs(sent["cascade"], True)
        _path, sent = self._send(lambda client: client.voucher_so_contract_pair(_call(), _pair()))
        self.assertNotIn("cascade", sent)

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad = (
            (_call(None), _pair(), "id"),
            (_call(), _pair(kind="dispatch"), "type"),
            (_call(), _pair(contract_no=""), "contract_no"),
            (_call(), _pair(internal_contract_no=None), "internal_contract_no"),
            (_call(), _pair(internal_contract_no="无"), "internal_contract_no"),
            (_call(), _pair(internal_contract_no="HT1 "), "internal_contract_no"),
            (_call(), _pair(cascade="true"), "cascade"),
            (_call(), _pair(expect={"contract_no": None, "customer_contract_no": None}), "customer_contract_no"),
            (_call(), _pair(expect={"contract_no": None}), "internal_contract_no"),
            (_call(), _pair(expect={"contract_no": None, "internal_contract_no": 3}), "expect.internal_contract_no"),
        )
        for call, change, word in bad:
            with self.subTest(word=word, change=change), self.assertRaises(ValueError) as caught:
                client.voucher_so_contract_pair(call, change)
            self.assertIn(word, str(caught.exception))


class PairCliTests(unittest.TestCase):
    def _run(self, *extra: str) -> tuple[int, dict[str, Any]]:
        seen: dict[str, Any] = {}

        def _op(self: U8CoClient, call: U8Call, change: SoContractPair, **kw: object) -> dict[str, object]:
            del kw
            seen.update(call=call, change=change, dry=self._dry)
            return {"ok": True, "changed": True}

        argv = _argv("voucher-so-contract", "--type", "sale_order", "--id", str(_ID), "--mode", "pair", *extra)
        return _run_main(argv, "voucher_so_contract_pair", _op), seen

    def test_main_builds_the_pair(self) -> None:
        code, seen = self._run(
            "--contract-no", _NO, "--internal-contract-no", _INTERNAL,
            "--expect-contract-no", _INTERNAL, "--expect-internal-contract-no", "", "--cascade", "--dry-run",
        )
        self.assertEqual(code, 0)
        expect = {"contract_no": _INTERNAL, "internal_contract_no": ""}
        self.assertEqual(seen["change"], _pair(expect=expect, cascade=True))
        self.assertTrue(seen["dry"])

    def test_pair_flags_are_checked(self) -> None:
        full = ("--contract-no", _NO, "--internal-contract-no", _INTERNAL, "--expect-contract-no", "")
        for extra in (
            full,
            (*full, "--expect-internal-contract-no", "", "--replace"),
            (*full, "--expect-internal-contract-no", "", "--customer-contract-no", "KH1"),
            (*full, "--expect-internal-contract-no", "", "--expect-customer-contract-no", ""),
        ):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self._run(*extra)

    def test_classic_refuses_pair_flags(self) -> None:
        base = ("--contract-no", _NO, "--expect-contract-no", "", "--expect-customer-contract-no", "")
        for extra in (("--cascade",), ("--internal-contract-no", _INTERNAL), ("--expect-internal-contract-no", "")):
            argv = _argv("voucher-so-contract", "--type", "sale_order", "--id", str(_ID), *base, *extra)
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                _run_main(argv, "voucher_so_contract", lambda *a, **k: {"ok": True})


if __name__ == "__main__":
    unittest.main()
