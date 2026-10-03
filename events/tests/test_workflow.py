"""审批流事件（kind=workflow）：质量单据的 wf_state、current_auditor 变化，以及旧状态库升级。"""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime

import pytest
from u8co_events.diff import Scope, change_kinds, changes, snapshot_of
from u8co_events.poller import Poller
from u8co_events.state import SCHEMA_VERSION, Store

from fakes import FakeLister, doc, make_config

QM = "qm_product_check"
SCOPE = Scope("999", QM, "2026-09-28T01:02:03Z")


def qm(doc_id: int, ufts: int, **state) -> dict:
    row = doc(doc_id, ufts, wf=True, wf_state="0", current_auditor=None)
    row.update(state)
    return row


def _kinds(prev: dict, curr: dict) -> list[str]:
    return change_kinds(snapshot_of(prev), snapshot_of(curr))


def test_submit_emits_workflow_only() -> None:
    upserts, events = changes(
        SCOPE,
        [qm(1, 101, wf_state="1", current_auditor="审批人乙")],
        {1: snapshot_of(qm(1, 100))},
        emit_created=True,
    )
    assert [e["kind"] for e in events] == ["workflow"]
    event = events[0]
    assert event["prev"]["wf_state"] == 0
    assert event["prev"]["current_auditor"] is None
    assert event["curr"]["wf_state"] == 1
    assert event["curr"]["current_auditor"] == "审批人乙"
    assert event["event_id"] == hashlib.sha256(f"999|{QM}|1|workflow|101".encode()).hexdigest()
    assert upserts[0].wf_state == 1


@pytest.mark.parametrize(
    ("prev", "curr", "kinds"),
    [
        ({"wf_state": "1", "current_auditor": "甲"}, {"wf_state": "1", "current_auditor": "乙"}, ["workflow"]),
        (
            {"wf_state": "1", "current_auditor": "乙"},
            {"wf_state": "2", "verified": True, "verifier": "乙"},
            ["verified", "workflow"],
        ),
        ({"wf_state": "1", "current_auditor": "甲"}, {"wf_state": "-1"}, ["workflow"]),
        ({"wf_state": "2", "verified": True}, {"wf_state": "0"}, ["unverified", "workflow"]),
        (
            {"wf_state": "1", "current_auditor": "甲"},
            {"wf_state": "1", "current_auditor": "甲", "code": "X"},
            ["modified"],
        ),
    ],
)
def test_workflow_kinds(prev, curr, kinds) -> None:
    assert _kinds(qm(1, 100, **prev), qm(1, 101, **curr)) == kinds


def test_other_types_carry_no_workflow_fields() -> None:
    snap = snapshot_of(doc(1, 100))
    assert not snap.wf_known
    assert set(snap.state()) == {"verified", "closed", "red", "verifier", "closer"}
    assert set(snapshot_of(qm(1, 100)).state()) >= {"wf_state", "current_auditor"}


def test_old_snapshot_is_filled_silently() -> None:
    old = snapshot_of(doc(1, 100))
    upserts, events = changes(SCOPE, [qm(1, 100, wf_state="1", current_auditor="乙")], {1: old}, True)
    assert events == []
    assert upserts[0].wf_known and upserts[0].wf_state == 1
    # ufts 也变了：只按原有字段判断，发 modified，不发 workflow。
    _, events = changes(SCOPE, [qm(1, 105, wf_state="1", current_auditor="乙")], {1: old}, True)
    assert [e["kind"] for e in events] == ["modified"]


def test_bridge_without_workflow_fields_is_not_a_change() -> None:
    known = {1: snapshot_of(qm(1, 100, wf_state="1", current_auditor="乙"))}
    upserts, events = changes(SCOPE, [doc(1, 100)], known, True)
    assert events == []
    assert [s.wf_known for s in upserts] == [False]


@pytest.mark.parametrize(("value", "parsed"), [("-1", -1), (" 2 ", 2), (1, 1), (None, None), ("", None)])
def test_wf_state_parsing(value, parsed) -> None:
    assert snapshot_of(qm(1, 100, wf_state=value)).wf_state == parsed


