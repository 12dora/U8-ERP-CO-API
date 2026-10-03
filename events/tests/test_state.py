from __future__ import annotations

import json
import os
import sqlite3
import stat
import subprocess
import sys
import textwrap

import pytest
from u8co_events.diff import Scope, changes, event_id, snapshot_of
from u8co_events.entities import EntityCommit, EntitySnap
from u8co_events.readonly import ReadOnlyState
from u8co_events.source import make_event
from u8co_events.state import SCHEMA_VERSION, Resync, Store

from fakes import doc

SCOPE = Scope("999", "sale_order", "2026-09-28T01:02:03Z")


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "state" / "s.db")
    yield s
    s.close()


def test_pragmas_and_permissions(tmp_path) -> None:
    path = tmp_path / "state" / "s.db"
    s = Store(path)
    try:
        db = s._db
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
    finally:
        s.close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_commit_cycle_is_atomic_and_ordered(store: Store) -> None:
    upserts, events = changes(SCOPE, [doc(1, 10), doc(2, 11)], {}, emit_created=True)
    store.commit_cycle(SCOPE, "11", upserts, events)
    assert store.watermark("999", "sale_order") == "11"
    rows = store.pending_outbox(10)
    assert [r.doc_id for r in rows] == [1, 2]
    assert json.loads(rows[0].payload)["event_id"] == rows[0].event_id
    assert store.all_snapshot_ids("999", "sale_order") == {1, 2}
    # 同一个 event_id 还没发出去时不重复进发件箱。
    store.commit_cycle(SCOPE, "12", [], events)
    assert store.outbox_size() == 2
    store.mark_sent([rows[0].seq])
    assert [r.doc_id for r in store.pending_outbox(10)] == [2]


def test_failed_commit_leaves_nothing(store: Store) -> None:
    upserts, events = changes(SCOPE, [doc(1, 10)], {}, emit_created=True)
    broken = [dict(events[0], id="not-an-int")]
    with pytest.raises(ValueError):
        store.commit_cycle(SCOPE, "10", upserts, broken + events)
    assert store.watermark("999", "sale_order") is None
    assert store.outbox_size() == 0
    assert store.all_snapshot_ids("999", "sale_order") == set()
    # 连接仍然可用。
    store.commit_cycle(SCOPE, "10", upserts, events)
    assert store.outbox_size() == 1


def test_scan_commit_and_status(store: Store) -> None:
    store.register("999", "sale_order")
    upserts, _ = changes(SCOPE, [doc(1, 10), doc(2, 11)], {}, emit_created=False)
    store.commit_cycle(SCOPE, "11", upserts, [])
    store.commit_scan(SCOPE, [2], [])
    assert store.all_snapshot_ids("999", "sale_order") == {1}
    status = store.status()[0]
    assert status.watermark == "11"
    assert status.snapshots == 1
    assert status.last_scan_at == SCOPE.detected_at
    store.record_error(SCOPE, "boom")
    assert store.status()[0].last_error == "boom"
    assert store.status()[0].watermark == "11"


def test_snapshots_lookup_in_chunks(store: Store) -> None:
    rows = [doc(i, i) for i in range(1, 1201)]
    upserts, _ = changes(SCOPE, rows, {}, emit_created=False)
    store.commit_cycle(SCOPE, "1200", upserts, [])
    found = store.snapshots("999", "sale_order", list(range(1, 1300)))
    assert len(found) == 1200
    assert found[7] == snapshot_of(doc(7, 7))


def test_committed_outbox_survives_hard_kill(tmp_path) -> None:
    # 子进程提交一轮后直接 os._exit（不 close、不 checkpoint），模拟断电前一刻。
    path = tmp_path / "s.db"
    script = textwrap.dedent(
        f"""
        import os
        from fakes import doc
        from u8co_events.diff import Scope, changes
        from u8co_events.state import Store
        scope = Scope("999", "sale_order", "2026-09-28T01:02:03Z")
        store = Store({str(path)!r})
        upserts, events = changes(scope, [doc(1, 10)], {{}}, emit_created=True)
        store.commit_cycle(scope, "10", upserts, events)
        store._db.execute("BEGIN IMMEDIATE")
        store._db.execute("DELETE FROM outbox")
        os._exit(9)
        """
    )
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    done = subprocess.run([sys.executable, "-c", script], env=env, cwd=os.path.dirname(__file__), check=False)
    assert done.returncode == 9
    reopened = Store(path)
    try:
        assert reopened.watermark("999", "sale_order") == "10"
        assert [r.doc_id for r in reopened.pending_outbox(10)] == [1]
    finally:
        reopened.close()


