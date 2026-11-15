"""基础档案数据源（archive:<档案>）：有时间戳的增量 + 编码扫描，没有时间戳的整表比对。假桥，不连网络。"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any

import pytest
from u8co_events.archives import ArchiveSource, change_kinds, snap_of, state_of
from u8co_events.bridge import BridgeLister, DocumentNotFound, Operator
from u8co_events.diff import Scope
from u8co_events.poller import Poller
from u8co_events.source import BridgeContractError, SourceContext, StopRequested
from u8co_events.state import Store

from fakes import make_config

CUS = "archive:customer"
PRJ = "archive:project"
FA = "archive:fa_card"


class FakeArcLister:
    """按桥的 archives/list 契约回答：changed_since 按 ufts 过滤，按编码翻页，watermark 只增不减。"""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, dict[str, Any]]] = {}
        self.requests: list[dict[str, Any]] = []
        self.loads: list[tuple[str, dict[str, Any]]] = []
        self.high = 0
        self.hidden = False
        self.fail_at: int | None = None
        self.load_errors: dict[str, Exception] = {}

    def put(self, archive: str, code: str, ufts: int | None = None, **fields: Any) -> None:
        row: dict[str, Any] = {"code": code, "name": f"名称{code}", **fields}
        if ufts is not None:
            row["ufts"] = str(ufts)
        self.rows.setdefault(archive, {})[code] = row

    def drop(self, archive: str, code: str) -> None:
        del self.rows[archive][code]

    def list_archives(self, acc: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(dict(fields))
        if self.fail_at is not None and len(self.requests) >= self.fail_at:
            raise ConnectionError("假桥：连接断开")
        rows = sorted(self.rows.get(fields["archive"], {}).values(), key=lambda row: row["code"])
        self.high = max([self.high, *(int(row["ufts"]) for row in rows if "ufts" in row)])
        if self.hidden:
            rows = []
        if fields.get("changed_since"):
            rows = [row for row in rows if int(row["ufts"]) > int(fields["changed_since"])]
        if "after" in fields:
            rows = [row for row in rows if row["code"] > fields["after"]]
        page = rows[: fields["limit"]]
        following = page[-1]["code"] if len(rows) > fields["limit"] else None
        if fields.get("keys_only"):
            page = [{"code": row["code"], "ufts": row.get("ufts")} for row in page]
        mark = str(self.high) if any("ufts" in row for row in self.rows.get(fields["archive"], {}).values()) else None
        return {"ok": True, "archive": fields["archive"], "items": page, "next": following, "watermark": mark}

    def load_sql(self, acc: str, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.loads.append((route, dict(fields)))
        code = fields["code"]
        if code in self.load_errors:
            raise self.load_errors[code]
        if code not in self.rows.get(fields["archive"], {}):
            raise DocumentNotFound(404, "not_found", "档案不存在")
        return {"ok": True, "archive": fields["archive"], "code": code, "fields": {}}


class Env:
    def __init__(self, tmp_path, **top: Any) -> None:
        account = {
            "acc": "999",
            "operator_file": "/x",
            "sources": ["archives"],
            "archives": ["customer", "project", "fa_card"],
        }
        self.cfg = make_config(str(tmp_path / "s.db"), accounts=[account], **top)
        self.store = Store(self.cfg.state_path)
        self.lister = FakeArcLister()
        self.stop = False
        ctx = SourceContext(
            self.cfg, self.cfg.accounts[0], self.store, self.lister, lambda: datetime.now(UTC), lambda: self.stop
        )
        for type_ in self.cfg.accounts[0].all_types():
            self.store.register("999", type_)
        self.source = ArchiveSource(ctx)
        self.tick = 0

    def scope(self, type_: str) -> Scope:
        self.tick += 1
        return Scope("999", type_, f"2026-10-02T01:00:{self.tick:02d}Z")

    def poll(self, type_: str = CUS) -> None:
        self.source.poll(self.scope(type_))

    def scan(self, type_: str = CUS) -> None:
        self.source.scan(self.scope(type_))

    def events(self) -> list[dict[str, Any]]:
        return [json.loads(row.payload) for row in self.store.pending_outbox(1000)]

    def kinds(self) -> list[tuple[str, str]]:
        return [(ev["code"], ev["kind"]) for ev in self.events()]

    def status(self, type_: str = CUS):
        return next(st for st in self.store.status() if st.type == type_)


@pytest.fixture
def env(tmp_path):
    e = Env(tmp_path)
    yield e
    e.store.close()


# ---- 纯函数 ----


def test_state_of_reads_end_date_and_other_status_fields() -> None:
    assert state_of({"code": "C1", "name": "甲", "class_code": "01", "end_date": "2026-09-30", "disabled": True}) == {
        "name": "甲", "class_code": "01", "disabled": True, "end_date": "2026-09-30",
    }
    plain = {"name": "甲", "class_code": None, "disabled": False, "end_date": None}
    assert state_of({"code": "C1", "name": "甲"}) == plain
    assert state_of({"code": "P1", "name": "项目", "closed": True})["disabled"] is True
    fa = state_of({"code": "F1", "name": "卡片", "disposed": True, "disposed_date": "2026-09-01"})
    assert fa["disabled"] is True and fa["end_date"] == "2026-09-01"


def test_snap_fingerprint_rowversion_and_plain() -> None:
    assert snap_of({"code": "C1", "name": "甲", "ufts": "123"}, True, None).fingerprint == "123"
    with pytest.raises(BridgeContractError, match="ufts"):
        snap_of({"code": "C1", "name": "甲", "ufts": "12a"}, True, None)
    with pytest.raises(BridgeContractError, match="code"):
        snap_of({"name": "甲", "ufts": "1"}, True, None)
    first = snap_of({"code": "P1", "name": "甲"}, False, None)
    assert len(first.fingerprint) == 16 and first.doc_id == 0 and first.code == "P1"
    # 内容没变指纹不变；改了再改回，指纹也不同（链式），event_id 不会与先前的重复。
    assert snap_of({"code": "P1", "name": "甲"}, False, first) == first
    second = snap_of({"code": "P1", "name": "乙"}, False, first)
    back = snap_of({"code": "P1", "name": "甲"}, False, second)
    assert len({first.fingerprint, second.fingerprint, back.fingerprint}) == 3


def test_change_kinds_status_suppresses_modified() -> None:
    base = snap_of({"code": "C1", "name": "甲", "ufts": "1"}, True, None)
    renamed = snap_of({"code": "C1", "name": "乙", "ufts": "2"}, True, None)
    ended = snap_of({"code": "C1", "name": "乙", "ufts": "3", "end_date": "2026-09-30", "disabled": True}, True, None)
    assert change_kinds(base, base) == []
    assert change_kinds(base, renamed) == ["modified"]
    assert change_kinds(renamed, ended) == ["disabled"]
    assert change_kinds(ended, renamed) == ["enabled"]
    # ufts 没变、只是桥多给了字段：不发事件。
    same = snap_of({"code": "C1", "name": "甲", "ufts": "1", "end_date": "2026-01-01"}, True, None)
    assert change_kinds(base, same) == []


# ---- 有时间戳 ----


def test_first_poll_records_snapshots_without_events(env) -> None:
    env.lister.put("customer", "C1", 10)
    env.lister.put("customer", "C2", 11)
    env.lister.put("customer", "C3", 12)
    env.poll()
    assert env.events() == []
    assert set(env.store.all_entity_snaps("999", CUS)) == {"C1", "C2", "C3"}
    assert env.store.watermark("999", CUS) == "12"
    # 两页（page_limit=2），第二页带 after。
    assert [req.get("after") for req in env.lister.requests] == [None, "C2"]
    assert env.lister.requests[0] == {"archive": "customer", "limit": 2}


def test_backfill_events_emits_created(tmp_path) -> None:
    e = Env(tmp_path, backfill_events=True)
    e.lister.put("customer", "C1", 10)
    e.poll()
    assert e.kinds() == [("C1", "created")]
    e.store.close()


def test_incremental_kinds_and_payload(env) -> None:
    env.lister.put("customer", "C1", 10, class_code="01")
    env.poll()
    env.lister.put("customer", "C1", 20, class_code="02")
    env.lister.put("customer", "C2", 21)
    env.poll()
    assert env.lister.requests[-1]["changed_since"] == "10"
    env.lister.put("customer", "C1", 30, class_code="02", end_date="2026-09-30", disabled=True)
    env.poll()
    env.lister.put("customer", "C1", 40, class_code="02", end_date=None, disabled=False)
    env.poll()
    assert env.kinds() == [("C1", "modified"), ("C2", "created"), ("C1", "disabled"), ("C1", "enabled")]
    disabled = env.events()[2]
    assert disabled["type"] == CUS and disabled["id"] == 0 and disabled["ufts"] == "30"
    assert disabled["prev"] == {"name": "名称C1", "class_code": "02", "disabled": False, "end_date": None}
    assert disabled["curr"] == {"name": "名称C1", "class_code": "02", "disabled": True, "end_date": "2026-09-30"}
    assert env.store.pending_outbox(10)[0].entity_key == "C1"
    assert env.store.watermark("999", CUS) == "40"


def test_failed_page_commits_nothing(env) -> None:
    for code, ufts in (("C1", 1), ("C2", 2), ("C3", 3)):
        env.lister.put("customer", code, ufts)
    env.lister.fail_at = 2
    with pytest.raises(ConnectionError):
        env.poll()
    assert env.store.watermark("999", CUS) is None
    assert env.store.all_entity_snaps("999", CUS) == {}


def test_bad_watermark_is_contract_error(env) -> None:
    env.lister.put("customer", "C1", 1)
    env.lister.list_archives = lambda acc, fields: {"ok": True, "items": [], "next": None, "watermark": None}
    with pytest.raises(BridgeContractError, match="watermark"):
        env.poll()


def test_next_must_advance(env) -> None:
    env.lister.list_archives = lambda acc, fields: {"ok": True, "items": [], "next": "C1", "watermark": "1"}
    with pytest.raises(BridgeContractError, match="next"):
        env.poll()


def test_key_scan_emits_deleted(env) -> None:
    for code, ufts in (("C1", 1), ("C2", 2), ("C3", 3)):
        env.lister.put("customer", code, ufts)
    env.poll()
    env.lister.drop("customer", "C2")
    env.scan()
    assert env.kinds() == [("C2", "deleted")]
    deleted = env.events()[0]
    assert deleted["curr"] is None and deleted["ufts"] == "2" and deleted["prev"]["name"] == "名称C2"
    assert env.lister.requests[-1].get("keys_only") is True
    assert set(env.store.all_entity_snaps("999", CUS)) == {"C1", "C3"}
    st = env.status()
    assert st.last_scan_at is not None and st.scan_error is None


def test_empty_scan_confirms_each_code(env) -> None:
    env.lister.put("customer", "C1", 1)
    env.lister.put("customer", "C2", 2)
    env.poll()
    env.lister.drop("customer", "C1")
    env.lister.drop("customer", "C2")
    env.scan()
    assert env.kinds() == [("C1", "deleted"), ("C2", "deleted")]
    assert env.lister.loads == [
        ("/v1/archives/get", {"archive": "customer", "code": "C1"}),
        ("/v1/archives/get", {"archive": "customer", "code": "C2"}),
    ]


def test_empty_scan_with_readable_archive_aborts(env) -> None:
    env.lister.put("customer", "C1", 1)
    env.lister.put("customer", "C2", 2)
    env.poll()
    env.lister.hidden = True
    with pytest.raises(BridgeContractError, match="仍能读取"):
        env.scan()
    assert env.events() == [] and len(env.lister.loads) == 1
    env.lister.load_errors["C1"] = DocumentNotFound(404, "not_found", "x")
    env.lister.load_errors["C2"] = PermissionError("403")
    with pytest.raises(BridgeContractError, match="出错"):
        env.scan()
    assert set(env.store.all_entity_snaps("999", CUS)) == {"C1", "C2"}


def test_empty_scan_limits_and_stop(tmp_path) -> None:
    e = Env(tmp_path, empty_scan_confirm_max=1)
    e.lister.put("customer", "C1", 1)
    e.lister.put("customer", "C2", 2)
    e.poll()
    e.lister.hidden = True
    with pytest.raises(BridgeContractError, match="上限"):
        e.scan()
    e.store.close()
    e = Env(tmp_path)
    e.lister.hidden = True
    e.lister.put("customer", "C1", 1)
    e.stop = True
    with pytest.raises(StopRequested):
        e.scan()
    assert e.lister.loads == []
    e.store.close()


def test_watermark_regression_resyncs(env) -> None:
    for code, ufts in (("C1", 10), ("C2", 20), ("C3", 30)):
        env.lister.put("customer", code, ufts)
    env.poll()
    # 库被还原：C3 没了，C1 变成更早的版本（名称不同），水位变小。
    env.lister.rows["customer"] = {}
    env.lister.put("customer", "C1", 5, name="旧名称")
    env.lister.put("customer", "C2", 20)
    env.lister.high = 6
    env.lister.rows["customer"]["C2"]["ufts"] = "6"
    env.poll()
    assert sorted(env.kinds()) == [("C1", "modified"), ("C2", "modified"), ("C3", "deleted")]
    st = env.status()
    assert st.watermark == "6" and st.watermark_reset == "30->6" and st.last_scan_at is not None


# ---- 没有时间戳 ----


def test_plain_poll_only_marks_ok(env) -> None:
    env.lister.put("project", "01:P1")
    env.poll(PRJ)
    assert env.lister.requests == []
    st = env.status(PRJ)
    assert st.last_ok_at is not None and st.watermark is None


def test_plain_scan_compares_content(env) -> None:
    env.lister.put("project", "01:P1", class_code="A")
    env.lister.put("project", "01:P2")
    env.scan(PRJ)
    assert env.events() == []
    assert env.lister.requests[0] == {"archive": "project", "limit": 2}
    env.lister.put("project", "01:P1", class_code="B")
    env.lister.put("project", "01:P2", closed=True)
    env.lister.put("project", "01:P3")
    env.scan(PRJ)
    env.lister.drop("project", "01:P3")
    env.lister.put("project", "01:P2", closed=False)
    env.scan(PRJ)
    env.lister.put("project", "01:P2", closed=True)
    env.scan(PRJ)
    assert env.kinds() == [
        ("01:P1", "modified"), ("01:P2", "disabled"), ("01:P3", "created"),
        ("01:P2", "enabled"), ("01:P3", "deleted"), ("01:P2", "disabled"),
    ]
    ids = [ev["event_id"] for ev in env.events()]
    assert len(set(ids)) == len(ids)
    assert env.status(PRJ).last_scan_at is not None


def test_plain_recreated_code_gets_new_event_ids(env) -> None:
    env.scan(PRJ)
    for _ in range(2):
        env.lister.put("project", "01:P9")
        env.scan(PRJ)
        env.lister.drop("project", "01:P9")
        env.scan(PRJ)
    assert env.kinds() == [("01:P9", "created"), ("01:P9", "deleted"), ("01:P9", "created"), ("01:P9", "deleted")]
    ids = [ev["event_id"] for ev in env.events()]
    assert len(set(ids)) == 4


def test_backfill_skips_archive_enabled_later(tmp_path) -> None:
    e = Env(tmp_path, backfill_events=True)
    e.lister.put("customer", "C1", 10)
    e.poll()
    # 状态库已不是新的，这时才登记的档案类型首轮不回填。
    e.store.register("999", "archive:vendor")
    e.lister.put("vendor", "V1", 11)
    e.poll("archive:vendor")
    assert e.kinds() == [("C1", "created")]
    e.store.close()


def test_fa_card_lists_disposed(env) -> None:
    env.lister.put("fa_card", "F1")
    env.scan(FA)
    env.lister.put("fa_card", "F1", disposed=True, disposed_date="2026-09-01")
    env.scan(FA)
    assert env.lister.requests[0]["include_disposed"] is True
    assert env.kinds() == [("F1", "disabled")]


def test_unknown_archive_type(env) -> None:
    with pytest.raises(ValueError):
        env.poll("archive:nope")


# ---- 接进轮询与桥 ----


def test_poller_builds_archive_source(tmp_path) -> None:
    account = {"acc": "999", "operator_file": "/x", "sources": ["archives"], "archives": ["customer"]}
    cfg = make_config(str(tmp_path / "s.db"), accounts=[account])
    store = Store(cfg.state_path)
    lister = FakeArcLister()
    lister.put("customer", "C1", 1)
    poller = Poller(cfg, store, lister, now=lambda: datetime(2026, 10, 2, tzinfo=UTC))
    poller.run_once(threading.Event())
    assert set(store.all_entity_snaps("999", CUS)) == {"C1"}
    assert any(req.get("keys_only") for req in lister.requests)
    store.close()


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, Any]] = []

    def arc_list(self, call, query):
        self.calls.append(("arc_list", call, query))
        return {"ok": True}

    def call(self, route, fields):
        self.calls.append(("call", route, fields))
        return {"ok": True}


def test_bridge_lister_archive_methods() -> None:
    client = RecordingClient()
    lister = BridgeLister(client, {"999": Operator("999", "2026", "op001", "pw")})
    lister.list_archives("999", {"archive": "customer", "changed_since": "5", "keys_only": True, "limit": 500})
    _, call, query = client.calls[0]
    assert (call.acc, call.operator) == ("999", "op001")
    assert (query.archive, query.changed_since, query.keys_only, query.limit) == ("customer", "5", True, 500)
    lister.load_sql("999", "/v1/archives/get", {"archive": "customer", "code": "C1"})
    _, route, body = client.calls[1]
    assert route == "/v1/archives/get"
    assert list(body) == ["acc", "year", "operator", "password", "date", "archive", "code"]
    with pytest.raises(ValueError):
        lister.load_sql("999", "/v1/archives/list", {"archive": "customer"})