@pytest.mark.parametrize("value", ["x", True, 1.5])
def test_wf_state_rejects_garbage(value) -> None:
    with pytest.raises(ValueError, match="wf_state"):
        snapshot_of(qm(1, 100, wf_state=value))


def test_store_round_trip(tmp_path) -> None:
    store = Store(tmp_path / "s.db")
    try:
        snap = snapshot_of(qm(1, 100, wf_state="-1", current_auditor="乙"))
        plain = snapshot_of(doc(2, 100))
        store.commit_cycle(SCOPE, "100", [snap, plain], [])
        assert store.all_snapshots("999", QM) == {1: snap, 2: plain}
    finally:
        store.close()


def _v2_database(path) -> None:
    db = sqlite3.connect(path)
    db.executescript(
        f"""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT INTO meta VALUES ('schema_version', '2');
        CREATE TABLE type_state (account TEXT NOT NULL, type TEXT NOT NULL, watermark TEXT, last_poll_at TEXT,
            last_ok_at TEXT, last_scan_at TEXT, last_error TEXT, scan_error TEXT, watermark_reset_at TEXT,
            watermark_reset TEXT, PRIMARY KEY (account, type)) WITHOUT ROWID;
        INSERT INTO type_state (account, type, watermark) VALUES ('999', '{QM}', '100');
        CREATE TABLE snapshot (account TEXT NOT NULL, type TEXT NOT NULL, id INTEGER NOT NULL, code TEXT,
            ufts TEXT NOT NULL, verified INTEGER NOT NULL, closed INTEGER NOT NULL, red INTEGER NOT NULL,
            verifier TEXT, closer TEXT, PRIMARY KEY (account, type, id)) WITHOUT ROWID;
        INSERT INTO snapshot VALUES ('999', '{QM}', 1, 'SO0001', '100', 0, 0, 0, NULL, NULL);
        INSERT INTO snapshot VALUES ('999', '{QM}', 2, 'SO0002', '100', 0, 0, 0, NULL, NULL);
        INSERT INTO snapshot VALUES ('999', 'sale_order', 3, 'SO0003', '100', 0, 0, 0, NULL, NULL);
        """
    )
    db.close()


def test_migrates_schema_v2_without_spurious_events(tmp_path) -> None:
    path = tmp_path / "s.db"
    _v2_database(path)
    store = Store(path)
    try:
        assert store._db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == SCHEMA_VERSION
        known = store.all_snapshots("999", QM)
        assert not known[1].wf_known
        assert store.wf_refill_pending("999", QM)
        assert not store.wf_refill_pending("999", "sale_order")
        upserts, events = changes(SCOPE, [qm(1, 100, wf_state="1", current_auditor="乙")], known, True)
        assert events == []
        store.commit_cycle(SCOPE, "100", upserts, events)
        assert store.all_snapshots("999", QM)[1].current_auditor == "乙"
    finally:
        store.close()


def test_poller_emits_workflow(tmp_path) -> None:
    path = str(tmp_path / "s.db")
    store = Store(path)
    lister = FakeLister()
    accounts = [{"acc": "999", "operator_file": "/run/secrets/op.json", "types": [QM]}]
    cfg = make_config(path, accounts=accounts, delete_scan_minutes=0)
    poller = Poller(cfg, store, lister, now=lambda: datetime(2026, 9, 28, 1, 0, 0, tzinfo=UTC))
    try:
        lister.put(QM, qm(1, 10))
        poller.run_once()
        lister.put(QM, qm(1, 11, wf_state="1", current_auditor="乙"))
        poller.run_once()
        lister.put(QM, qm(1, 12, wf_state="1", current_auditor="丙"))
        poller.run_once()
        rows = store.pending_outbox(10)
        assert [row.kind for row in rows] == ["workflow", "workflow"]
    finally:
        store.close()


class _FailFirst(FakeLister):
    """只有第一次调用失败（升级后补齐那次），之后照常。"""

    def list_page(self, acc, request):
        if not self.calls:
            self.calls.append((acc, request))
            raise ConnectionError("假桥：连接断开")
        return super().list_page(acc, request)


