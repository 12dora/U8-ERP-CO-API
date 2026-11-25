"""写入策略文件的读写、等重载与还原（co/e2e/wpolicy.py）。远端和桥都是回放替身，不连接服务器。"""

from __future__ import annotations

import base64
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from co.e2e import wpolicy
from co.e2e.tests import support
from co.e2e.tests.wpolicy_fake import PERMISSIVE_BYTES, FakeBridge


def setUpModule() -> None:
    support.install()


def tearDownModule() -> None:
    support.reset()


class _Out:
    def __init__(self, code: int, out: str) -> None:
        self.code, self.out = code, out
        self.bodies: list[str] = []

    def run_body(self, body: str, _timeout: int) -> SimpleNamespace:
        self.bodies.append(body)
        return SimpleNamespace(code=self.code, out=self.out)


class BodyTests(unittest.TestCase):
    def test_body_carries_root_mode_and_payload(self) -> None:
        body = wpolicy.policy_body("write", b'{"version":1}')
        self.assertIn(r"C:\ProgramData\U8Co\u8co", body)
        self.assertIn("if ('write' -eq 'read')", body)
        self.assertIn(base64.b64encode(b'{"version":1}').decode("ascii"), body)
        # 先写临时文件再原子替换，且只认运行目录之下的文件。
        self.assertIn("[System.IO.File]::Replace($tmp, $file, [NullString]::Value)", body)
        self.assertIn("写入策略文件不在桥的运行目录之下", body)

    def test_bad_mode_refused(self) -> None:
        with self.assertRaises(ValueError):
            wpolicy.policy_body("delete")


class ReadWriteTests(unittest.TestCase):
    def test_read_decodes_and_missing_is_none(self) -> None:
        raw = "WPF-B64:" + base64.b64encode(b"abc").decode("ascii") + "\n"
        self.assertEqual(wpolicy.read_policy(_Out(0, raw)), b"abc")
        self.assertIsNone(wpolicy.read_policy(_Out(0, "WPF-NONE\n")))

    def test_errors_raise(self) -> None:
        for remote in (_Out(1, "boom"), _Out(0, "WPF-NOKEY\n"), _Out(0, "")):
            with self.subTest(out=remote.out), self.assertRaises(wpolicy.PolicyFileError):
                wpolicy.read_policy(remote)
        with self.assertRaises(wpolicy.PolicyFileError):
            wpolicy.write_policy(_Out(0, "nothing"), b"{}")


class WaitTests(unittest.TestCase):
    def test_waits_for_new_loaded_at(self) -> None:
        bridge = FakeBridge()
        before = bridge.health()["write_policy"]["loaded_at"]
        bridge.loaded += 1
        found = wpolicy.wait_reload(bridge, before)
        self.assertNotEqual(found["loaded_at"], before)

    def test_timeout_raises(self) -> None:
        bridge = FakeBridge()
        before = bridge.health()["write_policy"]["loaded_at"]
        ticks = iter([0.0, 5.0, 11.0])
        clock = mock.patch.object(wpolicy.time, "monotonic", lambda: next(ticks))
        with clock, mock.patch.object(wpolicy.time, "sleep"):
            with self.assertRaisesRegex(wpolicy.PolicyFileError, "没有重载为 ok"):
                wpolicy.wait_reload(bridge, before)

    def test_invalid_state_awaited(self) -> None:
        bridge = FakeBridge()
        bridge.state = "invalid"
        self.assertEqual(wpolicy.wait_reload(bridge, "x", "invalid")["state"], "invalid")


class SwapTests(unittest.TestCase):
    def test_restores_original_even_on_error(self) -> None:
        original = b'{"version": 1, "unlisted": "allow", "comment": "lane"}'
        bridge = FakeBridge(original=original)
        with self.assertRaises(RuntimeError), wpolicy.policy_swap(bridge, bridge) as swap:
            swap.apply({"version": 1, "unlisted": "deny"})
            raise RuntimeError("case failed")
        self.assertEqual(bridge.content, original)
        self.assertEqual(bridge.state, "ok")
        self.assertEqual(bridge.loaded, 2)

    def test_missing_original_restored_as_permissive(self) -> None:
        bridge = FakeBridge(original=None)
        bridge.snap = {"version": 1}
        with wpolicy.policy_swap(bridge, bridge) as swap:
            swap.apply({"version": 1, "unlisted": "deny"})
        self.assertEqual(json.loads(bridge.content or b""), wpolicy.PERMISSIVE)
        self.assertEqual(json.loads(PERMISSIVE_BYTES), wpolicy.PERMISSIVE)

    def test_invalid_then_restore(self) -> None:
        bridge = FakeBridge()
        with wpolicy.policy_swap(bridge, bridge) as swap:
            found = swap.apply('{"version": 1, "e2eUnknown": true}', want="invalid")
            self.assertEqual((found["state"], found["version"]), ("invalid", 1))
        self.assertEqual(bridge.state, "ok")


