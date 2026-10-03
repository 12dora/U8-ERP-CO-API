"""附加数据源（应收应付处理、基础档案、总账凭证）的快照：entity_snap 表。混入 state.Store。

单据快照（snapshot 表）按整数 id；这些数据源的主键是文本（处理批次、档案编码、凭证号），状态各不相同，
所以另存一张表：（账套, 类型, 键）→ 指纹 + 状态 JSON。事件、快照、水位照旧在同一个事务里写（commit_entities）。
发件箱的 entity_key 记文本键；doc_id 是天然整数主键（没有时为 0）。
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Any

from u8co_events.diff import Scope, payload_text

_CHUNK = 500
_COLS = "key, fingerprint, state, doc_id, code"
_UPSERT = (
    "INSERT INTO entity_snap (account, type, key, fingerprint, state, doc_id, code) VALUES (?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT (account, type, key) DO UPDATE SET fingerprint = excluded.fingerprint, state = excluded.state, "
    "doc_id = excluded.doc_id, code = excluded.code"
)
_OUTBOX = (
    "INSERT OR IGNORE INTO outbox (event_id, account, type, kind, doc_id, payload, created_at, entity_key) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)


@dataclass(frozen=True)
class EntitySnap:
    """一个键上一次看到的样子。fingerprint 变了才算有变化；state 是事件里的 prev / curr。"""

    key: str
    fingerprint: str
    state: Mapping[str, Any] = field(default_factory=dict)
    # 事件信封里的 id（天然整数主键，没有为 0）和 code（给人看的编号）。
    doc_id: int = 0
    code: str | None = None


@dataclass(frozen=True)
class EntityEvent:
    """一条事件：payload 是事件信封（与单据事件同一套字段），key 记进发件箱的 entity_key。"""

    key: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class EntityCommit:
    """一轮（增量、摘要或扫描）的全部写入，同一个事务。"""

    upserts: Sequence[EntitySnap] = ()
    deleted: Sequence[str] = ()
    events: Sequence[EntityEvent] = ()
    # None 表示水位不动。
    watermark: str | None = None
    # 成功轮询：记 last_poll_at、last_ok_at，清 last_error。
    polled: bool = True
    # 完整扫描（能据此发删除事件的那种）：记 last_scan_at，清 scan_error。
    scanned: bool = False
    # 水位倒退时的「旧水位->新水位」：记 watermark_reset_at、watermark_reset，健康检查 24 小时内报警。
    reset_note: str | None = None


def _snap(row: Sequence[Any]) -> EntitySnap:
    state = json.loads(row[2]) if row[2] else {}
    return EntitySnap(key=row[0], fingerprint=row[1], state=state, doc_id=int(row[3]), code=row[4])


class EntityStoreMixin:
    """给 Store 加 entity_snap 的读写。用 Store 的连接和事务，与发件箱、type_state 同库同事务。"""

    _db: sqlite3.Connection
    _tx: Callable[[], AbstractContextManager[sqlite3.Connection]]

    def entity_snaps(self, acc: str, type_: str, keys: Sequence[str]) -> dict[str, EntitySnap]:
        found: dict[str, EntitySnap] = {}
        unique = sorted(set(keys))
        for start in range(0, len(unique), _CHUNK):
            part = unique[start : start + _CHUNK]
            marks = ",".join("?" * len(part))
            rows = self._db.execute(
                f"SELECT {_COLS} FROM entity_snap WHERE account = ? AND type = ? AND key IN ({marks})",
                (acc, type_, *part),
            ).fetchall()
            found.update((row[0], _snap(row)) for row in rows)
        return found

    def all_entity_snaps(self, acc: str, type_: str) -> dict[str, EntitySnap]:
        rows = self._db.execute(
            f"SELECT {_COLS} FROM entity_snap WHERE account = ? AND type = ?", (acc, type_)
        ).fetchall()
        return {row[0]: _snap(row) for row in rows}

    def commit_entities(self, scope: Scope, batch: EntityCommit) -> None:
        """事件进发件箱、快照增删、水位与状态，同一个事务。"""
        with self._tx() as db:
            db.executemany(_OUTBOX, [_outbox_params(scope, ev) for ev in batch.events])
            db.executemany(_UPSERT, [_snap_params(scope, snap) for snap in batch.upserts])
            db.executemany(
                "DELETE FROM entity_snap WHERE account = ? AND type = ? AND key = ?",
                [(scope.acc, scope.type, key) for key in batch.deleted],
            )
            db.execute(
                "INSERT INTO type_state (account, type) VALUES (?, ?) ON CONFLICT (account, type) DO NOTHING",
                (scope.acc, scope.type),
            )
            _mark_state(db, scope, batch)


def _mark_state(db: sqlite3.Connection, scope: Scope, batch: EntityCommit) -> None:
    sets = ["watermark = COALESCE(?, watermark)"]
    params: list[Any] = [batch.watermark]
    if batch.polled:
        sets.append("last_poll_at = ?, last_ok_at = ?, last_error = NULL")
        params += [scope.detected_at, scope.detected_at]
    if batch.scanned:
        sets.append("last_scan_at = ?, scan_error = NULL")
        params.append(scope.detected_at)
    if batch.reset_note is not None:
        sets.append("watermark_reset_at = ?, watermark_reset = ?")
        params += [scope.detected_at, batch.reset_note[:200]]
    db.execute(
        f"UPDATE type_state SET {', '.join(sets)} WHERE account = ? AND type = ?",
        (*params, scope.acc, scope.type),
    )


def _snap_params(scope: Scope, snap: EntitySnap) -> tuple[Any, ...]:
    state = json.dumps(dict(snap.state), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (scope.acc, scope.type, snap.key, snap.fingerprint, state, int(snap.doc_id), snap.code)


def _outbox_params(scope: Scope, ev: EntityEvent) -> tuple[Any, ...]:
    body = ev.payload
    return (
        body["event_id"],
        body["account"],
        body["type"],
        body["kind"],
        int(body["id"]),
        payload_text(body),
        scope.detected_at,
        ev.key,
    )

