"""档案列表 keys_only：只在 true 时发送，CLI --keys-only。"""

from __future__ import annotations

import unittest

from co.client.tests.test_u8co_update_close_gen import _AUTH, _argv
from co.client.tests import test_u8co_gl_arc as gl_arc
from co.client.u8co_cli import _parser
from co.client.u8co_gl_arc import ArcQuery


class ArcKeysWireTests(unittest.TestCase):
    _send = gl_arc.WireTests._send

    def test_keys_only_sent_when_true(self) -> None:
        query = ArcQuery("customer", limit=500, keys_only=True)
        path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), query))
        self.assertEqual(path, "/u8co/v1/archives/list")
        self.assertEqual(list(sent), _AUTH + ["archive", "limit", "keys_only"])
        self.assertIs(sent["keys_only"], True)

    def test_keys_only_omitted_by_default(self) -> None:
        _path, sent = self._send(lambda client: client.arc_list(gl_arc._call(), ArcQuery("person")))
        self.assertNotIn("keys_only", sent)


class ArcKeysCliTests(unittest.TestCase):
    def test_keys_only_flag(self) -> None:
        listing = _parser().parse_args(_argv("arc-list", "--archive", "customer", "--keys-only"))
        self.assertTrue(listing.keys_only)
        plain = _parser().parse_args(_argv("arc-list", "--archive", "customer"))
        self.assertFalse(plain.keys_only)


if __name__ == "__main__":
    unittest.main()