class _Flaky(FakeBridge):
    """写策略文件前 fail_writes 次失败（模拟 SSH 断开、File.Replace 共享冲突）。"""

    fail_writes = 0

    def run_body(self, body: str, timeout: int) -> SimpleNamespace:
        if "'write' -eq 'read'" in body and self.fail_writes > 0:
            self.fail_writes -= 1
            return SimpleNamespace(code=1, out="sharing violation\n")
        return super().run_body(body, timeout)


class RestoreRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        patches = (
            mock.patch.object(wpolicy, "_blocked", []),
            mock.patch.object(wpolicy.time, "sleep"),
            mock.patch("sys.stderr"),
        )
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_restore_retries_transient_failure(self) -> None:
        original = b'{"version": 1, "unlisted": "allow", "comment": "lane"}'
        bridge = _Flaky(original=original)
        with wpolicy.policy_swap(bridge, bridge) as swap:
            swap.apply({"version": 1, "unlisted": "deny"})
            bridge.fail_writes = 2
        self.assertEqual(bridge.content, original)
        self.assertEqual(bridge.state, "ok")
        self.assertEqual(wpolicy.lane_blocked(), "")

    def test_restore_failure_blocks_lane_and_keeps_case_error(self) -> None:
        bridge = _Flaky()
        with self.assertRaises(wpolicy.PolicyNotRestored) as caught, wpolicy.policy_swap(bridge, bridge) as swap:
            swap.apply({"version": 1, "unlisted": "deny"})
            bridge.fail_writes = wpolicy.RESTORE_TRIES
            raise RuntimeError("case failed")
        self.assertIsInstance(caught.exception.__context__, RuntimeError)
        self.assertIn(wpolicy.NOT_RESTORED, str(caught.exception))
        self.assertIn(wpolicy.NOT_RESTORED, wpolicy.lane_blocked())

    def test_apply_failed_restore_succeeds_without_blocking(self) -> None:
        # apply 没写成，文件仍是原内容：写同样的字节桥不会重载，还原应直接成功，不等超时、不阻塞通道。
        original = b'{"version": 1, "unlisted": "allow", "comment": "lane"}'
        bridge = _Flaky(original=original)
        bridge.fail_writes = 1
        waits = mock.patch.object(wpolicy, "wait_reload", side_effect=AssertionError("不应等重载"))
        with waits, self.assertRaises(wpolicy.PolicyFileError) as caught, wpolicy.policy_swap(bridge, bridge) as swap:
            swap.apply({"version": 1, "unlisted": "deny"})
        self.assertNotIsInstance(caught.exception, wpolicy.PolicyNotRestored)
        self.assertEqual(bridge.content, original)
        self.assertEqual((bridge.state, bridge.loaded), ("ok", 0))
        self.assertEqual(bridge.written(), [])
        self.assertEqual(wpolicy.lane_blocked(), "")

    def test_unseen_policy_written_back_without_reload(self) -> None:
        # 桥还没读到新内容就被写回原内容：不会重载，第一次等超时后按「内容未变、仍在基线」认还原成功。
        original = b'{"version": 1, "unlisted": "allow", "comment": "lane"}'
        bridge = FakeBridge(original=original)
        with mock.patch.object(wpolicy, "RESTORE_WAIT", 0.0), wpolicy.policy_swap(bridge, bridge):
            bridge.content = b'{"version": 1, "unlisted": "deny"}'
        self.assertEqual(bridge.content, original)
        self.assertEqual((bridge.state, bridge.loaded), ("ok", 0))
        self.assertEqual(len(bridge.written()), 1)
        self.assertEqual(wpolicy.lane_blocked(), "")

    def test_unknown_loaded_at_is_not_restored(self) -> None:
        bridge = FakeBridge()
        with mock.patch.object(wpolicy, "health_policy", side_effect=OSError("down")):
            with self.assertRaises(wpolicy.PolicyNotRestored):
                wpolicy.PolicySwap(bridge, bridge, None).restore()
        self.assertNotEqual(wpolicy.lane_blocked(), "")


if __name__ == "__main__":
    unittest.main()
