"""出箱发布循环与崩溃恢复：真实 SQLite 状态库，假发布器。"""

from __future__ import annotations

import multiprocessing
import os
import threading
from pathlib import Path

from u8co_events.diff import Scope
from u8co_events.outbox import ALERT_AFTER, ISOLATE_AFTER, OutboxLoop
from u8co_events.state import Store

from fakes_pub import RecordingPublisher, make_event

SCOPE = Scope("999", "sale_order", "2026-09-28T01:02:03Z")


def _seed(path: Path, count: int) -> list[str]:
    events = [make_event(100 + i, str(5000 + i)) for i in range(count)]
    store = Store(path)
    try:
        store.commit_cycle(SCOPE, "6000", [], events)
    finally:
        store.close()
    return [ev["event_id"] for ev in events]


def test_drain_publishes_in_seq_order_and_empties_outbox(tmp_path):
    path = tmp_path / "s.sqlite3"
    ids = _seed(path, 5)
    pub = RecordingPublisher()
    store = Store(path)
    loop = OutboxLoop(store, pub, batch=2, idle_seconds=0.1)
    assert loop.drain_all() == 5
    assert pub.sent == ids
    assert store.outbox_size() == 0
    store.close()


def test_publish_failure_keeps_rows(tmp_path):
    path = tmp_path / "s.sqlite3"
    ids = _seed(path, 3)
    pub = RecordingPublisher()
    pub.fail_next = True
    store = Store(path)
    loop = OutboxLoop(store, pub, batch=10, idle_seconds=0.1)
    try:
        loop.drain_once()
    except RuntimeError:
        pass
    assert store.outbox_size() == 3
    loop.drain_once()
    assert pub.sent == ids
    assert store.outbox_size() == 0
    store.close()


def test_restart_after_outbox_write_before_publish(tmp_path):
    """轮询已提交、还没发布就断电：重启后照发，一条不少。"""
    path = tmp_path / "s.sqlite3"
    ids = _seed(path, 4)
    store = Store(path)
    pub = RecordingPublisher()
    OutboxLoop(store, pub, batch=100, idle_seconds=0.1).drain_all()
    store.close()
    assert pub.sent == ids


def _publish_then_die(path: str, sink: str) -> None:
    """子进程：发布（写入并 fsync 到 sink）后、mark_sent 前被杀。"""

    class Dying:
        def publish(self, rows):
            with open(sink, "a", encoding="utf-8") as fh:
                for row in rows:
                    fh.write(row.event_id + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            os._exit(9)

        def close(self):
            return None

    OutboxLoop(Store(path), Dying(), batch=2, idle_seconds=0.1).drain_once()


def test_killed_between_publish_and_mark_sent_resends(tmp_path):
    """发布成功后、删出箱前进程被杀：重启后重发（至少一次），消费方按 event_id 去重后正好一份。"""
    path = tmp_path / "s.sqlite3"
    sink = tmp_path / "sink.txt"
    ids = _seed(path, 3)
    proc = multiprocessing.get_context("fork").Process(target=_publish_then_die, args=(str(path), str(sink)))
    proc.start()
    proc.join(30)
    assert proc.exitcode == 9
    first = sink.read_text(encoding="utf-8").split()
    assert first == ids[:2]

    store = Store(path)
    assert store.outbox_size() == 3
    pub = RecordingPublisher()
    OutboxLoop(store, pub, batch=2, idle_seconds=0.1).drain_all()
    store.close()
    delivered = first + pub.sent
    assert pub.sent == ids
    assert len(delivered) == 5
    assert list(dict.fromkeys(delivered)) == ids


def test_run_forever_retries_then_stops(tmp_path):
    """发布失败后退避重试，成功后清空出箱；stop 置位即返回。"""
    path = tmp_path / "s.sqlite3"
    ids = _seed(path, 2)
    stop = threading.Event()
    result: dict[str, object] = {}

    class StopWhenDone(RecordingPublisher):
        def publish(self, rows):
            super().publish(rows)
            if len(self.sent) >= len(ids):
                stop.set()

    pub = StopWhenDone()
    pub.fail_next = True

    def body():
        store = Store(path)  # Store 不跨线程，在线程里打开
        try:
            loop = OutboxLoop(store, pub, batch=10, idle_seconds=0.1)
            loop.run_forever(stop)
            result["failures"] = loop.failures
            result["left"] = store.outbox_size()
        finally:
            store.close()

    th = threading.Thread(target=body)
    th.start()
    th.join(30)
    assert not th.is_alive()
    assert pub.sent == ids
    assert result == {"failures": 1, "left": 0}


def test_poison_head_is_isolated_and_surfaced_never_dropped(tmp_path, caplog):
    """首条一直发不出去：改为逐条发、日志写出 event_id、健康检查报出来；出箱一条不少。"""
    path = tmp_path / "s.sqlite3"
    ids = _seed(path, 3)
    pub = RecordingPublisher()
    pub.fail_always = True
    store = Store(path)
    loop = OutboxLoop(store, pub, batch=10, idle_seconds=0.1)
    for _ in range(ALERT_AFTER):
        try:
            loop.drain_once()
        except RuntimeError as exc:
            loop.last_error = str(exc)
    assert pub.batches[:ISOLATE_AFTER] == [3] * ISOLATE_AFTER
    assert pub.batches[ISOLATE_AFTER:] == [1] * (ALERT_AFTER - ISOLATE_AFTER)
    assert loop.head_event == ids[0]
    assert ids[0] in caplog.text
    assert any(ids[0] in text for text in loop.problems())
    assert store.outbox_size() == 3

    pub.fail_always = False
    loop.drain_all()
    assert pub.sent == ids
    assert loop.problems() == []
    store.close()
