"""产品检验单合并检验 generate_merged_check：正文（source_ids 代替 id）、幂等键、本地校验和命令行。只监听 127.0.0.1。"""

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
from co.client.u8co_qm_merge import MergedCheck, merged_body

_HEAD = {"cCheckPersonCode": "P900001", "project_code": "DEMO-001", "items": [{"cChkItemCode": "I", "cChkGuideCode": "G"}]}
_LINES = [{"quantity": 2, "fDisQuantity": 1}, {}]


def _call() -> U8Call:
    return U8Call("801", "2026", "op001", PASSWORD, "2026-09-27", None)


class MergedCheckTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_body_has_source_ids_and_no_id(self) -> None:
        draft = MergedCheck([11, 12], _HEAD, _LINES)
        path, sent = self._send(lambda client: client.generate_merged_check(_call(), draft))
        self.assertEqual(path, "/u8co/v1/vouchers/generate")
        self.assertEqual(list(sent), _AUTH + ["type", "source_ids", "head", "lines"])
        self.assertEqual(sent["type"], "qm_product_check")
        self.assertEqual(sent["source_ids"], [11, 12])
        self.assertEqual(sent["head"], _HEAD)
        self.assertEqual(sent["lines"], _LINES)

    def test_lines_optional_and_idempotency_key(self) -> None:
        draft = MergedCheck([11, 12], {"cCheckPersonCode": "P900001"}, None, ["0", 1])
        _path, sent = self._send(lambda client: client.generate_merged_check(_call(), draft, idempotency_key="qm-1"))
        self.assertNotIn("lines", sent)
        self.assertEqual(sent["merge_rules"], ["0", 1])
        self.assertEqual(sent["idempotency_key"], "qm-1")

    def test_local_checks(self) -> None:
        bad = (
            MergedCheck([11]),
            MergedCheck([11, 11]),
            MergedCheck([11, 0]),
            MergedCheck([11, True]),
            MergedCheck(list(range(1, 22))),
            MergedCheck("11,12"),
            MergedCheck([11, 12], None, [{}]),
            MergedCheck([11, 12], None, None, []),
            MergedCheck([11, 12], None, None, ["a"]),
        )
        for draft in bad:
            with self.subTest(draft=draft), self.assertRaises(ValueError):
                merged_body(_call(), draft)

    def test_cli_source_ids_file(self) -> None:
        seen: dict[str, Any] = {}

        def _fake(self: U8CoClient, call: U8Call, draft: MergedCheck) -> dict[str, Any]:
            seen["call"] = (call.doc_id, draft)
            return {"ok": True}

        with _JsonFile({"head": _HEAD, "source_ids": [11, 12]}) as path:
            code = _run_main(_argv("generate", "--type", "qm_product_check", "--file", path), "generate_merged_check", _fake)
        self.assertEqual(code, 0)
        self.assertIsNone(seen["call"][0])
        self.assertEqual(seen["call"][1].source_ids, [11, 12])
        self.assertIsNone(seen["call"][1].lines)

    def test_cli_source_ids_refuses_id_other_types_and_keys(self) -> None:
        def _never(*_args: Any, **_kw: Any) -> dict[str, Any]:
            raise AssertionError("不应调用")

        cases = (
            ({"source_ids": [11, 12]}, ("--type", "qm_product_check", "--id", "7")),
            ({"source_ids": [11, 12]}, ("--type", "qm_incoming_check")),
            ({"source_ids": [11, 12], "sources": []}, ("--type", "qm_product_check")),
        )
        for data, extra in cases:
            with self.subTest(extra=extra), _JsonFile(data) as path, self.assertRaises(SystemExit):
                _run_main(_argv("generate", *extra, "--file", path), "generate_merged_check", _never)


if __name__ == "__main__":
    unittest.main()
