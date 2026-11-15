"""字段元数据 meta。要签名、请求体固定为 {}、不带口令；命令行不读口令。只监听 127.0.0.1。"""

from __future__ import annotations

import contextlib
import io
import json
import unittest
from typing import Any
from unittest import mock

from co.client.tests.test_u8co_update_close_gen import SECRET, _header, _K_MAC, _Running
from co.client.u8co_cli import _parser, main
from co.client.u8co_client import SignedRequest, U8CoClient, sign


class MetaWireTests(unittest.TestCase):
    def test_meta_is_signed_post_with_empty_object(self) -> None:
        with _Running() as bridge:
            payload = U8CoClient(bridge.base_url, SECRET, timeout=5).meta()
            captured = bridge.capture
        self.assertTrue(payload["ok"])
        self.assertEqual(captured["path"], "/u8co/v1/meta")
        self.assertEqual(captured["body"], b"{}")
        signed = SignedRequest(
            "POST",
            captured["path"],
            captured["body"],
            _header(captured["headers"], "X-U8co-Ts") or "",
            _header(captured["headers"], "X-U8co-Nonce") or "",
        )
        self.assertEqual(_header(captured["headers"], "X-U8co-Sig"), sign(_K_MAC, signed))
        self.assertNotIn(b"password", captured["body"])


class MetaCliTests(unittest.TestCase):
    def test_meta_command_needs_no_account(self) -> None:
        parsed = _parser().parse_args(["meta", "--base-url", "http://127.0.0.1/u8co"])
        self.assertEqual(parsed.command, "meta")
        self.assertFalse(hasattr(parsed, "acc"))

    def test_meta_command_does_not_read_a_password(self) -> None:
        stdout = io.StringIO()
        asked: list[int] = []

        def fake_meta(_self: U8CoClient) -> dict[str, Any]:
            return {"ok": True, "revision": "r1"}

        def fake_password() -> str:
            asked.append(1)
            return "x"

        with (
            mock.patch("co.client.u8co_cli._secret", return_value=SECRET),
            mock.patch("co.client.u8co_cli._password", fake_password),
            mock.patch("co.client.u8co_cli.U8CoClient.meta", fake_meta),
            contextlib.redirect_stdout(stdout),
        ):
            code = main(["meta", "--base-url", "http://127.0.0.1/u8co"])
        self.assertEqual(code, 0)
        self.assertEqual(asked, [])
        self.assertEqual(json.loads(stdout.getvalue())["revision"], "r1")


if __name__ == "__main__":
    unittest.main()
