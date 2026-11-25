"""应收合并制单（arap_voucher_merge）的登记、分片、SQL 形状和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.client.u8co_client import U8CoError

from co.e2e.casespec import ops_run
from co.e2e.casespec import stock_plus as sp
from co.e2e.casespec import arap_voucher_merge as avm
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arap_voucher_merge_sql import gl_bad_sql, left_sql, marks_bad_sql, voucher_merge_samples
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "ar-voucher-merge"
_SPEC = CaseSpec(_NAME, "s")
_STEP = Step("custom")
_PZ = "AR0000000000042"
_ROUTES = ("/v1/vouchers/create", "/v1/vouchers/verify", "/v1/vouchers/delete", "/v1/arap/voucher", "/v1/arap/voucher/delete")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answers: dict[str, list]) -> None:
        self.answers = {key: list(val) for key, val in answers.items()}
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        got = self.answers[route].pop(0)
        if isinstance(got, U8CoError):
            raise got
        return got


class _Sql:
    """按 SQL 里的特征串依次回答（每个键一个队列），没有匹配就报错。"""

    def __init__(self, answers: list[tuple[str, list[str]]]) -> None:
        self.answers = [(key, list(rows)) for key, rows in answers]

    def __call__(self, _env: object, query: str, _label: str) -> str:
        for key, rows in self.answers:
            if key in query and rows:
                return rows.pop(0)
        raise AssertionError("没有准备回答：" + query[:80])


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="802", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag={})


def _merged() -> dict:
    bills = [{"type": "ar_receipt", "id": 11, "code": "SK1"}, {"type": "ar_receipt", "id": 12, "code": "SK2"}]
    lines = [{"entry": n, "bill_code": code} for n, code in enumerate(("SK1", "SK2", "SK1", "SK2"), start=1)]
    return {"ok": True, "pz_id": _PZ, "bills": bills, "lines": lines}


def _client(again: object = None) -> _Replay:
    refused = U8CoError(409, "state_mismatch", "凭证已生成，不能重复制单。")
    return _Replay({
        "/v1/vouchers/create": [{"ok": True, "id": 11}, {"ok": True, "id": 12}],
        "/v1/vouchers/verify": [{"ok": True}] * 4,
        "/v1/vouchers/delete": [{"ok": True, "deleted": True}] * 2,
        "/v1/arap/voucher": [_merged(), again if again is not None else refused],
        "/v1/arap/voucher/delete": [{"ok": True, "deleted": True}] * 2,
    })


def _sql(gl: str = "0", left: str = "0") -> _Sql:
    return _Sql([
        ("dbo.Department WHERE", ["ROW:1\n"]),
        ("dbo.code AS c", ["ROW:112201\n"]),
        ("ISNULL((SELECT b.cVouchID", ["ROW:SK1\n", "ROW:SK2\n"]),
        ("COUNT(DISTINCT CONVERT", [f"ROW:{gl}\n"]),
        ("d.cGLSign IS NULL", ["ROW:0\n"]),
        ("ISNULL(b.cPzID, N'') <> N''))", [f"ROW:{left}\n"]),
    ])


def _run(client: _Replay, sql: _Sql) -> tuple[str, str]:
    with mock.patch.object(sp, "run_guarded", sql):
        return avm.voucher_merge(_env(client), _SPEC, _STEP)


class VoucherMergeTests(unittest.TestCase):
    def test_registered_with_gl_tags(self) -> None:
        self.assertEqual(known_names().count(_NAME), 1)
        spec = avm.voucher_merge_cases()[0]
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertTrue({"gl", "new:gl:收", "new:ar_receipt"} <= auto_tags(spec))

    def test_missing_seed_archives_is_na(self) -> None:
        status, detail = _run(_Replay({}), _Sql([("dbo.Department WHERE", ["ROW:0\n"])]))
        self.assertEqual(status, "N/A")
        self.assertIn("种子", detail)

    def test_samples_pass_the_guard(self) -> None:
        rows = sample_sql()
        for name, query in voucher_merge_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn((name, query), rows)
        self.assertIn("g.coutid = N'SK2'", gl_bad_sql(_PZ, (("SK1", 60), ("SK2", 40))))
        self.assertIn("b.iID IN (11, 12)", marks_bad_sql(_PZ, ("11", "12"), ("SK1", "SK2")))
        for bad in (lambda: left_sql("GL1", ("1",)), lambda: left_sql(_PZ, ("0",)), lambda: gl_bad_sql(_PZ, (("SK'", 1),))):
            with self.assertRaises(ValueError):
                bad()

    def test_merge_then_cancel_and_cleanup(self) -> None:
        client = _client()
        status, detail = _run(client, _sql())
        self.assertEqual(status, "PASS", detail)
        merge = next(fields for route, fields in client.calls if route == "/v1/arap/voucher")
        self.assertEqual(merge["ids"], [{"type": "ar_receipt", "id": 11}, {"type": "ar_receipt", "id": 12}])
        self.assertEqual(merge["type"], "ar_receipt")
        tail = [(route, fields.get("action"), fields.get("id")) for route, fields in client.calls[-5:]]
        self.assertEqual(tail, [
            ("/v1/arap/voucher/delete", None, None),
            ("/v1/vouchers/verify", "unverify", 12),
            ("/v1/vouchers/delete", None, 12),
            ("/v1/vouchers/verify", "unverify", 11),
            ("/v1/vouchers/delete", None, 11),
        ])
        self.assertEqual({route for route, _fields in client.calls}, set(_ROUTES))

    def test_bad_voucher_still_cancels_and_fails(self) -> None:
        client = _client()
        status, detail = _run(client, _sql(gl="2"))
        self.assertEqual(status, "FAIL")
        self.assertIn("凭证问题数 2", detail)
        self.assertIn(("/v1/arap/voucher/delete", {"flag": "AR", "pz_id": _PZ}), [
            (route, {key: fields[key] for key in ("flag", "pz_id")}) for route, fields in client.calls
            if route == "/v1/arap/voucher/delete"
        ])

    def test_second_voucher_is_cleaned_and_fails(self) -> None:
        client = _client(again={"ok": True, "pz_id": "AR0000000000043"})
        status, detail = _run(client, _sql())
        self.assertEqual(status, "FAIL")
        self.assertIn("409", detail)
        dropped = [fields["pz_id"] for route, fields in client.calls if route == "/v1/arap/voucher/delete"]
        self.assertEqual(dropped, [_PZ, "AR0000000000043"])


if __name__ == "__main__":
    unittest.main()
