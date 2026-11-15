"""持久状态：每个（账套, 类型）的水位、每张单据的快照、发件箱。SQLite 单文件。

断电不能丢事件，所以：
- WAL + synchronous=FULL，每次提交都把 WAL 落盘；
- 一轮对比的事件、快照、水位在同一个事务里写，要么全在，要么全不在；
- 发件箱的行只在发布成功之后删除，中途断电重启后会重发（至少一次，消费方按 event_id 去重）。

Store 不是线程安全的：每个线程自己开一个 Store(path)，WAL 允许同时读和一个写。
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Collection, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from co.client.u8co_kinds import WORKFLOW_KINDS

from u8co_events.diff import Scope, Snapshot, payload_text
from u8co_events.entities import EntityStoreMixin

SCHEMA_VERSION = "4"
# 后加的列：第 2 版在 type_state 上，第 3 版在 snapshot 上（审批流字段），第 4 版在 outbox 上（entity_key）。
# 第 4 版另加 entity_snap 表（附加数据源的快照，见 entities.py），CREATE TABLE IF NOT EXISTS 建上即可。
# 旧库打开时补上（ALTER TABLE ADD COLUMN，不动已有数据）；旧快照的 wf_known 为 0，下次看到时只补字段，不发事件。
# 旧发件箱行的 entity_key 为 NULL，只影响诊断，发布不看它。
# 从第 1、2 版升级时，给有旧快照的审批流类型各记一条 meta「wf_refill|账套|类型」：升级后第一轮先整轮静默补齐，
# 补齐和删这条记录在同一个事务里，所以只做一次（Poller._refill）。
_REFILL_KEY = "wf_refill|{}|{}"
# 回填资格：状态库全新时登记的（账套, 类型）各记一条 meta「backfill|账套|类型」，backfill_events 只对这些类型生效。
# 之后才开的数据源、类型（库已不是新的）登记时不记，首轮只记快照，不会把已有数据整批当作新事件发出。
_BACKFILL_KEY = "backfill|{}|{}"
_ADDED_COLUMNS = (
    ("type_state", "scan_error", "TEXT"),
    ("type_state", "watermark_reset_at", "TEXT"),
    ("type_state", "watermark_reset", "TEXT"),
    ("snapshot", "wf_known", "INTEGER NOT NULL DEFAULT 0"),
    ("snapshot", "wf_state", "INTEGER"),
    ("snapshot", "current_auditor", "TEXT"),
    ("outbox", "entity_key", "TEXT"),
)

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS type_state (
        account TEXT NOT NULL,
        type TEXT NOT NULL,
        watermark TEXT,
        last_poll_at TEXT,
        last_ok_at TEXT,
        last_scan_at TEXT,
        last_error TEXT,
        scan_error TEXT,
        watermark_reset_at TEXT,
        watermark_reset TEXT,
        PRIMARY KEY (account, type)
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS snapshot (
        account TEXT NOT NULL,
        type TEXT NOT NULL,
        id INTEGER NOT NULL,
        code TEXT,
        ufts TEXT NOT NULL,
        verified INTEGER NOT NULL,
        closed INTEGER NOT NULL,
        red INTEGER NOT NULL,
        verifier TEXT,
        closer TEXT,
        wf_known INTEGER NOT NULL DEFAULT 0,
        wf_state INTEGER,
        current_auditor TEXT,
        PRIMARY KEY (account, type, id)
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS outbox (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        account TEXT NOT NULL,
        type TEXT NOT NULL,
        kind TEXT NOT NULL,
        doc_id INTEGER NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        entity_key TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS entity_snap (
        account TEXT NOT NULL,
        type TEXT NOT NULL,
        key TEXT NOT NULL,
        fingerprint TEXT NOT NULL,
        state TEXT NOT NULL,
        doc_id INTEGER NOT NULL DEFAULT 0,
        code TEXT,
        PRIMARY KEY (account, type, key)
    ) WITHOUT ROWID""",
)

