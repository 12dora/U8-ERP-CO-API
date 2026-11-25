"""总账取消记账用例 `gl-post-unpost-cycle`：登记、回读 SQL 和离线回放。不连接服务器。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e.casespec import ops_run
from co.e2e.casespec import gl_unpost as gu
from co.e2e.casespec.catalog import known_names
from co.e2e.casespec.coverage import select_changed
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.gl_unpost_sql import gl_unpost_samples, range_sql, totals_sql
from co.e2e.casespec.gl_voucher_sql import gl_key
from co.e2e.sqlguard import sql_block_reason
from co.e2e.tests import support

_NAME = "gl-post-unpost-cycle"
_SPEC = CaseSpec(_NAME, "t")
_STEP = Step("custom", sql_name="gl_post_unpost_cycle")
_OPERATOR = "演示"


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Replay:
    """假桥 + 假 SQL：记账改总账校验和，取消记账改回（drift 为真时改不回）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.posted = False
        self.unposted = False
        self.probe: tuple[int, dict] = (409, {"code": "state_mismatch"})
        self.unpost_reply: tuple[int, dict] | None = None
        self.dry_moves = False
        self.drift = False

    def send(self, _env: object, op: str, body: dict) -> tuple[int, dict]:
        self.calls.append((op, dict(body)))
        if op == "create":
            return 200, {"period": 9, "sign": "转", "no": 5}
        if op == "post":
            self.posted = True
            return 200, {"ok": True}
        if op == "unpost":
            return self._unpost(body)
        return 200, {"ok": True}

    def _unpost(self, body: dict) -> tuple[int, dict]:
        if body.get("dry_run") is True:
            if "vouchers" not in body:
                return self.probe
            self.unposted = self.unposted or self.dry_moves
            return 200, {"ok": True, "dry_run": True, "mode": "rollback"}
        if not self.posted or self.unposted:
            return 409, {"code": "state_mismatch", "message": "没有可恢复的记账"}
        if self.unpost_reply is not None:
            return self.unpost_reply
        self.unposted = True
        return 200, {"ok": True, "period": 9, "count": 1, "vouchers": [{"sign": "转", "no": 5}]}

    def cell(self, _env: object, _spec: CaseSpec, query: str) -> str:
        live = self.posted and not self.unposted
        if "GL_AccMultiAss" in query:
            return "posted" if live or (self.unposted and self.drift) else "clean"
        if "MIN(ISNULL(cbook" in query:
            return f"2|1|{_OPERATOR}|{_OPERATOR}" if live else "2|0||"
        if "GL_mpostcond" in query:
            return "1" if self.posted and not self.unposted else "0"
        if "bMasterSign" in query:
            return "0"
        if "MIN(iperiod)" in query:
            return "9"
        return "0"

    def ops(self) -> list[str]:
        return [op for op, _body in self.calls]


def _run(replay: _Replay) -> tuple[str, str]:
    env = SimpleNamespace(client=object(), today="2026-09-28", operator_name=_OPERATOR, bag={})
    with mock.patch.object(gu, "_send", replay.send), mock.patch.object(gu, "_cell", replay.cell):
        return gu.gl_post_unpost_cycle(env, _SPEC, _STEP)  # type: ignore[arg-type]


class RegistryTests(unittest.TestCase):
    def test_registered_with_ops_and_tags(self) -> None:
        self.assertEqual(known_names().count(_NAME), 1)
        spec = gu.gl_unpost_cases()[0]
        self.assertTrue(spec.requires_login and spec.routes)
        self.assertTrue({"gl", "new:gl:转"} <= spec.tags)
        self.assertTrue(ops_run.has_op(spec.steps[0].sql_name))

    def test_changed_files_select_the_case(self) -> None:
        catalog = list(known_names())
        for path in ("co/bridge/src/GlUnpost.cs", "co/bridge/src/GlUnpostSql.cs", "api/u8co_api/co_models_gl_unpost.py"):
            self.assertIn(_NAME, select_changed([path], catalog), path)


class SqlTests(unittest.TestCase):
    def test_samples_pass_the_guard(self) -> None:
        for name, query in gl_unpost_samples():
            self.assertEqual(sql_block_reason(query), "", name)
            self.assertIn("N'ROW:'", query)

    def test_values_are_whitelisted(self) -> None:
        query = totals_sql("2026", gl_key(9, "转", 3))
        self.assertIn("v.iyear = 2026 AND v.iperiod = 9 AND v.csign = N'转' AND v.ino_id = 3", query)
        self.assertIn("s.iperiod BETWEEN 9 AND 12", query)
        for table in ("GL_accsum", "GL_accass", "GL_AccMultiAss"):
            self.assertIn("dbo." + table, query)
        with self.assertRaises(ValueError):
            totals_sql("2026; --", gl_key(9, "转", 3))
        with self.assertRaises(ValueError):
            range_sql("2026 OR 1=1")


class ReplayTests(unittest.TestCase):
    def test_full_cycle_passes(self) -> None:
        replay = _Replay()
        status, detail = _run(replay)
        self.assertEqual(status, "PASS", detail)
        self.assertEqual(
            replay.ops(), ["unpost", "create", "verify", "post", "unpost", "unpost", "unpost", "unverify", "void", "delete"]
        )
        real = [body for op, body in replay.calls if op == "unpost" and not body.get("dry_run")]
        self.assertEqual(real[0], {"period": 9, "vouchers": [{"sign": "转", "no": 5}]})

    def test_not_a_test_account_is_na_before_creating(self) -> None:
        replay = _Replay()
        replay.probe = (403, {"code": "test_account_only"})
        self.assertEqual(_run(replay)[0], "N/A")
        self.assertEqual(replay.ops(), ["unpost"])

    def test_totals_drift_fails_and_cleans_up(self) -> None:
        replay = _Replay()
        replay.drift = True
        status, detail = _run(replay)
        self.assertEqual(status, "FAIL")
        self.assertIn("校验和", detail)
        self.assertEqual(replay.ops()[-3:], ["unverify", "void", "delete"])

    def test_refused_unpost_leaves_the_voucher(self) -> None:
        replay = _Replay()
        replay.unpost_reply = (409, {"code": "state_mismatch", "message": "核对不符"})
        status, detail = _run(replay)
        self.assertEqual(status, "FAIL")
        self.assertIn("快照", detail)
        self.assertNotIn("delete", replay.ops())

    def test_dry_run_that_writes_fails(self) -> None:
        replay = _Replay()
        replay.dry_moves = True
        status, detail = _run(replay)
        self.assertEqual(status, "FAIL")
        self.assertIn("预演", detail)


if __name__ == "__main__":
    unittest.main()
