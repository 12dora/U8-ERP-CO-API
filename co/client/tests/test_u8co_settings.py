"""命令行的服务地址和密钥来源。只读临时目录，不连网络。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from co.client.u8co_settings import load_config, read_secret_file, resolve_base_url, resolve_secret

SECRET = "ab" * 32


class _Env:
    def __init__(self, **values: str) -> None:
        clean = {key: "" for key in ("U8CO_BASE_URL", "U8CO_SECRET", "U8CO_SECRET_FILE")}
        clean.update(values)
        self.patch = mock.patch.dict(os.environ, clean)

    def __enter__(self) -> None:
        self.patch.start()

    def __exit__(self, *args: object) -> None:
        self.patch.stop()


class BaseUrlTests(unittest.TestCase):
    def test_no_default_server(self) -> None:
        with _Env(), self.assertRaises(SystemExit) as caught:
            resolve_base_url(None, {})
        self.assertIn("U8CO_BASE_URL", str(caught.exception))

    def test_order(self) -> None:
        with _Env(U8CO_BASE_URL="http://192.0.2.10:18089/u8co"):
            self.assertEqual(resolve_base_url("http://198.51.100.10/u8co", {}), "http://198.51.100.10/u8co")
            self.assertEqual(resolve_base_url(None, {"base_url": "http://x/u8co"}), "http://192.0.2.10:18089/u8co")
        with _Env():
            self.assertEqual(resolve_base_url(None, {"base_url": "http://x/u8co"}), "http://x/u8co")


class SecretTests(unittest.TestCase):
    def test_env_then_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "secret.hex"
            path.write_text(SECRET + "\n", encoding="utf-8")
            path.chmod(0o600)
            with _Env(U8CO_SECRET_FILE=str(path)):
                self.assertEqual(resolve_secret({}), SECRET)
            with _Env(U8CO_SECRET="cd" * 32, U8CO_SECRET_FILE=str(path)):
                self.assertEqual(resolve_secret({}), "cd" * 32)
            with _Env():
                self.assertEqual(resolve_secret({"secret_file": str(path)}), SECRET)
            path.chmod(0o640)
            with self.assertRaises(SystemExit):
                read_secret_file(path)

    def test_missing_secret(self) -> None:
        with _Env(), self.assertRaises(SystemExit):
            resolve_secret({})


class ConfigFileTests(unittest.TestCase):
    def test_optional_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "client.json"
            self.assertEqual(load_config(path), {})
            path.write_text(json.dumps({"base_url": "http://192.0.2.10:18089/u8co", "other": 1}), encoding="utf-8")
            self.assertEqual(load_config(path), {"base_url": "http://192.0.2.10:18089/u8co"})
            path.write_text(json.dumps({"secret_file": 3}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_config(path)
            path.write_text("[", encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_config(path)


if __name__ == "__main__":
    unittest.main()
