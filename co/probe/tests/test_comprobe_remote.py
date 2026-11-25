# comprobe 建目录脚本：先锁 e2e 目录，再在里面建 comprobe 目录。不连接服务器。
from __future__ import annotations

import unittest

from co.e2e import remote
from co.e2e.tests import support
from co.probe.comprobe_remote import ensure_body, probe_root


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class EnsureBodyTests(unittest.TestCase):
    def test_locks_root_before_probe_dir(self) -> None:
        body = ensure_body()
        self.assertTrue(body.startswith(remote.ensure_body()))
        lock = body.index("Lock-Dir $root")
        make = body.index("[System.IO.Path]::GetFullPath('" + probe_root() + "')")
        self.assertLess(lock, make)
        self.assertIn("/inheritance:r", body)
        self.assertIn("/setowner '*S-1-5-32-544'", body)

    def test_probe_dir_inside_locked_root(self) -> None:
        body = ensure_body()
        root = "C:\\ProgramData\\U8Co\\u8co-e2e"
        self.assertEqual(probe_root(), root + "\\comprobe")
        self.assertIn("$root = [System.IO.Path]::GetFullPath('" + root + "')", body)
        self.assertIn("comprobe 目录不在 e2e 目录里", body)
        for sub in ("", "\\src", "\\out"):
            self.assertIn("'" + root + "\\comprobe" + sub + "'", body)
        self.assertNotIn("@PROBE@", body)
        self.assertNotIn("@ROOT@", body)
        self.assertNotIn("@PROTECTED@", body)


if __name__ == "__main__":
    unittest.main()
