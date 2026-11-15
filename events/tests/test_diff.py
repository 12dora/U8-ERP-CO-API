from __future__ import annotations

import hashlib

import pytest
from u8co_events.diff import Scope, change_kinds, changes, deletions, event_id, snapshot_of

from fakes import doc

SCOPE = Scope("999", "sale_order", "2026-09-28T01:02:03Z")


def _snap(**state):
    return snapshot_of(doc(1, 100, **state))


def test_event_id_is_deterministic() -> None:
    expected = hashlib.sha256(b"999|sale_order|1|created|100").hexdigest()
    assert event_id(SCOPE, 1, "created", "100") == expected


def test_created_and_backfill() -> None:
    upserts, events = changes(SCOPE, [doc(1, 100)], {}, emit_created=True)
    assert [s.id for s in upserts] == [1]
    assert events[0]["kind"] == "created"
    assert events[0]["prev"] is None
    assert events[0]["curr"]["verified"] is False
    assert set(events[0]) == {
        "event_id",
        "account",
        "type",
        "id",
        "code",
        "kind",
        "ufts",
        "detected_at",
        "prev",
        "curr",
    }
    upserts, events = changes(SCOPE, [doc(1, 100)], {}, emit_created=False)
    assert [s.id for s in upserts] == [1]
    assert events == []


@pytest.mark.parametrize(
    ("prev", "curr", "kinds"),
    [
        ({}, {"verified": True, "verifier": "张三"}, ["verified"]),
        ({"verified": True}, {}, ["unverified"]),
        ({}, {"closed": True, "closer": "张三"}, ["closed"]),
        ({"closed": True}, {}, ["opened"]),
        ({}, {"verified": True, "closed": True}, ["verified", "closed"]),
        ({}, {"code": "X"}, ["modified"]),
    ],
)
def test_change_kinds(prev, curr, kinds) -> None:
    assert change_kinds(_snap(**prev), snapshot_of(doc(1, 101, **curr))) == kinds


def test_unchanged_row_gives_nothing() -> None:
    known = {1: _snap()}
    assert changes(SCOPE, [doc(1, 100)], known, emit_created=True) == ([], [])


def test_modified_when_only_ufts_moves() -> None:
    known = {1: _snap()}
    upserts, events = changes(SCOPE, [doc(1, 105)], known, emit_created=True)
    assert upserts[0].ufts == "105"
    assert [e["kind"] for e in events] == ["modified"]
    assert events[0]["event_id"] == event_id(SCOPE, 1, "modified", "105")


def test_deletions() -> None:
    known = {1: _snap(), 2: snapshot_of(doc(2, 200))}
    gone, events = deletions(SCOPE, known, {1})
    assert gone == [2]
    assert events[0]["kind"] == "deleted"
    assert events[0]["curr"] is None
    assert events[0]["ufts"] == "200"
    assert events[0]["event_id"] == hashlib.sha256(b"999|sale_order|2|deleted|200").hexdigest()


def test_row_without_id_is_rejected() -> None:
    with pytest.raises(ValueError):
        snapshot_of({"ufts": "1"})
    with pytest.raises(ValueError):
        snapshot_of({"id": 1, "ufts": None})
