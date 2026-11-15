"""发布端测试用的假 Redis、记录型发布器和配置、事件构造。全部离线。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from redis import exceptions as redis_errors
from u8co_events import config
from u8co_events.diff import Scope, event_id
from u8co_events.state import OutboxRow

BASE_CONF: dict[str, Any] = {
    "bridge": {"base_url": "http://192.0.2.10:18089/u8co", "secret_file": "/nonexistent/secret"},
    "accounts": [{"acc": "999", "operator_file": "/nonexistent/op.json", "types": ["sale_order"]}],
    "health": {"listen": ""},
}


def make_config(state_path: str, **top: Any) -> config.Config:
    data = dict(BASE_CONF)
    data["state_path"] = state_path
    data.update(top)
    return config.from_dict(data, env={})


def make_event(doc_id: int, ufts: str, kind: str = "modified", acc: str = "999") -> dict[str, Any]:
    scope = Scope(acc, "sale_order", "2026-09-28T01:02:03Z")
    return {
        "event_id": event_id(scope, doc_id, kind, ufts),
        "account": acc,
        "type": "sale_order",
        "id": doc_id,
        "code": f"SO-{doc_id}",
        "kind": kind,
        "ufts": ufts,
        "detected_at": scope.detected_at,
        "prev": None,
        "curr": {"verified": False, "closed": False, "red": False, "verifier": None, "closer": None},
    }


def make_row(seq: int, doc_id: int = 1, acc: str = "999") -> OutboxRow:
    ev = make_event(doc_id, str(1000 + seq), acc=acc)
    return OutboxRow(seq, ev["event_id"], acc, "sale_order", "modified", doc_id, '{"n":%d}' % seq)


class FakePipe:
    """非事务 pipeline：命令先排队，execute 时按顺序执行，回复按顺序返回。"""

    def __init__(self, owner: FakeRedis) -> None:
        self._owner = owner
        self._ops: list[tuple[Any, ...]] = []

    def xadd(self, name: str, fields: dict[str, str], **kwargs: Any) -> None:
        assert not kwargs, "XADD 不应再带 MAXLEN"
        self._ops.append(("XADD", name, dict(fields)))

    def execute_command(self, *args: Any) -> None:
        self._ops.append(args)

    def execute(self, raise_on_error: bool = True) -> list[Any]:
        owner = self._owner
        if owner.fail_xadd:
            raise ConnectionError("connection reset")
        owner.pipelines.append([op[0] for op in self._ops])
        out: list[Any] = []
        for op in self._ops:
            if op[0] == "XADD":
                entries = owner.streams.setdefault(op[1], [])
                entries.append((f"{owner.next_ms()}-0", op[2]))
                out.append(entries[-1][0])
            else:
                owner.commands.append(op)
                out.append([owner.waitaof_local, 0])
        return out


class FakeRedis:
    def __init__(self, conf: dict[str, str] | None = None, version: str = "7.4.1") -> None:
        self.conf = {
            "appendonly": "yes",
            "appendfsync": "always",
            "maxmemory": "0",
            "maxmemory-policy": "noeviction",
            "no-appendfsync-on-rewrite": "no",
        }
        self.conf.update(conf or {})
        self.version = version
        self.streams: dict[str, list[tuple[str, dict[str, str]]]] = {}
        # 每个 stream 的消费组：name -> {"last": 已投递到的 ID, "pending": 未确认的最小 ID 或 None}
        self.groups: dict[str, dict[str, dict[str, Any]]] = {}
        self.pipelines: list[list[str]] = []
        self.commands: list[tuple[Any, ...]] = []
        self.fail_xadd = False
        self.fail_config = False
        self.down = False
        self.waitaof_local = 1
        self.closed = False
        self._ms = 0

    def next_ms(self) -> int:
        self._ms += 1
        return self._ms

    def config_get(self, name: str) -> dict[str, str]:
        if self.down:
            raise ConnectionError("Connection refused")
        if self.fail_config:
            raise redis_errors.ResponseError("unknown command 'CONFIG'")
        return {name: self.conf[name]} if name in self.conf else {}

    def info(self, section: str) -> dict[str, str]:
        return {"redis_version": self.version}

    def pipeline(self, transaction: bool = True) -> FakePipe:
        assert transaction is False
        return FakePipe(self)

    def exists(self, name: str) -> int:
        return int(name in self.streams)

    def xlen(self, name: str) -> int:
        return len(self.streams.get(name, []))

    def xinfo_groups(self, name: str) -> list[dict[str, Any]]:
        return [
            {"name": g, "pending": 0 if v["pending"] is None else 1, "last-delivered-id": v["last"]}
            for g, v in self.groups.get(name, {}).items()
        ]

    def xpending(self, name: str, group: str) -> dict[str, Any]:
        return {"pending": 1, "min": self.groups[name][group]["pending"]}

    def xtrim(self, name: str, maxlen: int | None = None, approximate: bool = True, minid: str | None = None) -> int:
        entries = self.streams.get(name, [])
        if minid is not None:
            floor = tuple(int(x) for x in minid.split("-"))
            keep = [e for e in entries if tuple(int(x) for x in e[0].split("-")) >= floor]
        else:
            keep = entries[-maxlen:] if maxlen else []
        self.streams[name] = keep
        return len(entries) - len(keep)

    def close(self) -> None:
        self.closed = True


class RecordingPublisher:
    """记下每次发布的 event_id；fail_next 为真时下一次发布抛异常，fail_always 为真时每次都抛。"""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.fail_next = False
        self.fail_always = False
        self.batches: list[int] = []

    def publish(self, rows: Sequence[OutboxRow]) -> None:
        self.batches.append(len(rows))
        if self.fail_always or self.fail_next:
            self.fail_next = False
            raise RuntimeError("redis down")
        self.sent.extend(row.event_id for row in rows)

    def warnings(self) -> list[str]:
        return []

    def close(self) -> None:
        return None
