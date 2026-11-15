"""历史发货单表头修正 voucher_head_fix：正文、预演、幂等键、本地校验和命令行。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import PASSWORD, SECRET, _AUTH, _assert_signed, _argv, _run_main, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_client import U8Call, U8CoClient
from co.client.u8co_head_fix import HeadFix
from co.client.u8co_idem import WRITE_ROUTES

_ID = 1000000001
_FIELDS = {"addr_code": "02", "person_code": "P900002"}
_EXPECT = {"addr_code": "01", "person_code": "P900001"}
_UNDO = {
    "addr_code": {"rdrecord32": [900000002], "SaleBillVouch": [900000003]},
    "person_code": {"rdrecord32": [900000002], "SaleBillVouch": [900000003], "Ar_Detail": [11, 12]},
}


def _call(doc_id: int | None = _ID) -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", doc_id)


def _fix(**over: Any) -> HeadFix:
    values: dict[str, Any] = {"kind": "dispatch", "fields": dict(_FIELDS), "expect": dict(_EXPECT)}
    values.update(over)
    return HeadFix(**values)


class HeadFixTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_full_body(self) -> None:
        path, sent = self._send(lambda client: client.voucher_head_fix(_call(), _fix()))
        self.assertEqual(path, "/u8co/v1/vouchers/head_fix")
        self.assertEqual(list(sent), _AUTH + ["type", "id", "fields", "expect"])
        self.assertEqual((sent["type"], sent["id"]), ("dispatch", _ID))
        self.assertEqual(sent["fields"], _FIELDS)
        self.assertEqual(sent["expect"], _EXPECT)

    def test_single_field(self) -> None:
        fix = _fix(fields={"person_code": "P900002"}, expect={"person_code": "P900001"})
        _path, sent = self._send(lambda client: client.voucher_head_fix(_call(), fix))
        self.assertEqual(sent["fields"], {"person_code": "P900002"})

    def test_addr_code_may_be_cleared_and_expect_may_be_empty(self) -> None:
        clear = _fix(fields={"addr_code": ""}, expect={"addr_code": "02"})
        _path, sent = self._send(lambda client: client.voucher_head_fix(_call(), clear))
        self.assertEqual((sent["fields"], sent["expect"]), ({"addr_code": ""}, {"addr_code": "02"}))
        fill = _fix(expect={"addr_code": "", "person_code": ""})
        _path, sent = self._send(lambda client: client.voucher_head_fix(_call(), fill))
        self.assertEqual(sent["expect"], {"addr_code": "", "person_code": ""})

    def test_undo_is_sent_after_expect(self) -> None:
        _path, sent = self._send(lambda client: client.voucher_head_fix(_call(), _fix(undo=_UNDO)))
        self.assertEqual(list(sent), _AUTH + ["type", "id", "fields", "expect", "undo"])
        self.assertEqual(sent["undo"], _UNDO)
        _path, sent = self._send(lambda client: client.voucher_head_fix(_call(), _fix(undo={})))
        self.assertEqual(sent["undo"], {})
        _path, sent = self._send(lambda client: client.voucher_head_fix(_call(), _fix()))
        self.assertNotIn("undo", sent)

    def test_dry_and_keyed_clients(self) -> None:
        _path, sent = self._send(lambda client: client.dry().voucher_head_fix(_call(), _fix()))
        self.assertIs(sent["dry_run"], True)
        _path, sent = self._send(lambda client: client.keyed("fix-1").voucher_head_fix(_call(), _fix()))
        self.assertEqual(sent["idempotency_key"], "fix-1")
        _path, sent = self._send(lambda client: client.voucher_head_fix(_call(), _fix(), idempotency_key="fix-2"))
        self.assertEqual(sent["idempotency_key"], "fix-2")

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        bad = (
            (_call(None), _fix(), "id"),
            (_call(0), _fix(), "id"),
            (_call(), _fix(kind="sale_order"), "type"),
            (_call(), _fix(fields={}, expect={}), "fields"),
            (_call(), _fix(fields="x"), "fields"),
            (_call(), _fix(fields={"cMemo": "x"}, expect={"cMemo": "y"}), "cMemo"),
            (_call(), _fix(fields={"person_code": ""}, expect={"person_code": "P900001"}), "fields.person_code"),
            (_call(), _fix(fields={"person_code": "P" * 21}, expect={"person_code": "P900001"}), "fields.person_code"),
            (_call(), _fix(fields={"person_code": " P1"}, expect={"person_code": "P900001"}), "fields.person_code"),
            (_call(), _fix(fields={"addr_code": "A" * 31}, expect={"addr_code": "01"}), "fields.addr_code"),
            (_call(), _fix(fields={"addr_code": "0\n2"}, expect={"addr_code": "01"}), "fields.addr_code"),
            (_call(), _fix(fields={"addr_code": "02 "}, expect={"addr_code": "01"}), "fields.addr_code"),
            (_call(), _fix(fields={"addr_code": 2}, expect={"addr_code": "01"}), "fields.addr_code"),
            (_call(), _fix(expect={"addr_code": "01"}), "person_code"),
            (_call(), _fix(expect={}), "expect"),
            (_call(), _fix(expect={"addr_code": "01", "person_code": 1}), "expect.person_code"),
            (_call(), _fix(expect={"addr_code": None, "person_code": "P900001"}), "expect.addr_code"),
            (_call(), _fix(fields={"addr_code": "02"}, expect={"addr_code": "01", "person_code": "P"}), "person_code"),
        )
        bad += (
            (_call(), _fix(undo=[1]), "undo"),
            (_call(), _fix(undo={"cMemo": {"rdrecord32": [1]}}), "cMemo"),
            (_call(), _fix(fields={"addr_code": "02"}, expect={"addr_code": "01"}, undo=_UNDO), "person_code"),
            (_call(), _fix(undo={"addr_code": {"Ar_Detail": [1]}}), "Ar_Detail"),
            (_call(), _fix(undo={"addr_code": {"rdrecord16": [1]}}), "rdrecord16"),
            (_call(), _fix(undo={"addr_code": "x"}), "undo.addr_code"),
            (_call(), _fix(undo={"addr_code": {"rdrecord32": 1}}), "undo.addr_code.rdrecord32"),
            (_call(), _fix(undo={"addr_code": {"rdrecord32": [0]}}), "undo.addr_code.rdrecord32"),
            (_call(), _fix(undo={"addr_code": {"rdrecord32": [2147483648]}}), "undo.addr_code.rdrecord32"),
            (_call(), _fix(undo={"addr_code": {"rdrecord32": ["1"]}}), "undo.addr_code.rdrecord32"),
            (_call(), _fix(undo={"addr_code": {"rdrecord32": [True]}}), "undo.addr_code.rdrecord32"),
            (_call(), _fix(undo={"addr_code": {"rdrecord32": [1, 1]}}), "重复"),
            (_call(), _fix(undo={"addr_code": {"rdrecord32": list(range(1, 502))}}), "500"),
        )
        for call, fix, word in bad:
            with self.subTest(word=word, fields=fix.fields), self.assertRaises(ValueError) as caught:
                client.voucher_head_fix(call, fix)
            self.assertIn(word, str(caught.exception))

    def test_route_is_a_write(self) -> None:
        self.assertIn("/v1/vouchers/head_fix", WRITE_ROUTES)


class CliTests(unittest.TestCase):
    def _run(self, *extra: str) -> tuple[int, dict[str, Any]]:
        seen: dict[str, Any] = {}

        def _op(self: U8CoClient, call: U8Call, fix: HeadFix, **kw: object) -> dict[str, object]:
            del kw
            seen.update(call=call, fix=fix, dry=self._dry)
            return {"ok": True, "changed": True}

        argv = _argv("voucher-head-fix", "--type", "dispatch", "--id", str(_ID), *extra)
        return _run_main(argv, "voucher_head_fix", _op), seen

    def test_command_takes_dry_run_and_key(self) -> None:
        self.assertIn("voucher-head-fix", WRITE_COMMANDS)
        self.assertIn("voucher-head-fix", KEY_COMMANDS)
        parsed = _parser().parse_args(
            _argv(
                "voucher-head-fix", "--type", "dispatch", "--id", "7", "--addr-code", "", "--expect-addr-code", "02",
                "--dry-run", "--idempotency-key", "k1",
            )
        )
        self.assertEqual((parsed.type, parsed.id, parsed.addr_code, parsed.expect_addr_code), ("dispatch", 7, "", "02"))
        self.assertIsNone(parsed.person_code)
        self.assertTrue(parsed.dry_run)
        self.assertEqual(parsed.idempotency_key, "k1")

    def test_type_is_limited_to_dispatch(self) -> None:
        argv = _argv("voucher-head-fix", "--type", "sale_order", "--id", "7", "--addr-code", "02")
        with self.assertRaises(SystemExit):
            _parser().parse_args(argv)

    def test_main_builds_the_fix(self) -> None:
        code, seen = self._run(
            "--addr-code", "02", "--person-code", "P900002",
            "--expect-addr-code", "01", "--expect-person-code", "P900001", "--dry-run",
        )
        self.assertEqual(code, 0)
        self.assertEqual(seen["fix"], _fix())
        self.assertEqual(seen["call"].doc_id, _ID)
        self.assertTrue(seen["dry"])

    def test_main_allows_clearing_the_address(self) -> None:
        code, seen = self._run("--addr-code", "", "--expect-addr-code", "02")
        self.assertEqual(code, 0)
        self.assertEqual(seen["fix"], _fix(fields={"addr_code": ""}, expect={"addr_code": "02"}))
        self.assertFalse(seen["dry"])

    def test_main_allows_expecting_empty(self) -> None:
        code, seen = self._run("--person-code", "P900002", "--expect-person-code", "")
        self.assertEqual(code, 0)
        self.assertEqual(seen["fix"], _fix(fields={"person_code": "P900002"}, expect={"person_code": ""}))

    def _undo_file(self, data: object) -> str:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, handle.name)
        with handle:
            json.dump(data, handle)
        return handle.name

    def test_undo_file_is_passed_on(self) -> None:
        path = self._undo_file(_UNDO)
        code, seen = self._run(
            "--addr-code", "02", "--person-code", "P900002",
            "--expect-addr-code", "01", "--expect-person-code", "P900001", "--undo-file", path,
        )
        self.assertEqual(code, 0)
        self.assertEqual(seen["fix"], _fix(undo=_UNDO))
        code, seen = self._run("--addr-code", "02", "--expect-addr-code", "01")
        self.assertIsNone(seen["fix"].undo)

    def test_undo_file_must_be_a_json_object(self) -> None:
        for data in ([1], "x"):
            with self.subTest(data=data), self.assertRaises(SystemExit):
                self._run("--addr-code", "02", "--expect-addr-code", "01", "--undo-file", self._undo_file(data))
        with self.assertRaises(SystemExit):
            self._run("--addr-code", "02", "--expect-addr-code", "01", "--undo-file", "/nonexistent/undo.json")

    def test_incomplete_flags_are_refused(self) -> None:
        for extra in (
            (),
            ("--addr-code", "02"),
            ("--expect-addr-code", "01"),
            ("--person-code", "P900002"),
            ("--expect-person-code", "P900001"),
            ("--addr-code", "02", "--expect-person-code", "P900001"),
        ):
            with self.subTest(extra=extra), self.assertRaises(SystemExit):
                self._run(*extra)


if __name__ == "__main__":
    unittest.main()
