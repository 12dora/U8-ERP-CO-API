"""总账取消记账 gl_unpost：发给桥的正文、字段顺序和本地校验。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import SECRET, _AUTH, _assert_signed, _Running
from co.client.tests.test_u8co_gl_arc import _call, _offline
from co.client.u8co_client import U8CoClient
from co.client.u8co_gl_unpost import GL_UNPOST_ROUTE, GlUnpostCheck
from co.client.u8co_idem import WRITE_ROUTES


class GlUnpostWireTests(unittest.TestCase):
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
        path, sent = self._send(lambda client: client.gl_unpost(_call()))
        self.assertEqual(path, "/u8co" + GL_UNPOST_ROUTE)
        self.assertEqual(list(sent), _AUTH)

    def test_check_fields(self) -> None:
        check = GlUnpostCheck(fiscal_year=2026, period=9, vouchers=(("转", 3), ("收", 12)))
        _path, sent = self._send(lambda client: client.gl_unpost(_call(), check))
        self.assertEqual(list(sent), _AUTH + ["fiscal_year", "period", "vouchers"])
        self.assertEqual(sent["vouchers"], [{"sign": "转", "no": 3}, {"sign": "收", "no": 12}])

    def test_is_a_write_route(self) -> None:
        self.assertIn(GL_UNPOST_ROUTE, WRITE_ROUTES)


class GlUnpostRuleTests(unittest.TestCase):
    def _refused(self, check: GlUnpostCheck, text: str) -> None:
        with self.assertRaises(ValueError) as caught:
            _offline().gl_unpost(_call(), check)
        self.assertIn(text, str(caught.exception))

    def test_rules(self) -> None:
        self._refused(GlUnpostCheck(fiscal_year=1899), "fiscal_year")
        self._refused(GlUnpostCheck(period=13), "period")
        self._refused(GlUnpostCheck(vouchers=(("转", 3),)), "period")
        self._refused(GlUnpostCheck(period=9, vouchers=(("转", 3), ("转", 3))), "重复")
        self._refused(GlUnpostCheck(period=9, vouchers=(("转", 0),)), "no")
        self._refused(GlUnpostCheck(period=9, vouchers=(("转账凭", 3),)), "sign")


if __name__ == "__main__":
    unittest.main()
