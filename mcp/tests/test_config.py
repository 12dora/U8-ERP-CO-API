"""配置解析和本地密钥文件的检查。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from u8co_mcp.auth import TokenProvider
from u8co_mcp.config import (
    ConfigError,
    TokenConfig,
    U8Config,
    load_config,
    parse_config,
    read_password,
    read_secret_file,
)

from tests.support import FakeSend, config_dict


class ParseTests(unittest.TestCase):
    def assertBad(self, data, fragment: str):
        with self.assertRaises(ConfigError) as ctx:
            parse_config(data)
        self.assertIn(fragment, str(ctx.exception))

    def test_valid_defaults(self):
        cfg = parse_config(config_dict(base_url="https://u8co.example.com/"))
        self.assertEqual(cfg.base_url, "https://u8co.example.com")
        self.assertFalse(cfg.read_only)
        self.assertEqual(cfg.timeout_s, 90.0)
        self.assertIsNone(cfg.ca_file)
        self.assertEqual((cfg.u8.acc, cfg.u8.operator, cfg.u8.year), ("801", "op001", "2026"))

    def test_example_file_parses(self):
        example = Path(__file__).resolve().parents[1] / "mcp.example.json"
        cfg = parse_config(json.loads(example.read_text(encoding="utf-8")))
        self.assertEqual(cfg.token.type, "client_credentials")
        self.assertEqual(cfg.token.client_secret_file, Path("~/.config/u8co/mcp-client-secret").expanduser())

    def test_https_required_except_loopback(self):
        self.assertBad(config_dict(base_url="http://u8co.example.com"), "https")
        parse_config(config_dict(base_url="http://127.0.0.1:8000"))
        parse_config(config_dict(base_url="http://localhost:8000"))

    def test_url_without_credentials_or_query(self):
        self.assertBad(config_dict(base_url="https://user:pw@u8co.example.com"), "用户名")
        self.assertBad(config_dict(base_url="https://u8co.example.com/?a=1"), "查询串")
        self.assertBad(config_dict(base_url="ftp://u8co.example.com"), "http(s)")

    def test_unknown_and_missing_keys(self):
        self.assertBad(config_dict(extra=1), "未知的键")
        data = config_dict()
        del data["u8"]
        self.assertBad(data, "缺少 u8")
        self.assertBad(config_dict(u8={"acc": "801"}), "缺少 operator")
        self.assertBad(config_dict(u8={"acc": "801", "operator": "op001", "password": "x"}), "未知的键")
        self.assertBad([], "JSON 对象")

    def test_u8_values(self):
        self.assertBad(config_dict(u8={"acc": "9", "operator": "op001"}), "三位")
        self.assertBad(config_dict(u8={"acc": "801", "operator": "op001", "year": "24"}), "四位")
        cfg = parse_config(config_dict(u8={"acc": "801", "operator": "op001", "year": 2026}))
        self.assertEqual(cfg.u8.year, "2026")

    def test_token_types(self):
        self.assertBad(config_dict(token={"type": "basic"}), "token.type")
        self.assertBad(config_dict(token={"type": "file"}), "缺少 path")
        self.assertBad(config_dict(token={"type": "env", "name": "BAD NAME"}), "环境变量名")
        self.assertBad(config_dict(token={"type": "env", "name": "X", "path": "/p"}), "未知的键")
        cc = {"type": "client_credentials", "token_url": "http://auth.example.com/t", "client_id": "a",
              "client_secret_file": "/s"}
        self.assertBad(config_dict(token=cc), "https")
        cfg = parse_config(config_dict(token={"type": "file", "path": "~/tok"}))
        self.assertEqual(cfg.token.path, Path("~/tok").expanduser())

    def test_scalars(self):
        self.assertBad(config_dict(read_only="yes"), "read_only")
        self.assertBad(config_dict(timeout_s=0), "timeout_s")
        self.assertBad(config_dict(timeout_s=True), "timeout_s")
        self.assertBad(config_dict(ca_file=""), "ca_file")
        self.assertEqual(parse_config(config_dict(timeout_s=30)).timeout_s, 30.0)

    def test_long_timeout(self):
        # 缺省 1000 与 timeout_s 中较大的；显式值不小于 timeout_s、不大于 7300。
        self.assertEqual(parse_config(config_dict()).long_timeout_s, 1000.0)
        self.assertEqual(parse_config(config_dict(long_timeout_s=7260)).long_timeout_s, 7260.0)
        self.assertEqual(parse_config(config_dict(timeout_s=30, long_timeout_s=30)).long_timeout_s, 30.0)
        for bad in (60, 7301, True, "1000"):
            self.assertBad(config_dict(long_timeout_s=bad), "long_timeout_s")


class FileTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def write(self, name: str, text: str, mode: int = 0o600) -> Path:
        path = Path(self.dir.name) / name
        path.write_text(text, encoding="utf-8")
        os.chmod(path, mode)
        return path

    @unittest.skipIf(os.name == "nt", "Windows 没有 POSIX 权限位")
    def test_mode_too_open(self):
        path = self.write("s", "secret", 0o644)
        with self.assertRaises(ConfigError) as ctx:
            read_secret_file(path, "客户端密钥")
        self.assertIn("chmod 600", str(ctx.exception))
        self.assertNotIn("secret\n", str(ctx.exception))

    def test_reads_and_strips(self):
        self.assertEqual(read_secret_file(self.write("s", "  value\n"), "令牌"), "value")

    def test_not_regular_missing_empty(self):
        with self.assertRaises(ConfigError):
            read_secret_file(Path(self.dir.name), "令牌")
        with self.assertRaises(ConfigError):
            read_secret_file(Path(self.dir.name) / "none", "令牌")
        with self.assertRaises(ConfigError):
            read_secret_file(self.write("e", "  \n"), "令牌")
        with self.assertRaises(ConfigError):
            read_secret_file(None, "令牌")

    def test_password_env_wins(self):
        u8 = U8Config(acc="801", operator="op001", password_file=self.write("p", "from-file"))
        with mock.patch.dict(os.environ, {"U8CO_MCP_PASSWORD": "from-env"}):
            self.assertEqual(read_password(u8), "from-env")
        with mock.patch.dict(os.environ, {"U8CO_MCP_PASSWORD": ""}):
            self.assertEqual(read_password(u8), "from-file")
            with self.assertRaises(ConfigError):
                read_password(U8Config(acc="801", operator="op001"))

    def test_load_config_from_env_path(self):
        path = self.write("mcp.json", json.dumps(config_dict()))
        with mock.patch.dict(os.environ, {"U8CO_MCP_CONFIG": str(path)}):
            self.assertEqual(load_config().u8.acc, "801")
        with self.assertRaises(ConfigError):
            load_config(self.write("bad.json", "{"))
        with self.assertRaises(ConfigError):
            load_config(Path(self.dir.name) / "missing.json")

    def test_token_file_reread_each_call(self):
        path = self.write("tok", "one")
        provider = TokenProvider(TokenConfig(type="file", path=path), FakeSend(), 5)
        self.assertEqual(provider.token(), "one")
        self.write("tok", "two")
        self.assertEqual(provider.token(), "two")
        self.assertFalse(provider.refreshable)


class ClientCredentialsExpiryTests(unittest.TestCase):
    def test_expiry_minus_skew(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret = Path(tmp) / "s"
            secret.write_text("cs", encoding="utf-8")
            os.chmod(secret, 0o600)
            cfg = TokenConfig(type="client_credentials", token_url="https://auth.example.com/t",
                              client_id="app-a", client_secret_file=secret)
            send = FakeSend()
            send.add("POST", "/t", body={"access_token": "a", "expires_in": 120})
            send.add("POST", "/t", body={"access_token": "b", "expires_in": 120})
            now = [1000.0]
            provider = TokenProvider(cfg, send, 5, clock=lambda: now[0])
            self.assertEqual(provider.token(), "a")
            now[0] += 59
            self.assertEqual(provider.token(), "a")
            now[0] += 2
            self.assertEqual(provider.token(), "b")
            self.assertNotIn(b"scope", send.calls[0].data)


if __name__ == "__main__":
    unittest.main()
