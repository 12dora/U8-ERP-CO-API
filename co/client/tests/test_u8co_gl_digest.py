"""总账凭证摘要 gl_digest：发给桥的正文、字段顺序和本地校验。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _assert_signed, _Running
from co.client.tests.test_u8co_gl_arc import _call, _offline
from co.client.u8co_client import U8CoClient
from co.client.u8co_gl_digest import GL_DIGEST_ROUTE, GlDigestQuery


class GlDigestWireTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[str, dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        self.assertEqual(list(sent)[:5], _AUTH)
        return captured["path"], sent

    def test_defaults_send_only_auth(self) -> None:
        path, sent = self._send(lambda client: client.gl_digest(_call()))
        self.assertEqual(path, "/u8co" + GL_DIGEST_ROUTE)
        self.assertEqual(list(sent), _AUTH)

    def test_all_fields(self) -> None:
        query = GlDigestQuery(fiscal_year=2026, periods=(8, 9), after="9.3.120", limit=500, keys_only=True)
        _path, sent = self._send(lambda client: client.gl_digest(_call(), query))
        self.assertEqual(list(sent), _AUTH + ["fiscal_year", "periods", "after", "limit", "keys_only"])
        self.assertEqual(sent["periods"], [8, 9])
        self.assertIs(sent["keys_only"], True)

    def test_closed_periods_zero_is_sent(self) -> None:
        _path, sent = self._send(lambda client: client.gl_digest(_call(), GlDigestQuery(closed_periods=0)))
        self.assertEqual(list(sent), _AUTH + ["closed_periods"])
        self.assertEqual(sent["closed_periods"], 0)


class GlDigestRuleTests(unittest.TestCase):
    def _refused(self, query: GlDigestQuery, text: str) -> None:
        with self.assertRaises(ValueError) as caught:
            _offline().gl_digest(_call(), query)
        self.assertIn(text, str(caught.exception))

    def test_rules(self) -> None:
        self._refused(GlDigestQuery(fiscal_year=1899), "fiscal_year")
        self._refused(GlDigestQuery(periods=()), "periods")
        self._refused(GlDigestQuery(periods=(13,)), "periods")
        self._refused(GlDigestQuery(periods=(9, 9)), "重复")
        self._refused(GlDigestQuery(periods=(9,), closed_periods=1), "同时")
        self._refused(GlDigestQuery(closed_periods=13), "closed_periods")
        self._refused(GlDigestQuery(after="9.3"), "after")
        self._refused(GlDigestQuery(limit=501), "limit")


if __name__ == "__main__":
    unittest.main()
