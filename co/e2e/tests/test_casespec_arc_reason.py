"""原因码写入用例 `archive-reason-write` 的登记、分线和一整轮的调用顺序。不连接服务器。"""

from __future__ import annotations

import unittest
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import arc_reason as reason
from co.e2e.casespec.catalog import known_names
from co.e2e.tests import support

_NAME = "archive-reason-write"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class ReasonCatalogTests(unittest.TestCase):
    def test_case_registered(self) -> None:
        (spec,) = reason.reason_cases()
        self.assertEqual(spec.name, _NAME)
        self.assertIn(_NAME, known_names())
        self.assertTrue(spec.requires_login)
        self.assertEqual(spec.tags, frozenset({"arc:reason"}))
        self.assertIn("/v1/archives/delete", spec.routes)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))

    def test_stamp_fits_code_length(self) -> None:
        stamp = reason._stamp()
        self.assertTrue(stamp.startswith("T"))
        self.assertLessEqual(len(stamp), 10)


class _Fake:
    """按 (操作, 编码) 记账的假档案路由：新增后能读到，删除后 404。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.rows: dict[str, dict] = {}

    def send(self, _env: object, op: str, extra: dict) -> tuple[int, dict]:
        self.calls.append((op, extra))
        code = extra["code"]
        if op == "create":
            self.rows[code] = {"code": code, **{k: str(v) for k, v in extra["fields"].items()}}
            return 200, {"ok": True, "code": code}
        if op == "update":
            self.rows[code].update({k: str(v) for k, v in extra["fields"].items()})
            return 200, {"ok": True}
        if op == "delete":
            self.rows.pop(code, None)
            return 200, {"ok": True, "deleted": True}
        row = self.rows.get(code)
        return (200, {"ok": True, "fields": row}) if row else (404, {"ok": False, "code": "not_found"})


class ReasonCycleTests(unittest.TestCase):
    def _run(self, fake: _Fake) -> tuple[str, str]:
        with mock.patch.object(reason, "_send", fake.send), mock.patch("co.e2e.casespec.arc_write_more._send", fake.send):
            return reason.reason_write(object(), None, None)

    def test_full_cycle_passes(self) -> None:
        fake = _Fake()
        verdict, note = self._run(fake)
        self.assertEqual(verdict, "PASS", note)
        ops = [op for op, _extra in fake.calls]
        self.assertEqual(ops[0], "get")
        self.assertEqual([op for op in ops if op != "get"], ["create", "update", "delete"])
        created = next(extra for op, extra in fake.calls if op == "create")
        self.assertEqual(created["fields"]["Reasontype"], 1)
        self.assertEqual(fake.rows, {})

    def test_existing_code_fails_without_writing(self) -> None:
        fake = _Fake()
        fake.rows = mock.MagicMock()
        fake.rows.get.return_value = {"code": "T1"}
        verdict, _note = self._run(fake)
        self.assertEqual(verdict, "FAIL")
        self.assertEqual([op for op, _extra in fake.calls], ["get"])


if __name__ == "__main__":
    unittest.main()
