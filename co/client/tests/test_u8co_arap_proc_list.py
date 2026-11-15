"""应收 / 应付处理记录（/v1/arap/process/list）的请求正文和本地校验。只监听 127.0.0.1。"""

from __future__ import annotations

import json
import unittest
from typing import Any, Callable

from co.client.tests.test_u8co_update_close_gen import _AUTH, SECRET, _assert_signed, make_call, _Running
from co.client.u8co_arap_proc_list import (
    ARAP_PROC_LIST_ROUTE,
    ProcDigestQuery,
    ProcListQuery,
    proc_digest_fields,
    proc_list_fields,
)
from co.client.u8co_client import U8CoClient
from co.client.u8co_idem import WRITE_ROUTES


def _capture(case: unittest.TestCase, invoke: Callable[[U8CoClient], object]) -> tuple[dict[str, Any], str]:
    with _Running() as bridge:
        invoke(U8CoClient(bridge.base_url, SECRET, timeout=5))
        captured = bridge.capture
    _assert_signed(case, captured)
    sent = json.loads(captured["body"].decode("utf-8"))
    case.assertNotIn("password", sent)
    return sent, captured["path"]


class WireTests(unittest.TestCase):
    def test_list_minimal(self) -> None:
        sent, path = _capture(self, lambda client: client.arap_process_list(make_call(), ProcListQuery("AR")))
        self.assertEqual(path, "/u8co/v1/arap/process/list")
        self.assertEqual(list(sent), _AUTH + ["flag"])

    def test_list_incremental(self) -> None:
        query = ProcListQuery("AP", changed_since="30025", after=30100, limit=500, keys_only=True, open_only=False)
        sent, _path = _capture(self, lambda client: client.arap_process_list(make_call(), query))
        keys = ["flag", "changed_since", "after", "limit", "keys_only", "open_only"]
        self.assertEqual(list(sent), _AUTH + keys)
        self.assertEqual((sent["changed_since"], sent["after"], sent["open_only"]), ("30025", 30100, False))

    def test_digest(self) -> None:
        query = ProcDigestQuery("AR", fiscal_year=2026, periods=[9, 10], after="OVAfSFhBUjAwMDE", limit=1)
        sent, path = _capture(self, lambda client: client.arap_process_digest(make_call(), query))
        self.assertEqual(path, "/u8co/v1/arap/process/list")
        self.assertEqual(list(sent), _AUTH + ["flag", "digest", "fiscal_year", "periods", "after", "limit"])
        self.assertIs(sent["digest"], True)
        sent, _path = _capture(self, lambda client: client.arap_process_digest(make_call(), ProcDigestQuery("AP")))
        self.assertEqual(list(sent), _AUTH + ["flag", "digest"])

    def test_route_is_not_a_write(self) -> None:
        self.assertNotIn(ARAP_PROC_LIST_ROUTE, WRITE_ROUTES)


class LocalCheckTests(unittest.TestCase):
    def test_list_rejects(self) -> None:
        bad = (
            ProcListQuery("ar"),
            ProcListQuery("AR", changed_since="12a"),
            ProcListQuery("AR", changed_since="2147483648"),
            ProcListQuery("AR", changed_since=-1),
            ProcListQuery("AR", changed_since=True),
            ProcListQuery("AR", after="5"),
            ProcListQuery("AR", limit=0),
            ProcListQuery("AR", limit=501),
            ProcListQuery("AR", keys_only="true"),
        )
        for query in bad:
            with self.subTest(query=query), self.assertRaises(ValueError):
                proc_list_fields(make_call(), query)

    def test_digest_rejects(self) -> None:
        bad = (
            ProcDigestQuery("GL"),
            ProcDigestQuery("AR", fiscal_year=1999),
            ProcDigestQuery("AR", periods=[]),
            ProcDigestQuery("AR", periods=[13]),
            ProcDigestQuery("AR", periods=[9, 9]),
            ProcDigestQuery("AR", periods="9"),
            ProcDigestQuery("AR", after=5),
            ProcDigestQuery("AR", after=""),
            ProcDigestQuery("AR", limit=501),
        )
        for query in bad:
            with self.subTest(query=query), self.assertRaises(ValueError):
                proc_digest_fields(make_call(), query)


if __name__ == "__main__":
    unittest.main()
