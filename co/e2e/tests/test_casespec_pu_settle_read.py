"""采购结算单只读（pu_settle_read）的登记、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import pu_settle_read as psr
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.pu_settle_read_sql import pu_settle_samples, settle_pick_sql
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "purchase-settle-read"
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")
_PICK = "ROW:300|000000000000123|V01|2|1|31|40|50|M-1\n"
_UFTS = "900"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _lines() -> list[dict]:
    return [{"ID": "31", "in_id": "40", "invoice_id": "50"}, {"ID": "32", "in_id": "41", "invoice_id": "50"}]


class _Replay:
    def __init__(self) -> None:
        state = {"verified": False, "line_count": 2, "accounted_lines": 1}
        self.load = {"ok": True, "code": "000000000000123", "state": state, "lines": _lines()}
        self.row = {"id": 300, "ven_code": "V01", "verified": 0, "verifier": None, "line_count": "2",
                    "accounted_lines": "1", "ufts": _UFTS}
        self.search = [{"id": 300}]
        self.verify = (400, {"ok": False, "code": "bad_request", "message": "该单据类型不支持直接审核"})
        self.calls: list[tuple[str, dict]] = []

    def _list(self, fields: dict) -> dict:
        since = fields.get("changed_since")
        if since is None:
            return {"ok": True, "items": [self.row]}
        hit = int(since) < int(_UFTS)
        return {"ok": True, "items": [{"id": 300, "code": "000000000000123", "ufts": _UFTS}] if hit else []}

    def call(self, route: str, fields: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(fields)))
        if route.endswith("/load"):
            return 200, self.load
        if route.endswith("/list"):
            return 200, self._list(fields)
        if route.endswith("/search"):
            return 200, {"ok": True, "items": self.search}
        return self.verify


def _env() -> SimpleNamespace:
    operator = SimpleNamespace(acc="803", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=object(), operator=operator, year="2026", today="2026-10-03", bag={})


def _run(client: _Replay, pick: str = _PICK) -> tuple[str, str]:
    def invoke(_client: object, route: str, fields: dict) -> tuple[int, dict]:
        return client.call(route, fields)

    def sql(_env: object, query: str, _label: str) -> str:
        if "FROM dbo.PurSettleVouch AS h" not in query:
            raise AssertionError("没有准备回答：" + query[:80])
        return pick

    with mock.patch.object(sp, "run_guarded", sql), mock.patch.object(sp, "invoke", invoke):
        return psr.pu_settle_read(_env(), _SPEC, _STEP)  # type: ignore[arg-type]


class PuSettleRegistryTests(unittest.TestCase):
    def test_registered_once_with_op_routes_and_list_tag(self) -> None:
        self.assertEqual([spec.name for spec in psr.pu_settle_cases()], [_NAME])
        self.assertEqual(known_names().count(_NAME), 1)
        spec = psr.pu_settle_cases()[0]
        self.assertTrue(spec.requires_login and spec.routes)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        tags = auto_tags(spec)
        self.assertIn("list:purchase_settle", tags)
        self.assertFalse(any(tag.startswith(("new:", "w:")) or tag == "stock" for tag in tags))

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in pu_settle_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
            self.assertIn("ROW:", query, name)
        self.assertIn("y.iBsID <> 0", settle_pick_sql())
        self.assertIn("ORDER BY h.PSVID DESC", settle_pick_sql())


class PuSettleReadTests(unittest.TestCase):
    def test_all_reads_agree(self) -> None:
        client = _Replay()
        status, detail = _run(client)
        self.assertEqual(status, "PASS", detail)
        routes = [route.rsplit("/", 1)[1] for route, _fields in client.calls]
        self.assertEqual(routes, ["load", "list", "list", "list", "search", "verify"])
        sinces = [fields.get("changed_since") for route, fields in client.calls if route.endswith("/list")]
        self.assertEqual(sinces, [None, "899", "900"])
        search = client.calls[4][1]
        self.assertEqual((search["code_like"], search["partner"], search["inventory"]), ("000000000000123", "V01", "M-1"))

    def test_wrong_link_fails(self) -> None:
        client = _Replay()
        client.load["lines"] = [{"ID": "31", "in_id": "40", "invoice_id": "51"}, {"ID": "32"}]
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("发票 50", detail)

    def test_verified_state_fails(self) -> None:
        client = _Replay()
        client.load["state"] = {"verified": True, "line_count": 2, "accounted_lines": 1}
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("state", detail)

    def test_list_row_mismatch_fails(self) -> None:
        client = _Replay()
        client.row["accounted_lines"] = "2"
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("accounted_lines", detail)

    def test_search_miss_fails(self) -> None:
        client = _Replay()
        client.search = [{"id": 301}]
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("查找没有 300", detail)

    def test_verify_accepted_fails(self) -> None:
        client = _Replay()
        client.verify = (200, {"ok": True})
        status, detail = _run(client)
        self.assertEqual(status, "FAIL")
        self.assertIn("应 400", detail)

    def test_nothing_to_pick_is_na_without_calls(self) -> None:
        client = _Replay()
        status, _detail = _run(client, pick="")
        self.assertEqual(status, "N/A")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