_UPSERT = (
    "INSERT INTO snapshot (account, type, id, code, ufts, verified, closed, red, verifier, closer, "
    "wf_known, wf_state, current_auditor) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT (account, type, id) DO UPDATE SET code = excluded.code, ufts = excluded.ufts, "
    "verified = excluded.verified, closed = excluded.closed, red = excluded.red, "
    "verifier = excluded.verifier, closer = excluded.closer, wf_known = excluded.wf_known, "
    "wf_state = excluded.wf_state, current_auditor = excluded.current_auditor"
)
_OUTBOX = (
    "INSERT OR IGNORE INTO outbox (event_id, account, type, kind, doc_id, payload, created_at, entity_key) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)
# 每个（账套, 类型）的快照数：单据在 snapshot，附加数据源在 entity_snap（readonly.py 用同一句）。
STATUS_SQL = (
    "SELECT t.account, t.type, t.watermark, "
    "(SELECT COUNT(*) FROM snapshot s WHERE s.account = t.account AND s.type = t.type) + "
    "(SELECT COUNT(*) FROM entity_snap e WHERE e.account = t.account AND e.type = t.type), "
    "t.last_poll_at, t.last_ok_at, t.last_scan_at, t.last_error, t.scan_error, "
    "t.watermark_reset_at, t.watermark_reset "
    "FROM type_state t ORDER BY t.account, t.type"
)
_SNAP_COLS = "id, code, ufts, verified, closed, red, verifier, closer, wf_known, wf_state, current_auditor"
# SQLite 单条语句的参数个数有上限，IN 列表分批查。
_CHUNK = 500


@dataclass(frozen=True)
class OutboxRow:
    seq: int
    event_id: str
    account: str
    type: str
    kind: str
    doc_id: int
    payload: str
    # 实体键（单据为 id 的十进制，附加数据源为文本键）；第 4 版之前进发件箱的为 None。
    entity_key: str | None = None


@dataclass(frozen=True)
class TypeStatus:
    account: str
    type: str
    watermark: str | None
    snapshots: int
    last_poll_at: str | None
    last_ok_at: str | None
    last_scan_at: str | None
    last_error: str | None
    # 最近一次删除扫描失败的原因；扫描成功后清空（增量轮询成功不清它）。
    scan_error: str | None = None
    # 最近一次发现水位倒退（库被还原等）的时间和「旧水位->新水位」，不自动清除。
    watermark_reset_at: str | None = None
    watermark_reset: str | None = None


@dataclass(frozen=True)
class Resync:
    """水位倒退后的整轮重对比：快照、删除、事件、新水位一起提交。"""

    watermark: str
    upserts: Sequence[Snapshot]
    deleted_ids: Sequence[int]
    events: Sequence[dict[str, Any]]
    note: str


def _snapshot(row: Sequence[Any]) -> Snapshot:
    return Snapshot(
        id=int(row[0]),
        code=row[1],
        ufts=str(row[2]),
        verified=bool(row[3]),
        closed=bool(row[4]),
        red=bool(row[5]),
        verifier=row[6],
        closer=row[7],
        wf_known=bool(row[8]),
        wf_state=None if row[9] is None else int(row[9]),
        current_auditor=row[10],
    )


def _prepare_file(path: Path) -> None:
    # 目录 0700，库文件 0600。SQLite 建 -wal、-shm 时沿用库文件的权限。
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(fd)


class Store(EntityStoreMixin):
    def __init__(self, path: str | Path) -> None:
        target = Path(path)
        _prepare_file(target)
        self._db = sqlite3.connect(str(target), isolation_level=None, timeout=30)
        self._db.execute("PRAGMA busy_timeout = 30000")
        mode = self._db.execute("PRAGMA journal_mode = WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            self._db.close()
            raise RuntimeError(f"状态库 {target} 开不了 WAL 模式（得到 {mode}），不能保证断电不丢事件")
        self._db.execute("PRAGMA synchronous = FULL")
        with self._tx():
            for statement in _SCHEMA:
                self._db.execute(statement)
            self._migrate()
        version = self._db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0]
        if version != SCHEMA_VERSION:
            self._db.close()
            raise RuntimeError(f"状态库版本 {version} 与程序（{SCHEMA_VERSION}）不符")

    def _migrate(self) -> None:
        row = self._db.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            self._db.execute("INSERT INTO meta (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
            return
        if row[0] not in ("1", "2", "3"):
            return
        for table, name, decl in _ADDED_COLUMNS:
            have = {str(col[1]) for col in self._db.execute(f"PRAGMA table_info({table})").fetchall()}
            if name not in have:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
        if row[0] != "3":
            self._mark_refill()
        self._db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (SCHEMA_VERSION,))

    def _mark_refill(self) -> None:
        marks = ",".join("?" * len(WORKFLOW_KINDS))
        pairs = self._db.execute(
            f"SELECT DISTINCT account, type FROM snapshot WHERE wf_known = 0 AND type IN ({marks})", WORKFLOW_KINDS
        ).fetchall()
        self._db.executemany(
            "INSERT OR IGNORE INTO meta (key, value) VALUES (?, 'pending')",
            [(_REFILL_KEY.format(acc, type_),) for acc, type_ in pairs],
        )

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        # BEGIN IMMEDIATE 一开始就拿写锁，避免两个线程读完再写时撞锁。
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield self._db
            self._db.execute("COMMIT")
        except BaseException:
            # COMMIT 本身失败（磁盘满、IO 错）时事务可能还开着，一并回滚。
            if self._db.in_transaction:
                self._db.execute("ROLLBACK")
            raise

    # ---- 发布端 ----

    def pending_outbox(self, limit: int) -> list[OutboxRow]:
        rows = self._db.execute(
            "SELECT seq, event_id, account, type, kind, doc_id, payload, entity_key FROM outbox ORDER BY seq LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
        return [OutboxRow(int(r[0]), r[1], r[2], r[3], r[4], int(r[5]), r[6], r[7]) for r in rows]

    def mark_sent(self, seqs: Sequence[int]) -> None:
        if not seqs:
            return
        with self._tx() as db:
            db.executemany("DELETE FROM outbox WHERE seq = ?", [(int(seq),) for seq in seqs])

    def outbox_size(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def status(self, pairs: Collection[tuple[str, str]] | None = None) -> list[TypeStatus]:
        """每个（账套, 类型）的状态。pairs 给了就只返回这些（配置里去掉的类型留下的旧行不算）。"""
        rows = self._db.execute(STATUS_SQL).fetchall()
        result = [TypeStatus(r[0], r[1], r[2], int(r[3]), *r[4:11]) for r in rows]
        if pairs is None:
            return result
        wanted = set(pairs)
        return [st for st in result if (st.account, st.type) in wanted]

    def is_fresh(self) -> bool:
        """全新（或丢失后重建）的状态库：没有任何水位、快照（含附加数据源的）和待发事件。

        main 用它配合 Redis 判断是不是状态卷丢了：库是新的但流已存在，就不能悄悄重新回填。
        """
        row = self._db.execute(
            "SELECT (SELECT COUNT(*) FROM type_state WHERE watermark IS NOT NULL), "
            "(SELECT COUNT(*) FROM snapshot), (SELECT COUNT(*) FROM entity_snap), (SELECT COUNT(*) FROM outbox)"
        ).fetchone()
        return not any(int(value) for value in row)

    # ---- 轮询端 ----

    def register(self, acc: str, type_: str) -> None:
        """登记类型。状态库全新时同时记下回填资格（见 _BACKFILL_KEY）；服务中途重启、库仍是新的时照样补记。"""
        with self._tx() as db:
            if self.is_fresh():
                db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES (?, '1')", (_BACKFILL_KEY.format(acc, type_),))
            db.execute("INSERT OR IGNORE INTO type_state (account, type) VALUES (?, ?)", (acc, type_))

    def backfill_allowed(self, acc: str, type_: str) -> bool:
        """这个类型是否在状态库全新时登记过：backfill_events 只对这样的类型的首轮生效。"""
        key = _BACKFILL_KEY.format(acc, type_)
        return self._db.execute("SELECT 1 FROM meta WHERE key = ?", (key,)).fetchone() is not None

    def watermark(self, acc: str, type_: str) -> str | None:
        row = self._db.execute(
            "SELECT watermark FROM type_state WHERE account = ? AND type = ?", (acc, type_)
        ).fetchone()
        return None if row is None else row[0]

    def last_scan_at(self, acc: str, type_: str) -> str | None:
        row = self._db.execute(
            "SELECT last_scan_at FROM type_state WHERE account = ? AND type = ?", (acc, type_)
        ).fetchone()
        return None if row is None else row[0]

    def snapshots(self, acc: str, type_: str, ids: Sequence[int]) -> dict[int, Snapshot]:
        found: dict[int, Snapshot] = {}
        unique = sorted(set(ids))
        for start in range(0, len(unique), _CHUNK):
            part = unique[start : start + _CHUNK]
            marks = ",".join("?" * len(part))
            rows = self._db.execute(
                f"SELECT {_SNAP_COLS} FROM snapshot WHERE account = ? AND type = ? AND id IN ({marks})",
                (acc, type_, *part),
            ).fetchall()
            for row in rows:
                snap = _snapshot(row)
                found[snap.id] = snap
        return found

    def all_snapshots(self, acc: str, type_: str) -> dict[int, Snapshot]:
        rows = self._db.execute(
            f"SELECT {_SNAP_COLS} FROM snapshot WHERE account = ? AND type = ?", (acc, type_)
        ).fetchall()
        return {int(row[0]): _snapshot(row) for row in rows}

    def all_snapshot_ids(self, acc: str, type_: str) -> set[int]:
        rows = self._db.execute("SELECT id FROM snapshot WHERE account = ? AND type = ?", (acc, type_)).fetchall()
        return {int(row[0]) for row in rows}

    def commit_cycle(
        self, scope: Scope, watermark: str, upserts: Sequence[Snapshot], events: Sequence[dict[str, Any]]
    ) -> None:
        """一轮增量：事件进发件箱、快照更新、水位前移，同一个事务。"""
        with self._tx() as db:
            self._write(db, scope, upserts, (), events)
            db.execute(
                "INSERT INTO type_state (account, type, watermark, last_poll_at, last_ok_at, last_error) "
                "VALUES (?, ?, ?, ?, ?, NULL) ON CONFLICT (account, type) DO UPDATE SET "
                "watermark = excluded.watermark, last_poll_at = excluded.last_poll_at, "
                "last_ok_at = excluded.last_ok_at, last_error = NULL",
                (scope.acc, scope.type, watermark, scope.detected_at, scope.detected_at),
            )

    def wf_refill_pending(self, acc: str, type_: str) -> bool:
        """升级后这个（账套, 类型）还没做过审批流字段的静默补齐。"""
        key = _REFILL_KEY.format(acc, type_)
        return self._db.execute("SELECT 1 FROM meta WHERE key = ?", (key,)).fetchone() is not None

    def commit_refill(self, scope: Scope, upserts: Sequence[Snapshot]) -> None:
        """静默补齐：只写快照、删掉待办记录，同一个事务；不写事件、不动水位。"""
        with self._tx() as db:
            db.executemany(_UPSERT, [self._snap_params(scope, snap) for snap in upserts])
            db.execute("DELETE FROM meta WHERE key = ?", (_REFILL_KEY.format(scope.acc, scope.type),))

    def commit_scan(self, scope: Scope, deleted_ids: Sequence[int], events: Sequence[dict[str, Any]]) -> None:
        """一次完整的主键扫描：删除事件进发件箱、删快照、记扫描时间，同一个事务。"""
        with self._tx() as db:
            self._write(db, scope, (), deleted_ids, events)
            db.execute(
                "INSERT INTO type_state (account, type, last_scan_at) VALUES (?, ?, ?) "
                "ON CONFLICT (account, type) DO UPDATE SET last_scan_at = excluded.last_scan_at, scan_error = NULL",
                (scope.acc, scope.type, scope.detected_at),
            )

    def commit_resync(self, scope: Scope, batch: Resync) -> None:
        """水位倒退后的整轮重对比：事件、快照、删除、新水位、扫描时间和告警，同一个事务。"""
        with self._tx() as db:
            self._write(db, scope, batch.upserts, batch.deleted_ids, batch.events)
            db.execute(
                "INSERT INTO type_state (account, type) VALUES (?, ?) ON CONFLICT (account, type) DO NOTHING",
                (scope.acc, scope.type),
            )
            db.execute(
                "UPDATE type_state SET watermark = ?, last_poll_at = ?, last_ok_at = ?, last_scan_at = ?, "
                "last_error = NULL, scan_error = NULL, watermark_reset_at = ?, watermark_reset = ? "
                "WHERE account = ? AND type = ?",
                (
                    batch.watermark,
                    scope.detected_at,
                    scope.detected_at,
                    scope.detected_at,
                    scope.detected_at,
                    batch.note[:200],
                    scope.acc,
                    scope.type,
                ),
            )

    def record_error(self, scope: Scope, message: str, scan: bool = False) -> None:
        """记失败。scan=True 记到 scan_error（删除扫描），否则记到 last_error（增量轮询）。"""
        column = "scan_error" if scan else "last_error"
        with self._tx() as db:
            db.execute(
                f"INSERT INTO type_state (account, type, last_poll_at, {column}) VALUES (?, ?, ?, ?) "
                f"ON CONFLICT (account, type) DO UPDATE SET last_poll_at = excluded.last_poll_at, "
                f"{column} = excluded.{column}",
                (scope.acc, scope.type, scope.detected_at, message[:500]),
            )

    @classmethod
    def _write(
        cls,
        db: sqlite3.Connection,
        scope: Scope,
        upserts: Sequence[Snapshot],
        deleted_ids: Sequence[int],
        events: Sequence[dict[str, Any]],
    ) -> None:
        cls._put_events(db, scope, events)
        db.executemany(_UPSERT, [cls._snap_params(scope, snap) for snap in upserts])
        db.executemany(
            "DELETE FROM snapshot WHERE account = ? AND type = ? AND id = ?",
            [(scope.acc, scope.type, int(doc_id)) for doc_id in deleted_ids],
        )

    @staticmethod
    def _snap_params(scope: Scope, snap: Snapshot) -> tuple[Any, ...]:
        return (
            scope.acc,
            scope.type,
            snap.id,
            snap.code,
            snap.ufts,
            int(snap.verified),
            int(snap.closed),
            int(snap.red),
            snap.verifier,
            snap.closer,
            int(snap.wf_known),
            snap.wf_state,
            snap.current_auditor,
        )

    @staticmethod
    def _put_events(db: sqlite3.Connection, scope: Scope, events: Sequence[dict[str, Any]]) -> None:
        db.executemany(
            _OUTBOX,
            [
                (
                    ev["event_id"],
                    ev["account"],
                    ev["type"],
                    ev["kind"],
                    int(ev["id"]),
                    payload_text(ev),
                    scope.detected_at,
                    str(int(ev["id"])),
                )
                for ev in events
            ],
        )
