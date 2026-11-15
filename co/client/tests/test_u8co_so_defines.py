"""销售订单表头自定义项写入 voucher_so_defines：正文、cascade、预演、幂等键、本地校验和命令行。只监听 127.0.0.1。"""

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
from co.client.u8co_so_defines import SoDefines

_ID = 1000000001
_COMMAND = "voucher-so-defines"


def _call(doc_id: int | None = _ID) -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", doc_id)


def _change(**over: Any) -> SoDefines:
    values: dict[str, Any] = {"kind": "sale_order", "fields": {"cdefine1": "DEMO-001"}, "expect": {"cdefine1": None}}
    values.update(over)
    return SoDefines(**values)


class SoDefinesTests(unittest.TestCase):
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
        path, sent = self._send(lambda client: client.voucher_so_defines(_call(), _change()))
        self.assertEqual(path, "/u8co/v1/vouchers/so_defines")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "fields", "expect"])
        self.assertEqual((sent["type"], sent["id"]), ("sale_order", _ID))
        self.assertEqual(sent["fields"], {"cdefine1": "DEMO-001"})
        self.assertEqual(sent["expect"], {"cdefine1": None})

    def test_keys_are_lowercased_and_expect_follows_fields_order(self) -> None:
        change = _change(fields={"cDefine2": "文本A", "CDEFINE1": "DEMO-001"}, expect={"cdefine1": "", "CDefine2": "文本B"})
        _path, sent = self._send(lambda client: client.voucher_so_defines(_call(), change))
        self.assertEqual(sent["fields"], {"cdefine2": "文本A", "cdefine1": "DEMO-001"})
        self.assertEqual(list(sent["expect"]), ["cdefine2", "cdefine1"])
        self.assertEqual(sent["expect"], {"cdefine2": "文本B", "cdefine1": ""})

    def test_empty_values_and_cascade_are_sent_only_when_given(self) -> None:
        change = _change(empty_values=("-", "N/A"), cascade=True)
        _path, sent = self._send(lambda client: client.voucher_so_defines(_call(), change))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "fields", "expect", "empty_values", "cascade"])
        self.assertEqual(sent["empty_values"], ["-", "N/A"])
        self.assertIs(sent["cascade"], True)
        _path, sent = self._send(lambda client: client.voucher_so_defines(_call(), _change(cascade=False)))
        self.assertNotIn("cascade", sent)
        self.assertNotIn("empty_values", sent)

    def test_expect_keeps_raw_current_values(self) -> None:
        expect = {"cdefine1": " 文本B "}
        _path, sent = self._send(lambda client: client.voucher_so_defines(_call(), _change(expect=expect)))
        self.assertEqual(sent["expect"], expect)

    def test_dry_and_keyed_clients(self) -> None:
        _path, sent = self._send(lambda client: client.dry().voucher_so_defines(_call(), _change()))
        self.assertIs(sent["dry_run"], True)
        _path, sent = self._send(lambda client: client.keyed("sod-1").voucher_so_defines(_call(), _change()))
        self.assertEqual(sent["idempotency_key"], "sod-1")
        _path, sent = self._send(lambda client: client.voucher_so_defines(_call(), _change(), idempotency_key="sod-2"))
        self.assertEqual(sent["idempotency_key"], "sod-2")

    def test_dry_run_with_a_key_is_refused(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        with self.assertRaises(ValueError):
            client.dry().voucher_so_defines(_call(), _change(), idempotency_key="sod-3")

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad = (
            (_call(None), _change(), "id"),
            (_call(0), _change(), "id"),
            (_call(), _change(kind="dispatch"), "type"),
            (_call(), _change(fields="x"), "fields"),
            (_call(), _change(fields={}, expect={}), "fields"),
            (_call(), _change(fields={"cdefine17": "x"}, expect={"cdefine17": None}), "cdefine17"),
            (_call(), _change(fields={"cdefine01": "x"}, expect={"cdefine01": None}), "cdefine01"),
            (_call(), _change(fields={"cMemo": "x"}, expect={"cMemo": None}), "cMemo"),
            (_call(), _change(fields={"cdefine1": "x", "CDEFINE1": "y"}), "重复"),
            (_call(), _change(fields={"cdefine1": ""}), "fields.cdefine1"),
            (_call(), _change(fields={"cdefine1": "   "}), "fields.cdefine1"),
            (_call(), _change(fields={"cdefine1": None}), "fields.cdefine1"),
            (_call(), _change(fields={"cdefine1": 5}), "fields.cdefine1"),
            (_call(), _change(fields={"cdefine1": " DEMO-001"}), "fields.cdefine1"),
            (_call(), _change(fields={"cdefine1": "DEMO\n001"}), "fields.cdefine1"),
            (_call(), _change(fields={"cdefine1": "J" * 121}), "fields.cdefine1"),
            (_call(), _change(fields={"cdefine1": "-"}, empty_values=("-",)), "empty_values"),
            (_call(), _change(expect="x"), "expect"),
            (_call(), _change(expect={}), "cdefine1"),
            (_call(), _change(expect={"cdefine1": None, "cdefine2": None}), "cdefine2"),
            (_call(), _change(expect={"cdefine1": 1}), "expect.cdefine1"),
            (_call(), _change(expect={"cdefine1": "C" * 121}), "expect.cdefine1"),
            (_call(), _change(expect={"cdefine1": "a\nb"}), "expect.cdefine1"),
            (_call(), _change(empty_values="-"), "empty_values"),
            (_call(), _change(empty_values=tuple(str(n) for n in range(9))), "empty_values"),
            (_call(), _change(empty_values=("",)), "empty_values[0]"),
            (_call(), _change(empty_values=("E" * 21,)), "empty_values[0]"),
            (_call(), _change(empty_values=(1,)), "empty_values[0]"),
            (_call(), _change(cascade="true"), "cascade"),
        )
        for call, change, word in bad:
            with self.subTest(word=word, change=change), self.assertRaises(ValueError) as caught:
                client.voucher_so_defines(call, change)
            self.assertIn(word, str(caught.exception))

    def test_route_is_a_write(self) -> None:
        self.assertIn("/v1/vouchers/so_defines", WRITE_ROUTES)

    def test_state_mismatch_current_is_exposed(self) -> None:
        error = U8CoStateMismatch(409, "state_mismatch", "表头与 expect 不符")
        error.detail = {"current": {"cdefine1": "DEMO-002", "cdefine2": None}}
        self.assertEqual(error.current, {"cdefine1": "DEMO-002", "cdefine2": None})


class CliTests(unittest.TestCase):
    def _run(self, *extra: str) -> tuple[int, dict[str, Any]]:
        seen: dict[str, Any] = {}

        def _op(self: U8CoClient, call: U8Call, change: SoDefines, **kw: object) -> dict[str, object]:
            del kw
            seen.update(call=call, change=change, dry=self._dry)
            return {"ok": True, "changed": True}

        argv = _argv(_COMMAND, "--type", "sale_order", "--id", str(_ID), *extra)
        return _run_main(argv, "voucher_so_defines", _op), seen

    def test_command_takes_dry_run_and_key(self) -> None:
        self.assertIn(_COMMAND, WRITE_COMMANDS)
        self.assertIn(_COMMAND, KEY_COMMANDS)
        parsed = _parser().parse_args(
            _argv(
                _COMMAND, "--type", "sale_order", "--id", "7", "--set", "cdefine1=DEMO-001",
                "--expect", "cdefine1=", "--dry-run", "--idempotency-key", "k1",
            )
        )
        self.assertEqual((parsed.type, parsed.id), ("sale_order", 7))
        self.assertEqual((parsed.set, parsed.expect), (["cdefine1=DEMO-001"], ["cdefine1="]))
        self.assertEqual((parsed.empty_value, parsed.cascade), ([], False))
        self.assertTrue(parsed.dry_run)
        self.assertEqual(parsed.idempotency_key, "k1")

    def test_type_is_limited_to_sale_order(self) -> None:
        argv = _argv(_COMMAND, "--type", "dispatch", "--id", "7", "--set", "cdefine1=x", "--expect", "cdefine1=")
        with self.assertRaises(SystemExit):
            _parser().parse_args(argv)

    def test_main_builds_the_change(self) -> None:
        code, seen = self._run(
            "--set", "cdefine1=DEMO-001", "--set", "CDefine2=文本A=B",
            "--expect", "cdefine1=", "--expect", "cdefine2=文本B",
            "--empty-value", "-", "--empty-value", "N/A", "--cascade", "--dry-run",
        )
        self.assertEqual(code, 0)
        fields = {"cdefine1": "DEMO-001", "cdefine2": "文本A=B"}
        expect = {"cdefine1": "", "cdefine2": "文本B"}
        self.assertEqual(seen["change"], _change(fields=fields, expect=expect, empty_values=("-", "N/A"), cascade=True))
        self.assertEqual(seen["call"].doc_id, _ID)
        self.assertTrue(seen["dry"])

    def test_empty_expect_means_currently_empty(self) -> None:
        code, seen = self._run("--set", "cdefine2=文本A", "--expect", "cdefine2=")
        self.assertEqual(code, 0)
        self.assertEqual(seen["change"], _change(fields={"cdefine2": "文本A"}, expect={"cdefine2": ""}))
        self.assertFalse(seen["dry"])

    def test_incomplete_flags_are_refused(self) -> None:
        for extra in (
            (),
            ("--set", "cdefine1=DEMO-001"),
            ("--set", "cdefine1=DEMO-001", "--set", "cdefine2=文本A", "--expect", "cdefine1="),
            ("--set", "cdefine1=DEMO-001", "--expect", "cdefine1=", "--expect", "cdefine2="),
            ("--expect", "cdefine1="),
            ("--set", "cdefine1", "--expect", "cdefine1="),
            ("--set", "=x", "--expect", "cdefine1="),
            ("--set", "cdefine1=a", "--set", "CDEFINE1=b", "--expect", "cdefine1="),
        ):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self._run(*extra)


if __name__ == "__main__":
    unittest.main()