def test_schema_version_mismatch(tmp_path) -> None:
    path = tmp_path / "s.db"
    Store(path).close()
    db = sqlite3.connect(path)
    db.execute("UPDATE meta SET value = '99' WHERE key = 'schema_version'")
    db.commit()
    db.close()
    with pytest.raises(RuntimeError, match="版本"):
        Store(path)


def test_is_fresh(store: Store) -> None:
    store.register("999", "sale_order")
    assert store.is_fresh()
    store.commit_cycle(SCOPE, "1", [], [])
    assert not store.is_fresh()


def test_backfill_only_for_types_registered_while_fresh(store: Store) -> None:
    store.register("999", "sale_order")
    store.register("999", "ar_process")
    store.commit_cycle(SCOPE, "1", [], [])
    # 库已不是新的：之后才开的类型没有回填资格；重复登记不影响先前的资格。
    store.register("999", "gl_voucher")
    store.register("999", "ar_process")
    assert store.backfill_allowed("999", "sale_order")
    assert store.backfill_allowed("999", "ar_process")
    assert not store.backfill_allowed("999", "gl_voucher")
    assert not store.backfill_allowed("888", "sale_order")


def test_status_filtered_by_configured_pairs(store: Store) -> None:
    store.register("999", "sale_order")
    store.register("999", "dispatch")
    assert [st.type for st in store.status()] == ["dispatch", "sale_order"]
    assert [st.type for st in store.status({("999", "sale_order")})] == ["sale_order"]


def test_scan_error_column(store: Store) -> None:
    store.record_error(SCOPE, "scan boom", scan=True)
    st = store.status()[0]
    assert (st.scan_error, st.last_error) == ("scan boom", None)
    store.commit_cycle(SCOPE, "1", [], [])
    assert store.status()[0].scan_error == "scan boom"
    store.commit_scan(SCOPE, [], [])
    assert store.status()[0].scan_error is None


def test_commit_resync(store: Store) -> None:
    upserts, _ = changes(SCOPE, [doc(1, 10), doc(2, 11)], {}, emit_created=False)
    store.commit_cycle(SCOPE, "11", upserts, [])
    upserts, events = changes(SCOPE, [doc(1, 5, closed=True)], store.all_snapshots("999", "sale_order"), True)
    store.commit_resync(SCOPE, Resync("5", upserts, [2], events, "11->5"))
    st = store.status()[0]
    assert (st.watermark, st.watermark_reset, st.last_scan_at) == ("5", "11->5", SCOPE.detected_at)
    assert store.all_snapshot_ids("999", "sale_order") == {1}
    assert [r.kind for r in store.pending_outbox(10)] == ["closed"]


