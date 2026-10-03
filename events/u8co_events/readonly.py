"""只读查看状态库：给健康检查和 status 命令用。

用 file:...?mode=ro 打开，再加 PRAGMA query_only：不建库、不建表、不迁移、不拿写锁，
和轮询线程、发布线程的写入互不影响（WAL 下读不阻塞写）。库文件不存在时直接报错，不会新建。
查询与 state.Store.status / outbox_size 共用（STATUS_SQL）。要求库已是当前版本（服务启动时迁移）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection
from pathlib import Path

from u8co_events.state import STATUS_SQL, TypeStatus


class ReadOnlyState:
    def __init__(self, path: str | Path) -> None:
        uri = Path(path).resolve().as_uri() + "?mode=ro"
        self._db = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
        self._db.execute("PRAGMA query_only = 1")

    def close(self) -> None:
        self._db.close()

    def outbox_size(self) -> int:
        return int(self._db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0])

    def status(self, pairs: Collection[tuple[str, str]] | None = None) -> list[TypeStatus]:
        rows = self._db.execute(STATUS_SQL).fetchall()
        result = [TypeStatus(r[0], r[1], r[2], int(r[3]), *r[4:11]) for r in rows]
        if pairs is None:
            return result
        wanted = set(pairs)
        return [st for st in result if (st.account, st.type) in wanted]
