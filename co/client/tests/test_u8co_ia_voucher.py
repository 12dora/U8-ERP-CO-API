"""存货核算制单、取消制单（/v1/ia/voucher、/v1/ia/voucher/delete）的请求正文、预演、幂等键和命令行解析。
只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import _AUTH, SECRET, _argv, _assert_signed, make_call, _Running
from co.client.u8co_cli import _parser
from co.client.u8co_cli_assist import KEY_COMMANDS, WRITE_COMMANDS
from co.client.u8co_cli_ia_voucher import _ask
from co.client.u8co_client import U8CoClient
from co.client.u8co_ia_voucher import IaVoucherAsk
from co.client.u8co_idem import WRITE_ROUTES

_PZ = "2026IA0000000000001"


class IaVoucherClientTests(unittest.TestCase):
    def _send(self, invoke: Callable[[U8CoClient], object]) -> tuple[dict[str, Any], dict[str, Any]]:
        with _Running() as bridge:
            invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
            captured = bridge.capture
        _assert_signed(self, captured)
        sent = json.loads(captured["body"].decode("utf-8"))
        self.assertNotIn("password", sent)
        return sent, captured

    def test_month_wire_body_minimal(self) -> None:
        sent, captured = self._send(lambda client: client.ia_voucher(make_call(), IaVoucherAsk(month=9)))
        self.assertEqual(captured["path"], "/u8co/v1/ia/voucher")
        self.assertEqual(list(sent), _AUTH + ["month"])

    def test_ids_wire_body_full(self) -> None:
        ask = IaVoucherAsk(
            ids=(("09", 1001), ("3201", 1002)), merge="all", sign="转", digest=" 文本A ", voucher_date="2026-09-30"
        )
        sent, _captured = self._send(lambda client: client.ia_voucher(make_call(), ask, idempotency_key="iv-1"))
        self.assertEqual(list(sent), _AUTH + ["ids", "merge", "sign", "digest", "voucher_date", "idempotency_key"])
        self.assertEqual(sent["ids"], [{"type": "09", "id": 1001}, {"type": "3201", "id": 1002}])
        self.assertEqual(sent["digest"], "文本A")

    def test_dry_and_delete(self) -> None:
        sent, _captured = self._send(lambda client: client.dry().ia_voucher(make_call(), IaVoucherAsk(month=9, types=("09",))))
        self.assertIs(sent["dry_run"], True)
        self.assertEqual(sent["types"], ["09"])
        sent, captured = self._send(lambda client: client.ia_voucher_delete(make_call(), _PZ))
        self.assertEqual(captured["path"], "/u8co/v1/ia/voucher/delete")
        self.assertEqual(list(sent), _AUTH + ["pz_id"])

    def test_rejects_bad_requests(self) -> None:
        client = U8CoClient("http://127.0.0.1/u8co", SECRET, timeout=5)
        eleven = tuple(("09", i) for i in range(1, 12))
        bad = (
            (IaVoucherAsk(), "month"),
            (IaVoucherAsk(ids=(("09", 1),), month=9), "month"),
            (IaVoucherAsk(month=13), "month"),
            (IaVoucherAsk(ids=(("sale_out", 1),)), "type"),
            (IaVoucherAsk(ids=(("09", 0),)), "id"),
            (IaVoucherAsk(ids=(("09", 1), ("09", 1))), "重复"),
            (IaVoucherAsk(ids=eleven), "ids"),
            (IaVoucherAsk(ids=(("09", 1),), types=("09",)), "types"),
            (IaVoucherAsk(month=9, merge="one"), "merge"),
            (IaVoucherAsk(month=9, voucher_date="2026/09/30"), "voucher_date"),
        )
        for ask, word in bad:
            with self.subTest(ask=ask), self.assertRaises(ValueError) as caught:
                client.ia_voucher(make_call(), ask)
            self.assertIn(word, str(caught.exception))
        with self.assertRaises(ValueError):
            client.ia_voucher_delete(make_call(), "AP0000000000001")

    def test_routes_and_commands_are_registered(self) -> None:
        self.assertIn("/v1/ia/voucher", WRITE_ROUTES)
        self.assertIn("/v1/ia/voucher/delete", WRITE_ROUTES)
        for name in ("ia-voucher", "ia-voucher-delete"):
            self.assertIn(name, WRITE_COMMANDS)
            self.assertIn(name, KEY_COMMANDS)

    def test_cli_parses_ids_and_month(self) -> None:
        parsed = _parser().parse_args(_argv("ia-voucher", "--id", "09:1001", "--id", "3201:1002", "--merge", "all", "--dry-run"))
        ask = _ask(parsed)
        self.assertEqual(ask.ids, (("09", 1001), ("3201", 1002)))
        self.assertEqual(ask.merge, "all")
        self.assertTrue(parsed.dry_run)
        parsed = _parser().parse_args(_argv("ia-voucher", "--month", "9", "--types", "09, 11"))
        self.assertEqual(_ask(parsed).types, ("09", "11"))
        parsed = _parser().parse_args(_argv("ia-voucher-delete", "--pz-id", _PZ))
        self.assertEqual(parsed.pz_id, _PZ)


if __name__ == "__main__":
    unittest.main()
