"""engine.run 的还原阶段不被 Ctrl-C / SIGTERM 打断：信号只记警告，还原和报告照常完成。不连服务器。"""

from __future__ import annotations

import os
import signal
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from co.e2e import engine
from co.e2e.context import Run
from co.e2e.tests import support


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(watchdog_min=0, case_names="", jobs=1, force_deploy=False, owner="", warnings=[], failed=False)


class RevertSignalTests(unittest.TestCase):
    def setUp(self) -> None:
        support.install()
        self.saved = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def tearDown(self) -> None:
        for sig, old in self.saved.items():
            signal.signal(sig, old)
        support.reset()

    def _run(self, signum: int) -> tuple[int, SimpleNamespace, list[str]]:
        ctx = _ctx()
        calls: list[str] = []

        def wind_down(run) -> None:
            os.kill(os.getpid(), signum)
            calls.append("wind_down")

        def close_report(run) -> int:
            calls.append("close_report")
            return 0

        with (
            mock.patch.object(engine, "open_run", return_value=ctx),
            mock.patch.object(engine, "sequence"),
            mock.patch.object(engine, "_wind_down", side_effect=wind_down),
            mock.patch.object(engine, "close_report", side_effect=close_report),
            mock.patch.object(engine, "release_all"),
        ):
            code = engine.run(False)
        return code, ctx, calls

    def test_sigint_during_revert_is_held(self) -> None:
        code, ctx, calls = self._run(signal.SIGINT)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["wind_down", "close_report"])
        self.assertIn(engine.HELD_NOTE, ctx.warnings)

    def test_sigterm_during_revert_is_held(self) -> None:
        code, ctx, calls = self._run(signal.SIGTERM)
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["wind_down", "close_report"])
        self.assertIn(engine.HELD_NOTE, ctx.warnings)

    def test_handlers_restored_after_report(self) -> None:
        self._run(signal.SIGINT)
        self.assertIs(signal.getsignal(signal.SIGINT), self.saved[signal.SIGINT])
        # run 开头把 SIGTERM 换成 _on_term（中断用例）；还原结束后回到它，而不是还停在「忽略」。
        self.assertIs(signal.getsignal(signal.SIGTERM), engine._on_term)

    def test_wind_down_sets_cleanup_closed_before_cleanup(self) -> None:
        ctx = Run(False, Path("/nonexistent"), object())
        ctx.snapshots_made = True
        seen: list[bool] = []

        def cleanup(run: Run) -> None:
            seen.append(run.cleanup_closed.is_set())

        with mock.patch.object(engine, "cleanup", cleanup):
            engine._wind_down(ctx)
        self.assertEqual(seen, [True])
        self.assertTrue(ctx.stop.is_set())

    def test_wind_down_keeps_cleanup_open_without_snapshots(self) -> None:
        ctx = Run(False, Path("/nonexistent"), object())
        with mock.patch.object(engine, "cleanup"):
            engine._wind_down(ctx)
        self.assertTrue(ctx.stop.is_set())
        self.assertFalse(ctx.cleanup_closed.is_set())


if __name__ == "__main__":
    unittest.main()
