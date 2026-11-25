"""业务时间护栏。时刻都是注入的，不连接服务器。"""

from __future__ import annotations

import copy
import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, time, timezone
from unittest import mock

from co.e2e import daytime, run_e2e, session, settings
from co.e2e.settings import ConfigError
from co.e2e.tests import support

_HOURS = {"days": "1-6", "start": "08:00", "end": "17:30", "tz": "Asia/Shanghai"}
# 2026-09-28 是星期一。上海 10:00 = UTC 02:00。
_MON_10 = datetime(2026, 9, 28, 2, 0, tzinfo=timezone.utc)
_MON_1730 = datetime(2026, 9, 28, 9, 30, tzinfo=timezone.utc)
_MON_0759 = datetime(2026, 9, 27, 23, 59, tzinfo=timezone.utc)
_SUN_10 = datetime(2026, 9, 27, 2, 0, tzinfo=timezone.utc)
_MON_0740 = datetime(2026, 9, 27, 23, 40, tzinfo=timezone.utc)
_MON_1729 = datetime(2026, 9, 28, 9, 29, tzinfo=timezone.utc)
_SAT_2350 = datetime(2026, 10, 3, 15, 50, tzinfo=timezone.utc)


def _data(hours: object) -> dict:
    data = copy.deepcopy(support.sample_dict())
    data["business_hours"] = hours
    return data


def _hours(**changes: str) -> daytime.BusinessHours | None:
    value = dict(_HOURS)
    value.update(changes)
    return settings.from_dict(_data(value)).business_hours


def tearDownModule() -> None:
    support.reset()


class ParseTests(unittest.TestCase):
    def test_missing_key_is_config_error(self) -> None:
        data = copy.deepcopy(support.sample_dict())
        data.pop("business_hours", None)
        with self.assertRaises(ConfigError) as caught:
            settings.from_dict(data)
        self.assertIn("缺少 business_hours", str(caught.exception))
        self.assertIn("null", str(caught.exception))

    def test_explicit_null_means_no_guard(self) -> None:
        self.assertIsNone(settings.from_dict(_data(None)).business_hours)

    def test_examples_enable_guard_with_server_timezone(self) -> None:
        for cfg in (settings.from_dict(support.sample_dict()), support.lane_config()):
            hours = cfg.business_hours
            assert hours is not None
            self.assertEqual(hours.days, frozenset(range(1, 6)))
            self.assertEqual((hours.start, hours.end), (time(8, 0), time(17, 30)))
            self.assertEqual(hours.tz, cfg.timezone)

    def test_parses_ranges_and_lists(self) -> None:
        hours = _hours()
        assert hours is not None
        self.assertEqual(hours.days, frozenset(range(1, 7)))
        self.assertEqual((hours.start, hours.end), (time(8, 0), time(17, 30)))
        listed = _hours(days="1,3,5-6")
        assert listed is not None
        self.assertEqual(listed.days, frozenset((1, 3, 5, 6)))

    def test_tz_defaults_to_server_timezone(self) -> None:
        value = dict(_HOURS)
        value.pop("tz")
        hours = settings.from_dict(_data(value)).business_hours
        assert hours is not None
        self.assertEqual(hours.tz, "Asia/Shanghai")

    def test_rejects_bad_values(self) -> None:
        bad = (
            {"days": "0-5"},
            {"days": "5-1"},
            {"days": "1-5,"},
            {"days": "mon"},
            {"start": "8:00"},
            {"end": "24:00"},
            {"start": "18:00"},
            {"tz": "Mars/Base"},
        )
        for change in bad:
            with self.subTest(change=change), self.assertRaises(ConfigError):
                _hours(**change)

    def test_rejects_unknown_or_missing_keys(self) -> None:
        with self.assertRaises(ConfigError):
            settings.from_dict(_data(dict(_HOURS, weekend="7")))
        for key in ("days", "start", "end"):
            value = dict(_HOURS)
            value.pop(key)
            with self.subTest(key=key), self.assertRaises(ConfigError):
                settings.from_dict(_data(value))
        with self.assertRaises(ConfigError):
            settings.from_dict(_data("1-6 08:00-17:30"))