def test_migrates_schema_v1(tmp_path) -> None:
    path = tmp_path / "s.db"
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '1');
        CREATE TABLE type_state (account TEXT NOT NULL, type TEXT NOT NULL, watermark TEXT, last_poll_at TEXT,
            last_ok_at TEXT, last_scan_at TEXT, last_error TEXT, PRIMARY KEY (account, type)) WITHOUT ROWID;
        INSERT INTO type_state (account, type, watermark) VALUES ('999', 'sale_order', '42');
        """
    )
    db.close()
    s = Store(path)
    try:
        st = s.status()[0]
        assert (st.watermark, st.scan_error, st.watermark_reset) == ("42", None, None)
        assert s._db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == SCHEMA_VERSION
    finally:
        s.close()


# ---- 第 4 版：entity_snap、outbox.entity_key ----

PROC = Scope("999", "ar_process", "2026-10-02T01:02:03Z")


def _snap(key: str, fingerprint: str, **state: object) -> EntitySnap:
    return EntitySnap(key=key, fingerprint=fingerprint, state=state, doc_id=int(fingerprint), code=f"HX{key}")


def test_voucher_outbox_carries_entity_key(store: Store) -> None:
    upserts, events = changes(SCOPE, [doc(7, 10)], {}, emit_created=True)
    store.commit_cycle(SCOPE, "10", upserts, events)
    assert store.pending_outbox(1)[0].entity_key == "7"


def test_commit_entities_roundtrip(store: Store) -> None:
    store.register("999", "ar_process")
    first = _snap("9P|0001", "101", rows=2, partners=["C001"])
    event = make_event(PROC, "processed", None, first)
    store.commit_entities(PROC, EntityCommit(upserts=[first], events=[event], watermark="500", scanned=True))
    assert store.all_entity_snaps("999", "ar_process") == {"9P|0001": first}
    assert store.entity_snaps("999", "ar_process", ["9P|0001", "missing"]) == {"9P|0001": first}
    row = store.pending_outbox(1)[0]
    assert (row.type, row.kind, row.doc_id, row.entity_key) == ("ar_process", "processed", 101, "9P|0001")
    body = json.loads(row.payload)
    assert (body["id"], body["code"], body["ufts"], body["prev"]) == (101, "HX9P|0001", "101", None)
    assert body["curr"] == {"rows": 2, "partners": ["C001"]}
    st = store.status({("999", "ar_process")})[0]
    assert (st.watermark, st.snapshots) == ("500", 1)
    assert st.last_ok_at == st.last_scan_at == PROC.detected_at
    assert not store.is_fresh()


def test_commit_entities_keeps_watermark_and_deletes(store: Store) -> None:
    store.commit_entities(PROC, EntityCommit(upserts=[_snap("a", "1"), _snap("b", "2")], watermark="9"))
    store.record_error(PROC, "boom")
    gone = make_event(PROC, "cancelled", _snap("b", "2"), None)
    store.commit_entities(PROC, EntityCommit(deleted=["b"], events=[gone], reset_note="9->3"))
    st = store.status()[0]
    assert (st.watermark, st.last_error, st.watermark_reset) == ("9", None, "9->3")
    assert set(store.all_entity_snaps("999", "ar_process")) == {"a"}
    assert json.loads(store.pending_outbox(1)[0].payload)["prev"] == {}


def test_commit_entities_scan_only(store: Store) -> None:
    store.record_error(PROC, "scan boom", scan=True)
    store.commit_entities(PROC, EntityCommit(polled=False, scanned=True))
    st = store.status()[0]
    assert (st.scan_error, st.last_scan_at, st.last_ok_at) == (None, PROC.detected_at, None)


def test_failed_entity_commit_leaves_nothing(store: Store) -> None:
    bad = make_event(PROC, "processed", None, _snap("a", "1"))
    bad.payload["id"] = "x"
    with pytest.raises(ValueError):
        store.commit_entities(PROC, EntityCommit(upserts=[_snap("a", "1")], events=[bad], watermark="1"))
    assert store.all_entity_snaps("999", "ar_process") == {}
    assert store.outbox_size() == 0
    assert store.watermark("999", "ar_process") is None


def test_event_id_is_deterministic() -> None:
    one = make_event(PROC, "vouchered", _snap("k", "1"), _snap("k", "2"))
    two = make_event(PROC, "vouchered", _snap("k", "1"), _snap("k", "2"))
    other = make_event(PROC, "unvouchered", _snap("k", "1"), _snap("k", "2"))
    assert one.payload["event_id"] == two.payload["event_id"] != other.payload["event_id"]
    assert one.key == "k"
    # 单据的 event_id 与第 3 版相同（键为 id 的十进制，指纹为 ufts）。
    _, events = changes(SCOPE, [doc(3, 30)], {}, emit_created=True)
    assert events[0]["event_id"] == event_id(SCOPE, "3", "created", "30") == event_id(SCOPE, 3, "created", "30")


def _downgrade_to_v3(path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        """
        DROP TABLE entity_snap;
        ALTER TABLE outbox DROP COLUMN entity_key;
        UPDATE meta SET value = '3' WHERE key = 'schema_version';
        """
    )
    db.close()


def test_migrates_schema_v3_to_v4(tmp_path) -> None:
    path = tmp_path / "s.db"
    s = Store(path)
    upserts, events = changes(SCOPE, [doc(1, 10)], {}, emit_created=True)
    s.commit_cycle(SCOPE, "10", upserts, events)
    wf = Scope("999", "qm_incoming_check", SCOPE.detected_at)
    wf_rows, _ = changes(wf, [doc(5, 5)], {}, emit_created=False)
    s.commit_cycle(wf, "5", wf_rows, [])
    s.close()
    _downgrade_to_v3(path)
    s = Store(path)
    try:
        assert s._db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == SCHEMA_VERSION
        assert SCHEMA_VERSION == "4"
        # 已有数据原样保留；第 3 版的发件箱行没有 entity_key。
        assert s.watermark("999", "sale_order") == "10"
        assert s.all_snapshot_ids("999", "sale_order") == {1}
        assert [(r.doc_id, r.entity_key) for r in s.pending_outbox(10)] == [(1, None)]
        # 从第 3 版升级不再做审批流静默补齐。
        assert not s.wf_refill_pending("999", "qm_incoming_check")
        s.commit_entities(PROC, EntityCommit(upserts=[_snap("a", "1")]))
        assert set(s.all_entity_snaps("999", "ar_process")) == {"a"}
    finally:
        s.close()
    # 再打开一次不再迁移，也不出错。
    Store(path).close()


def test_readonly_status_counts_entity_snaps(tmp_path) -> None:
    path = tmp_path / "s.db"
    s = Store(path)
    s.commit_entities(PROC, EntityCommit(upserts=[_snap("a", "1"), _snap("b", "2")], watermark="2"))
    s.close()
    ro = ReadOnlyState(path)
    try:
        assert [(st.type, st.snapshots, st.watermark) for st in ro.status()] == [("ar_process", 2, "2")]
    finally:
        ro.close()
