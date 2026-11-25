"""桥 /health 登录计数：读取、前后差值、报告行。全部离线，不连服务器。"""

from __future__ import annotations

import unittest
from unittest import mock

from co.client.u8co_client import U8CoError
from co.e2e import suite
from co.e2e.report import assemble, bridge_login_line, render_markdown
from co.e2e.suite import login_delta, read_login_counters
from co.e2e.tests import support


class _Client:
    def __init__(self, payload: object = None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error

    def health(self) -> object:
        if self.error is not None:
            raise self.error
        return self.payload


def _restart(reuse: object) -> dict:
    return {**_counts(reuse, None, None), "restarted": True}


def _counts(reuse: object, opened: object, reused: object, closed: object = None) -> dict:
    return {"login_reuse": reuse, "opened": opened, "reused": reused, "closed": closed}


class ReadCountersTests(unittest.TestCase):
    def test_reads_new_fields(self) -> None:
        client = _Client({"ok": True, "login_reuse": True, "logins_opened": 3, "logins_reused": 40, "logins_closed": 2})
        self.assertEqual(read_login_counters(client), _counts(True, 3, 40, 2))  # type: ignore[arg-type]

    def test_reads_without_closed(self) -> None:
        client = _Client({"ok": True, "login_reuse": False, "logins_opened": 3, "logins_reused": 0})
        self.assertEqual(read_login_counters(client), _counts(False, 3, 0))  # type: ignore[arg-type]

    def test_old_bridge_without_fields(self) -> None:
        self.assertEqual(read_login_counters(_Client({"ok": True})), _counts(None, None, None))  # type: ignore[arg-type]

    def test_bad_values_are_ignored(self) -> None:
        client = _Client({"login_reuse": "yes", "logins_opened": True, "logins_reused": -1, "logins_closed": 1.5})
        self.assertEqual(read_login_counters(client), _counts(None, None, None))  # type: ignore[arg-type]

    def test_health_error_gives_none(self) -> None:
        client = _Client(error=U8CoError(503, "stopping", "服务正在停止"))
        self.assertIsNone(read_login_counters(client))  # type: ignore[arg-type]


class DeltaTests(unittest.TestCase):
    def test_delta(self) -> None:
        self.assertEqual(login_delta(_counts(True, 5, 10), _counts(True, 7, 60)), _counts(True, 2, 50))

    def test_delta_with_closed(self) -> None:
        self.assertEqual(login_delta(_counts(True, 5, 10, 1), _counts(True, 7, 60, 4)), _counts(True, 2, 50, 3))

    def test_closed_missing_on_one_side(self) -> None:
        self.assertEqual(login_delta(_counts(True, 5, 10), _counts(True, 7, 60, 4)), _counts(True, 2, 50))

    def test_reuse_off_counts(self) -> None:
        self.assertEqual(login_delta(_counts(False, 0, 0), _counts(False, 42, 0)), _counts(False, 42, 0))

    def test_absent_fields(self) -> None:
        self.assertEqual(login_delta(_counts(None, None, None), _counts(None, None, None)), _counts(None, None, None))

    def test_missing_reading(self) -> None:
        self.assertEqual(login_delta(None, _counts(True, 4, 4)), _counts(True, None, None))
        self.assertEqual(login_delta(_counts(False, 4, 4), None), _counts(False, None, None))
        self.assertEqual(login_delta(None, None), _counts(None, None, None))

    def test_restart_detected(self) -> None:
        self.assertEqual(login_delta(_counts(True, 9, 30), _counts(True, 2, 31)), _restart(True))
        self.assertEqual(login_delta(_counts(True, 9, 30), _counts(True, 10, 1)), _restart(True))
        self.assertEqual(login_delta(_counts(True, 9, 30, 5), _counts(True, 10, 31, 0)), _restart(True))

    def test_reuse_flip_is_restart(self) -> None:
        # 改 loginReuse 必须重启桥；即使计数涨回到读数之上也不算差值。
        self.assertEqual(login_delta(_counts(False, 9, 0), _counts(True, 20, 5)), _restart(True))
        self.assertEqual(login_delta(_counts(True, 9, 3), _counts(False, 20, 3)), _restart(False))


class SuiteFinallyTests(unittest.TestCase):
    def test_cases_error_keeps_reading(self) -> None:
        readings = [_counts(True, 1, 1, 0), _counts(True, 4, 9, 2)]
        ctx = type("C", (), {"needles": [], "secret": "", "case_names": "", "jobs": 1, "bridge_logins": None})()
        operator = type("O", (), {"password": "x"})()
        with (
            mock.patch.object(suite, "load_operator", return_value=operator),
            mock.patch.object(suite, "conf"),
            mock.patch.object(suite, "Path"),
            mock.patch.object(suite, "U8CoClient"),
            mock.patch.object(suite, "open_env"),
            mock.patch.object(suite, "read_login_counters", side_effect=readings),
            mock.patch.object(suite, "run_selected", side_effect=RuntimeError("用例中断")),
            self.assertRaises(RuntimeError),
        ):
            suite.run_suite(ctx, "2026-01-01", "s", "a")  # type: ignore[arg-type]
        self.assertEqual(ctx.bridge_logins, _counts(True, 3, 8, 2))


class ReportLineTests(unittest.TestCase):
    def setUp(self) -> None:
        support.install()

    def tearDown(self) -> None:
        support.reset()

    def test_line_variants(self) -> None:
        self.assertEqual(bridge_login_line(_counts(True, 2, 50)), "U8 登录：新开 2 次，复用 50 次（登录复用开）")
        self.assertEqual(bridge_login_line(_counts(False, 42, 0)), "U8 登录：新开 42 次，复用 0 次（登录复用关）")
        self.assertEqual(bridge_login_line(_counts(True, 2, 50, 1)), "U8 登录：新开 2 次，复用 50 次，关闭 1 次（登录复用开）")
        self.assertEqual(bridge_login_line(_counts(True, None, None)), "U8 登录：桥未提供计数")
        self.assertEqual(bridge_login_line(None), "U8 登录：桥未提供计数")
        self.assertEqual(bridge_login_line(_restart(True)), "U8 登录：运行期间桥重启过，计数作废")

    def test_json_and_markdown(self) -> None:
        report = assemble({"dry_run": True, "u8_logins": 61, "bridge_logins": _counts(True, 2, 50)})
        self.assertEqual(report["bridge_logins"], _counts(True, 2, 50))
        self.assertEqual(report["u8_logins"], 61)
        text = render_markdown(report)
        self.assertIn("本次登录次数（桥审计，不含 401/403/400）：61\nU8 登录：新开 2 次，复用 50 次（登录复用开）", text)

    def test_markdown_without_measurement(self) -> None:
        report = assemble({"dry_run": True})
        self.assertIsNone(report["bridge_logins"])
        self.assertNotIn("U8 登录：", render_markdown(report))
        self.assertIn("U8 登录：桥未提供计数", render_markdown({"bridge_logins": _counts(None, None, None)}))


if __name__ == "__main__":
    unittest.main()