class RefusalTests(unittest.TestCase):
    def test_refuses_inside_hours(self) -> None:
        message = daytime.refusal(_hours(), False, False, _MON_10)
        self.assertIn("现在是业务时间", message)
        self.assertIn("争用 U8 许可", message)
        self.assertIn("--allow-daytime", message)
        self.assertIn("2026-09-28 10:00", message)

    def test_outside_hours_or_day_is_allowed(self) -> None:
        hours = _hours()
        for moment in (_MON_1730, _MON_0759, _SUN_10):
            with self.subTest(moment=moment):
                self.assertEqual(daytime.refusal(hours, False, False, moment), "")

    def test_refuses_when_watchdog_reaches_hours(self) -> None:
        hours = _hours()
        message = daytime.refusal(hours, False, False, _MON_0740, minutes=25)
        self.assertIn("看门狗 25 分钟内会进入业务时间", message)
        self.assertIn("--allow-daytime", message)
        self.assertIn("现在是业务时间", daytime.refusal(hours, False, False, _MON_1729, minutes=25))

    def test_watchdog_ending_before_hours_is_allowed(self) -> None:
        hours = _hours()
        self.assertEqual(daytime.refusal(hours, False, False, _MON_0740, minutes=19), "")
        self.assertEqual(daytime.refusal(hours, False, False, _MON_1730, minutes=120), "")
        # 星期六 23:50 起 120 分钟跨进星期日，星期日不是业务日。
        self.assertEqual(daytime.refusal(hours, False, False, _SAT_2350, minutes=120), "")

    def test_flag_dry_run_or_no_config_never_refuse(self) -> None:
        hours = _hours()
        self.assertEqual(daytime.refusal(hours, True, False, _MON_10), "")
        self.assertEqual(daytime.refusal(hours, False, True, _MON_10), "")
        self.assertEqual(daytime.refusal(None, False, False, _MON_10), "")


class CommandTests(unittest.TestCase):
    def setUp(self) -> None:
        support.install(settings.from_dict(_data(dict(_HOURS))))
        clock = mock.patch.object(daytime, "current_time", return_value=_MON_10)
        clock.start()
        self.addCleanup(clock.stop)

    def _main(self, argv: list[str]) -> tuple[int, mock.MagicMock]:
        with mock.patch.object(run_e2e, "run", return_value=0) as runner, redirect_stdout(io.StringIO()):
            code = run_e2e.main(argv)
        return code, runner

    def test_run_refused_in_business_hours(self) -> None:
        code, runner = self._main([])
        self.assertEqual(code, 2)
        runner.assert_not_called()

    def test_run_allowed_with_flag_or_dry_run(self) -> None:
        for argv in (["--allow-daytime"], ["--dry-run"]):
            with self.subTest(argv=argv):
                code, runner = self._main(argv)
                self.assertEqual(code, 0)
                runner.assert_called_once()

    def _at(self, moment: datetime) -> None:
        clock = mock.patch.object(daytime, "current_time", return_value=moment)
        clock.start()
        self.addCleanup(clock.stop)

    def test_run_refused_when_watchdog_reaches_hours(self) -> None:
        # 07:40 起，配置的 run_minutes 25 会跨进 08:00；--watchdog-min 10 就不会（下限也是 10）。
        self._at(_MON_0740)
        code, runner = self._main([])
        self.assertEqual(code, 2)
        runner.assert_not_called()
        code, runner = self._main(["--watchdog-min", "10"])
        self.assertEqual(code, 0)
        runner.assert_called_once()

    def test_session_open_uses_watchdog_minutes(self) -> None:
        self._at(_MON_0740)
        with mock.patch.object(session, "open_session", return_value=0) as opener, redirect_stdout(io.StringIO()):
            refused = session._run(session._parser().parse_args(["open"]))
            allowed = session._run(session._parser().parse_args(["open", "--watchdog-min", "10"]))
        self.assertEqual((refused, allowed), (2, 0))
        opener.assert_called_once_with(10)

    def test_session_open_refused_then_allowed(self) -> None:
        with mock.patch.object(session, "open_session", return_value=0) as opener, redirect_stdout(io.StringIO()):
            refused = session._run(session._parser().parse_args(["open"]))
            allowed = session._run(session._parser().parse_args(["open", "--allow-daytime"]))
        self.assertEqual((refused, allowed), (2, 0))
        opener.assert_called_once()


if __name__ == "__main__":
    unittest.main()
