"""U8TaskConfig 调度记账判别：到点未执行（NextTime 略早于还原开始）放行，太早的仍拒绝。"""

import unittest

from co.e2e.taskcfg import classify


def _doc(pre: str, nxt: str, ts: str) -> dict:
    return {"rows": [{"name": "T", "pre": pre, "next": nxt, "ts": ts, "app": "H", "rest": "r"}]}


class OverdueTests(unittest.TestCase):
    def test_overdue_by_seconds_is_bookkeeping(self) -> None:
        snap = _doc("2026-10-02 19:38:00.000", "2026-10-02 19:39:00.000", "1")
        live = _doc("2026-10-02 19:46:00.000", "2026-10-02 19:47:00.000", "2")
        ok, notes = classify(snap, live, "2026-10-02 19:47:12.299")
        self.assertTrue(ok)
        self.assertEqual(len(notes), 1)

    def test_next_far_before_restore_is_refused(self) -> None:
        snap = _doc("2026-10-02 19:30:00.000", "2026-10-02 19:31:00.000", "1")
        live = _doc("2026-10-02 19:40:00.000", "2026-10-02 19:41:00.000", "2")
        ok, notes = classify(snap, live, "2026-10-02 19:47:12.299")
        self.assertFalse(ok)
        self.assertIn("NextTime 不晚于还原开始", notes[0])


if __name__ == "__main__":
    unittest.main()
