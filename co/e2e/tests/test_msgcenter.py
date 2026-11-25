"""MsgCenter.db 只读核对：被占用、没有权限、正常三种结果都只记警告。不连接服务器。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from co.e2e.context import Run
from co.e2e.guard import capture_msgcenter
from co.e2e.remote import RemoteResult
from co.e2e.tests import support


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


def _doc(sha: str = "aa", **extra) -> dict:
    doc = {"sha256": sha, "size": 10, "mtime": "2026-01-01T00:00:00.0000000Z", "in_use": False, "busy": False}
    doc.update(extra)
    return doc


class FakeRemote:
    def __init__(self) -> None:
        self.docs: list[dict] = []
        self.code = 0

    def run_script(self, name: str, named: dict | None, timeout: int) -> RemoteResult:
        del named, timeout
        return RemoteResult(self.code, name)

    def download(self, remote_win: str, local: Path) -> None:
        del remote_win
        local.write_text(json.dumps(self.docs.pop(0)), encoding="utf-8")


def _session(*docs: dict) -> tuple[Run, FakeRemote]:
    remote = FakeRemote()
    remote.docs = list(docs)
    ctx = Run(False, Path(tempfile.mkdtemp()), remote)
    return ctx, remote


def _both(ctx: Run) -> None:
    capture_msgcenter(ctx, "open")
    capture_msgcenter(ctx, "close")


class MsgCenterTests(unittest.TestCase):
    def test_same_hash_no_warning(self) -> None:
        ctx, _ = _session(_doc(), _doc())
        _both(ctx)
        self.assertEqual(ctx.warnings, [])
        self.assertEqual([row["ok"] for row in ctx.steps], [True, True])

    def test_busy_at_open_warns_once_and_skips_compare(self) -> None:
        ctx, _ = _session(_doc("", busy=True, in_use=True, reason="being used by another process"), _doc("bb"))
        _both(ctx)
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("打开时MsgCenter.db 正被占用", ctx.warnings[0])
        self.assertIn("being used by another process", ctx.warnings[0])
        self.assertIsNone(ctx.msg_open)
        self.assertEqual([row["ok"] for row in ctx.steps], [True, True])

    def test_busy_at_close_replaces_generic_warning(self) -> None:
        ctx, _ = _session(_doc(), _doc("", busy=True, in_use=True))
        _both(ctx)
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("结束时MsgCenter.db 正被占用", ctx.warnings[0])
        self.assertNotIn("变化", ctx.warnings[0])

    def test_denied_keeps_reason(self) -> None:
        ctx, _ = _session(_doc("", busy=True, denied=True, reason="Access is denied"), _doc())
        _both(ctx)
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("没有权限读取", ctx.warnings[0])
        self.assertIn("Access is denied", ctx.warnings[0])

    def test_change_while_in_use_says_so(self) -> None:
        ctx, _ = _session(_doc("aa"), _doc("bb", in_use=True))
        _both(ctx)
        self.assertEqual(len(ctx.warnings), 1)
        self.assertIn("发生变化", ctx.warnings[0])
        self.assertIn("正被 U8 占用", ctx.warnings[0])

    def test_change_without_in_use_plain_warning(self) -> None:
        ctx, _ = _session(_doc("aa"), _doc("bb"))
        _both(ctx)
        self.assertEqual(len(ctx.warnings), 1)
        self.assertNotIn("占用", ctx.warnings[0])

    def test_script_failure_keeps_old_warning(self) -> None:
        ctx, remote = _session()
        remote.code = 1
        capture_msgcenter(ctx, "open")
        self.assertEqual(ctx.warnings, ["警告：打开时没能读取 MsgCenter.db"])
        self.assertEqual([row["ok"] for row in ctx.steps], [False])


if __name__ == "__main__":
    unittest.main()
