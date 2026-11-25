"""账套体检用例（readiness）的登记、标签和回放核对。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from co.client.u8co_client import U8CoError

from co.e2e import testdata
from co.e2e.casespec import ops_run
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.returns_misc import returns_misc_cases
from co.e2e.casespec.openings_pu import openings_cases
from co.e2e.casespec.readiness import CHECK_IDS, account_readiness, expect_ready, readiness_cases
from co.e2e.casespec.tags import auto_tags, conflicts
from co.e2e.tests import support

_NAME = "report-account-readiness"
_ROUTE = "/v1/reports/account_readiness"
_SPEC = CaseSpec(_NAME, "s")
_STEP = Step("custom", sql_name="account_readiness")


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    def __init__(self, answer: object) -> None:
        self.answer = answer
        self.calls: list[tuple[str, dict]] = []

    def call(self, route: str, fields: dict) -> dict:
        self.calls.append((route, dict(fields)))
        if isinstance(self.answer, U8CoError):
            raise self.answer
        return self.answer


def _env(client: object) -> SimpleNamespace:
    operator = SimpleNamespace(acc="999", year="2026", code="OP-001", password="pw")
    return SimpleNamespace(client=client, operator=operator, year="2026", today="2026-09-28", bag={})


def _check(name: str, status: str = "ok") -> dict:
    return {
        "id": name,
        "title": name,
        "status": status,
        "detail": "",
        "fix_hint": "",
        "docs_anchor": f"getting-started.md#{name}",
        "safe": True,
    }


def _reply(overall: str = "ok", **statuses: str) -> dict:
    checks = [_check(name, statuses.get(name, "ok")) for name in CHECK_IDS]
    return {"ok": True, "overall": overall, "as_of": "2026-09-28", "server_today": "2026-09-28", "acc": "999",
            "checks": checks}


def _run(answer: object) -> tuple[str, str, _Replay]:
    client = _Replay(answer)
    status, detail = account_readiness(_env(client), _SPEC, _STEP)
    return status, detail, client


def _reference(flag: object) -> None:
    data = support.sample_testdata()
    data["readiness"] = {"expect_ready": flag}
    testdata.use(data)


class ReadinessCatalogTests(unittest.TestCase):
    def test_case_registered_in_the_reports_phase(self) -> None:
        self.assertEqual(known_names().count(_NAME), 1)
        spec = next(item for item in returns_misc_cases() if item.name == _NAME)
        self.assertEqual(spec, readiness_cases()[0])
        self.assertTrue(spec.requires_login)
        self.assertTrue(spec.optional)
        self.assertEqual(spec.routes, (_ROUTE,))
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))

    def test_staggered_with_the_opening_post_cases(self) -> None:
        mine = auto_tags(readiness_cases()[0])
        dry = next(spec for spec in openings_cases() if spec.name == "openings-post-dry")
        self.assertTrue(conflicts(mine, auto_tags(dry)))

    def test_changed_files_pick_the_case(self) -> None:
        catalog = list(known_names())
        for path in (
            "co/bridge/src/ReportsReadinessProbe.cs",
            "api/u8co_api/co_models_reports_ready.py",
            "api/u8co_api/co_routes_reports.py",
            "co/client/u8co_reports.py",
        ):
            self.assertIn(_NAME, select_changed([path], catalog), path)


class ReadinessReplayTests(unittest.TestCase):
    def tearDown(self) -> None:
        testdata.use(support.sample_testdata())

    def test_empty_body_and_shape_pass(self) -> None:
        status, detail, client = _run(_reply())
        self.assertEqual(status, "PASS", detail)
        route, sent = client.calls[0]
        self.assertEqual(route, _ROUTE)
        self.assertNotIn("as_of", sent)
        self.assertEqual(sent["acc"], "999")

    def test_fail_overall_passes_off_the_reference_lane(self) -> None:
        self.assertFalse(expect_ready())
        status, detail, _client = _run(_reply("fail", patches="fail", years="fail", calendar="warn"))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("patches=fail", detail)
        self.assertIn("calendar=warn", detail)

    def test_reference_lane_needs_ok_or_warn(self) -> None:
        _reference(True)
        self.assertTrue(expect_ready())
        self.assertEqual(_run(_reply("warn", calendar="warn"))[0], "PASS")
        status, detail, _client = _run(_reply("fail", workflow="fail"))
        self.assertEqual(status, "FAIL")
        self.assertIn("workflow=fail", detail)
        status, _detail, _client = _run(_reply("unknown", patches="unknown"))
        self.assertEqual(status, "FAIL")

    def test_reference_lane_allow_fail_list(self) -> None:
        data = support.sample_testdata()
        data["readiness"] = {"expect_ready": True, "allow_fail": ["prior_gl_close", 3]}
        testdata.use(data)
        status, detail, _client = _run(_reply("fail", prior_gl_close="fail", patches="warn"))
        self.assertEqual(status, "PASS", detail)
        self.assertIn("prior_gl_close=fail", detail)
        status, detail, _client = _run(_reply("fail", prior_gl_close="fail", workflow="fail"))
        self.assertEqual(status, "FAIL")
        self.assertIn("workflow=fail", detail)
        self.assertEqual(_run(_reply("unknown", prior_gl_close="unknown"))[0], "FAIL")
        data["readiness"] = {"expect_ready": True, "allow_fail": "prior_gl_close"}
        testdata.use(data)
        self.assertEqual(_run(_reply("fail", prior_gl_close="fail"))[0], "FAIL")

    def test_only_a_true_boolean_marks_the_reference_lane(self) -> None:
        for flag in ("true", 1, None, False):
            _reference(flag)
            self.assertFalse(expect_ready(), flag)

    def test_shape_problems_fail(self) -> None:
        missing = _reply()
        missing["checks"] = missing["checks"][1:]
        bad_status = _reply(years="red")
        bad_anchor = _reply()
        bad_anchor["checks"][0]["docs_anchor"] = "x"
        unsafe = _reply()
        unsafe["checks"][3]["safe"] = False
        lost_key = _reply()
        del lost_key["checks"][4]["fix_hint"]
        doubled = _reply()
        doubled["checks"].append(_check("patches"))
        cases = (
            (_reply("bad"), "overall"),
            ({"ok": True, "overall": "ok"}, "checks"),
            (missing, "patches"),
            (bad_status, "years"),
            (bad_anchor, "docs_anchor"),
            (unsafe, "safe"),
            (lost_key, "fix_hint"),
            (doubled, "重复"),
        )
        for answer, text in cases:
            status, detail, _client = _run(answer)
            self.assertEqual(status, "FAIL", text)
            self.assertIn(text, detail)

    def test_non_200_fails(self) -> None:
        status, detail, _client = _run(U8CoError(422, "login_failed", "不存在的年度"))
        self.assertEqual(status, "FAIL")
        self.assertIn("422", detail)


if __name__ == "__main__":
    unittest.main()
