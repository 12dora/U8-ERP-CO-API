"""其他报检单、其他检验单只读核对（qm_other_read、load-qm_other_*）的登记和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import qm_other_read as qor
from co.e2e.casespec.catalog import build_cases, known_names
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.samples import sample_sql
from co.e2e.casespec.sql_load import LOAD_SOURCES
from co.e2e.casespec.tags import auto_tags
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAMES = ("load-qm_other_inspect", "load-qm_other_check", "qm-other-read")
_SPEC = CaseSpec("s", "s")
_STEP = Step("custom")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _inspect_row(doc: int) -> dict:
    return {"id": doc, "code": f"{doc:010d}", "ufts": str(doc * 10), "check_type": "OTH",
            "source": None, "source_code": None, "source_id": None}


def _check_row(doc: int) -> dict:
    return {"id": doc, "code": f"{doc:010d}", "ufts": str(doc * 10), "check_type": "OTH", "inspect_id": 11}


class _Replay:
    def __init__(self, inspects: list[dict], checks: list[dict]) -> None:
        self.rows = {qor.INSPECT: inspects, qor.CHECK: checks}
        self.check_load = {"ok": True, "head": {"INSPECTID": "11"}, "lines": []}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, _env: object, route: str, body: dict) -> tuple[int, dict]:
        self.calls.append((route, dict(body)))
        rows = self.rows[body["type"]]
        if route.endswith("/list"):
            return 200, self._page(rows, body)
        if route.endswith("/search"):
            return 200, {"ok": True, "items": [row for row in rows if body["code_like"] in row["code"]]}
        if body["type"] == qor.CHECK:
            return 200, self.check_load
        return 200, {"ok": True, "source": None, "lines": [{"AUTOID": 1}]}

    @staticmethod
    def _page(rows: list[dict], body: dict) -> dict:
        after = body.get("after", 0)
        page = [row for row in rows if row["id"] > after][: body["limit"]]
        more = len([row for row in rows if row["id"] > after]) > len(page)
        if body.get("keys_only"):
            page = [{key: row[key] for key in ("id", "code", "ufts")} for row in page]
        return {"ok": True, "items": page, "next": page[-1]["id"] if more else None, "watermark": "1"}


def _run(replay: _Replay) -> tuple[str, str]:
    env = SimpleNamespace(client=object(), bag={})
    with mock.patch.object(qor, "send", replay):
        return qor.qm_other_read(env, _SPEC, _STEP)  # type: ignore[arg-type]


class QmOtherRegistryTests(unittest.TestCase):
    def test_registered_once(self) -> None:
        names = known_names()
        for name in _NAMES:
            self.assertEqual(names.count(name), 1, name)
        spec = qor.qm_other_cases()[0]
        self.assertTrue(spec.requires_login and spec.optional and spec.routes)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))
        self.assertTrue({"list:qm_other_inspect", "list:qm_other_check"} <= auto_tags(spec))

    def test_load_cases_optional_without_wf(self) -> None:
        cases = {spec.name: spec for spec in build_cases({})}
        inspect = cases["load-qm_other_inspect"]
        check = cases["load-qm_other_check"]
        self.assertTrue(inspect.optional and check.optional)
        self.assertIn(("source", ""), inspect.steps[0].checks)
        self.assertIn(("wf", ""), inspect.steps[0].checks)
        self.assertIn(("wf", ""), check.steps[0].checks)

    def test_load_picks_filter_vouch_type(self) -> None:
        picks = {key: where for key, _table, _col, where in LOAD_SOURCES}
        self.assertEqual(picks["load_qm_other_inspect"], "CVOUCHTYPE = N'QM11'")
        self.assertEqual(picks["load_qm_other_check"], "CVOUCHTYPE = N'QM15'")
        rows = dict(sample_sql())
        for key in ("load_qm_other_inspect", "load_qm_other_check"):
            self.assertEqual(sql_block_reason(rows[key]), "", key)


class QmOtherReadTests(unittest.TestCase):
    def test_both_kinds_pass(self) -> None:
        replay = _Replay([_inspect_row(11), _inspect_row(12)], [_check_row(21), _check_row(22)])
        status, detail = _run(replay)
        self.assertEqual(status, "PASS", detail)
        loads = [body for route, body in replay.calls if route.endswith("/load")]
        self.assertIn({"type": qor.INSPECT, "id": 11}, loads)
        self.assertIn({"type": qor.CHECK, "id": 21}, loads)

    def test_no_rows_is_na(self) -> None:
        self.assertEqual(_run(_Replay([], []))[0], "N/A")

    def test_only_inspect_checks_inspect(self) -> None:
        replay = _Replay([_inspect_row(11)], [])
        self.assertEqual(_run(replay)[0], "PASS")
        self.assertFalse(any(body["type"] == qor.CHECK and not route.endswith("/list") for route, body in replay.calls))

    def test_wf_key_fails(self) -> None:
        status, detail = _run(_Replay([], [dict(_check_row(21), wf=False)]))
        self.assertEqual(status, "FAIL")
        self.assertIn("wf", detail)

    def test_source_on_inspect_fails(self) -> None:
        status, detail = _run(_Replay([dict(_inspect_row(11), source_id=5)], []))
        self.assertEqual(status, "FAIL")
        self.assertIn("来源", detail)

    def test_check_load_with_wf_fails(self) -> None:
        replay = _Replay([], [_check_row(21)])
        replay.check_load = dict(replay.check_load, wf={"controlled": False})
        status, detail = _run(replay)
        self.assertEqual(status, "FAIL")
        self.assertIn("wf", detail)

    def test_bridge_error_raises(self) -> None:
        def broken(_env: object, _route: str, _body: dict) -> tuple[int, dict]:
            return 403, {"ok": False, "code": "no_permission", "message": "没有权限"}

        env = SimpleNamespace(client=object(), bag={})
        with mock.patch.object(qor, "send", broken), self.assertRaises(ValueError):
            qor.qm_other_read(env, _SPEC, _STEP)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
