"""销售订单合同号回写 voucher_so_contract：正文、预演、幂等键、本地校验和命令行。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _assert_signed, _argv, _run_main, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_errors import U8CoStateMismatch
from co.client.u8co_idem import WRITE_ROUTES
from co.client.u8co_so_contract import SoContract

_ID = 1000000001
_NO = "HT202611001"
_CUSTOMER_NO = "KH-2026-088"
_BLANK = {"contract_no": None, "customer_contract_no": None}


def _call(doc_id: int | None = _ID) -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", doc_id)


def _change(**over: Any) -> SoContract:
    values: dict[str, Any] = {"kind": "sale_order", "contract_no": _NO, "expect": dict(_BLANK)}
    values.update(over)
    return SoContract(**values)


class SoContractTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_minimal_body(self) -> None:
        path, sent = self._send(lambda client: client.voucher_so_contract(_call(), _change()))
        self.assertEqual(path, "/u8co/v1/vouchers/so_contract")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "contract_no", "expect"])
        self.assertEqual((sent["type"], sent["id"], sent["contract_no"]), ("sale_order", _ID, _NO))
        self.assertEqual(sent["expect"], _BLANK)

    def test_customer_contract_no_is_placed_before_expect(self) -> None:
        change = _change(customer_contract_no=_CUSTOMER_NO, expect={"contract_no": _CUSTOMER_NO, "customer_contract_no": ""})
        _path, sent = self._send(lambda client: client.voucher_so_contract(_call(), change))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "contract_no", "customer_contract_no", "expect"])
        self.assertEqual(sent["customer_contract_no"], _CUSTOMER_NO)
        self.assertEqual(sent["expect"], {"contract_no": _CUSTOMER_NO, "customer_contract_no": ""})

    def test_expect_keeps_raw_current_values(self) -> None:
        expect = {"contract_no": " 无 ", "customer_contract_no": "无"}
        _path, sent = self._send(lambda client: client.voucher_so_contract(_call(), _change(expect=expect)))
        self.assertEqual(sent["expect"], expect)

    def test_dry_and_keyed_clients(self) -> None:
        _path, sent = self._send(lambda client: client.dry().voucher_so_contract(_call(), _change()))
        self.assertIs(sent["dry_run"], True)
        _path, sent = self._send(lambda client: client.keyed("soc-1").voucher_so_contract(_call(), _change()))
        self.assertEqual(sent["idempotency_key"], "soc-1")
        _path, sent = self._send(lambda client: client.voucher_so_contract(_call(), _change(), idempotency_key="soc-2"))
        self.assertEqual(sent["idempotency_key"], "soc-2")

    def test_dry_run_with_a_key_is_refused(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError):
            client.dry().voucher_so_contract(_call(), _change(), idempotency_key="soc-3")

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad = (
            (_call(None), _change(), "id"),
            (_call(0), _change(), "id"),
            (_call(), _change(kind="dispatch"), "type"),
            (_call(), _change(contract_no=""), "contract_no"),
            (_call(), _change(contract_no="   "), "contract_no"),
            (_call(), _change(contract_no=None), "contract_no"),
            (_call(), _change(contract_no=5), "contract_no"),
            (_call(), _change(contract_no="无"), "contract_no"),
            (_call(), _change(contract_no=" HT1"), "contract_no"),
            (_call(), _change(contract_no="HT1 "), "contract_no"),
            (_call(), _change(contract_no="HT\n1"), "contract_no"),
            (_call(), _change(contract_no="J" * 121), "contract_no"),
            (_call(), _change(customer_contract_no=""), "customer_contract_no"),
            (_call(), _change(customer_contract_no="无"), "customer_contract_no"),
            (_call(), _change(customer_contract_no="K\t1"), "customer_contract_no"),
            (_call(), _change(customer_contract_no=7), "customer_contract_no"),
            (_call(), _change(expect="x"), "expect"),
            (_call(), _change(expect={}), "expect"),
            (_call(), _change(expect={"contract_no": None}), "customer_contract_no"),
            (_call(), _change(expect={"customer_contract_no": None}), "contract_no"),
            (_call(), _change(expect={**_BLANK, "cMemo": "x"}), "cMemo"),
            (_call(), _change(expect={"contract_no": 1, "customer_contract_no": None}), "expect.contract_no"),
            (_call(), _change(expect={"contract_no": "C" * 121, "customer_contract_no": None}), "expect.contract_no"),
            (_call(), _change(expect={"contract_no": "a\nb", "customer_contract_no": None}), "expect.contract_no"),
        )
        for call, change, word in bad:
            with self.subTest(word=word, change=change), self.assertRaises(ValueError) as caught:
                client.voucher_so_contract(call, change)
            self.assertIn(word, str(caught.exception))

    def test_route_is_a_write(self) -> None:
        self.assertIn("/v1/vouchers/so_contract", WRITE_ROUTES)

    def test_state_mismatch_current_is_exposed(self) -> None:
        error = U8CoStateMismatch(409, "state_mismatch", "合同号当前不是空")
        error.detail = {"current": {"contract_no": _NO, "customer_contract_no": None}}
        self.assertEqual(error.current, {"contract_no": _NO, "customer_contract_no": None})


class CliTests(unittest.TestCase):
    def _run(self, *extra: str) -> tuple[int, dict[str, Any]]:
        seen: dict[str, Any] = {}

        def _op(self: U8CoClient, call: U8Call, change: SoContract, **kw: object) -> dict[str, object]:
            del kw
            seen.update(call=call, change=change, dry=self._dry)
            return {"ok": True, "changed": True}

        argv = _argv("voucher-so-contract", "--type", "sale_order", "--id", str(_ID), *extra)
        return _run_main(argv, "voucher_so_contract", _op), seen

    def test_command_takes_dry_run_and_key(self) -> None:
        self.assertIn("voucher-so-contract", WRITE_COMMANDS)
        self.assertIn("voucher-so-contract", KEY_COMMANDS)
        parsed = _parser().parse_args(
            _argv(
                "voucher-so-contract", "--type", "sale_order", "--id", "7", "--contract-no", _NO,
                "--expect-contract-no", "", "--expect-customer-contract-no", "", "--dry-run", "--idempotency-key", "k1",
            )
        )
        self.assertEqual((parsed.type, parsed.id, parsed.contract_no), ("sale_order", 7, _NO))
        self.assertEqual((parsed.expect_contract_no, parsed.expect_customer_contract_no), ("", ""))
        self.assertIsNone(parsed.customer_contract_no)
        self.assertTrue(parsed.dry_run)
        self.assertEqual(parsed.idempotency_key, "k1")

    def test_type_is_limited_to_sale_order(self) -> None:
        argv = _argv(
            "voucher-so-contract", "--type", "dispatch", "--id", "7", "--contract-no", _NO,
            "--expect-contract-no", "", "--expect-customer-contract-no", "",
        )
        with self.assertRaises(SystemExit):
            _parser().parse_args(argv)

    def test_main_builds_the_change(self) -> None:
        code, seen = self._run(
            "--contract-no", _NO, "--customer-contract-no", _CUSTOMER_NO,
            "--expect-contract-no", _CUSTOMER_NO, "--expect-customer-contract-no", "", "--dry-run",
        )
        self.assertEqual(code, 0)
        expect = {"contract_no": _CUSTOMER_NO, "customer_contract_no": ""}
        self.assertEqual(seen["change"], _change(customer_contract_no=_CUSTOMER_NO, expect=expect))
        self.assertEqual(seen["call"].doc_id, _ID)
        self.assertTrue(seen["dry"])

    def test_main_without_customer_contract_no(self) -> None:
        code, seen = self._run("--contract-no", _NO, "--expect-contract-no", "无", "--expect-customer-contract-no", "")
        self.assertEqual(code, 0)
        expect = {"contract_no": "无", "customer_contract_no": ""}
        self.assertEqual(seen["change"], _change(expect=expect))
        self.assertIsNone(seen["change"].customer_contract_no)
        self.assertFalse(seen["dry"])

    def test_incomplete_flags_are_refused(self) -> None:
        for extra in (
            (),
            ("--contract-no", _NO),
            ("--contract-no", _NO, "--expect-contract-no", ""),
            ("--contract-no", _NO, "--expect-customer-contract-no", ""),
            ("--expect-contract-no", "", "--expect-customer-contract-no", ""),
        ):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self._run(*extra)


if __name__ == "__main__":
    unittest.main()