def _upgraded_poller(tmp_path, lister: FakeLister | None = None) -> tuple[Store, FakeLister, Poller]:
    path = tmp_path / "s.db"
    _v2_database(path)
    store = Store(path)
    lister = lister or FakeLister()
    accounts = [{"acc": "999", "operator_file": "/run/secrets/op.json", "types": [QM]}]
    cfg = make_config(str(path), accounts=accounts, delete_scan_minutes=0)
    return store, lister, Poller(cfg, store, lister, now=lambda: datetime(2026, 9, 28, 1, 0, 0, tzinfo=UTC))


def test_upgrade_refills_silently_then_emits_workflow(tmp_path) -> None:
    store, lister, poller = _upgraded_poller(tmp_path)
    try:
        # 升级前已在审批中、升级后没动过的单据：第一轮整轮补齐，不发事件，水位不动。
        lister.put(QM, qm(1, 100, wf_state="1", current_auditor="乙"))
        lister.put(QM, qm(2, 100))
        poller.run_once()
        assert store.pending_outbox(10) == []
        assert not store.wf_refill_pending("999", QM)
        snaps = store.all_snapshots("999", QM)
        assert snaps[1].wf_known and snaps[1].wf_state == 1 and snaps[1].current_auditor == "乙"
        assert snaps[1].ufts == "100"
        assert store.watermark("999", QM) == "100"
        assert [req.changed_since for _, req in lister.calls] == ["", "100"]
        # 下一次审批变化发 workflow（补齐之前会是 modified）。
        lister.put(QM, qm(1, 101, wf_state="2", verified=True, verifier="乙"))
        poller.run_once()
        rows = store.pending_outbox(10)
        assert [row.kind for row in rows] == ["verified", "workflow"]
        # 只补一次：之后的轮询不再整轮读取。
        assert [req.changed_since for _, req in lister.calls][2:] == ["100"]
    finally:
        store.close()


def test_upgrade_refill_retries_after_failure(tmp_path) -> None:
    store, lister, poller = _upgraded_poller(tmp_path)
    try:
        lister.put(QM, qm(1, 100, wf_state="1", current_auditor="乙"))
        lister.fail_at = 1
        poller.run_once()
        assert store.wf_refill_pending("999", QM)
        lister.fail_at = None
        poller.run_once()
        assert not store.wf_refill_pending("999", QM)
        assert store.pending_outbox(10) == []
    finally:
        store.close()


def test_refill_waits_for_bridge_with_workflow_fields(tmp_path) -> None:
    store, lister, poller = _upgraded_poller(tmp_path)
    try:
        # 旧版桥：行里没有 wf_state / current_auditor，不算补齐，标记留着；增量照常。
        lister.put(QM, doc(1, 100))
        poller.run_once()
        assert store.wf_refill_pending("999", QM)
        assert store.pending_outbox(10) == []
        assert [req.changed_since for _, req in lister.calls] == ["", "100"]
        # 桥升级后下一轮补齐。
        lister.put(QM, qm(1, 100, wf_state="1", current_auditor="乙"))
        poller.run_once()
        assert not store.wf_refill_pending("999", QM)
        assert store.all_snapshots("999", QM)[1].current_auditor == "乙"
        assert store.pending_outbox(10) == []
    finally:
        store.close()


def test_refill_failure_does_not_block_incremental_poll(tmp_path) -> None:
    store, lister, poller = _upgraded_poller(tmp_path, _FailFirst())
    try:
        lister.put(QM, qm(1, 101, wf_state="1", current_auditor="乙"))
        poller.run_once()
        # 补齐失败：标记留着；同一轮的增量照常推进水位、发事件。
        assert store.wf_refill_pending("999", QM)
        assert [req.changed_since for _, req in lister.calls] == ["", "100"]
        assert store.watermark("999", QM) == "101"
        assert [row.kind for row in store.pending_outbox(10)] == ["modified"]
        poller.run_once()
        assert not store.wf_refill_pending("999", QM)
        assert [row.kind for row in store.pending_outbox(10)] == ["modified"]
    finally:
        store.close()
