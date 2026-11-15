"""总账凭证数据源（glsrc）：摘要对比、事件种类、链式指纹、空扫描确认、扫描范围变化、ident 倒退。不连桥。"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any

import pytest
from u8co_events import glsrc
from u8co_events.bridge import BridgeLister, DocumentNotFound, Operator
from u8co_events.diff import Scope
from u8co_events.glsrc import GlSource, Mark, change_kinds, parse_mark
from u8co_events.poller import Poller
from u8co_events.source import BridgeContractError, SourceContext, StopRequested
from u8co_events.state import Store

from fakes import make_config

SIGNS = {"记": 1, "收": 2, "付": 3, "转": 4}
NOW = datetime(2026, 10, 2, 1, 0, 0, tzinfo=UTC)


def voucher(period: int, sign: str, no: int, **state: Any) -> dict[str, Any]:
    row = {
        "period": period,
        "sign": sign,
        "no": no,
        "fingerprint": f"fp{period}{sign}{no}",
        "date": f"2026-{period:02d}-15",
        "maker": "张三",
        "checker": "",
        "cashier": "",
        "bookkeeper": "",
        "posted": False,
        "void": False,
        "debit_total": 100.5,
        "lines": 2,
    }
    row.update(state)
    return row


class FakeGl:
    """按 gl/vouchers/digest 的契约回答：periods 省略时取 open 加最近 closed_periods 个 closed，按键翻页。"""

    def __init__(self) -> None:
        self.year = 2026
        self.open = [9, 10, 11, 12]
        self.closed = [1, 2, 3, 4, 5, 6, 7, 8]
        self.ident = 100
        self.rows: dict[tuple[int, str, int], dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.loads: list[dict[str, Any]] = []
        self.existing: set[tuple[int, str, int]] = set()
        self.load_error: Exception | None = None
        self.stuck = False

    def put(self, row: dict[str, Any]) -> None:
        self.rows[(row["period"], row["sign"], row["no"])] = row
        self.ident += 1

    def change(self, period: int, sign: str, no: int, **state: Any) -> None:
        self.rows[(period, sign, no)].update(state)

    def drop(self, period: int, sign: str, no: int) -> None:
        del self.rows[(period, sign, no)]

    def list_gl_digest(self, acc: str, fields: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(dict(fields))
        periods = fields.get("periods")
        if periods is None:
            periods = sorted(self.open + self.closed[len(self.closed) - fields["closed_periods"] :])
        order = sorted((k for k in self.rows if k[0] in periods), key=lambda k: (k[0], SIGNS[k[1]], k[2]))
        if "after" in fields:
            after = tuple(int(x) for x in fields["after"].split("."))
            order = [k for k in order if (k[0], SIGNS[k[1]], k[2]) > after]
        limit = fields["limit"]
        page = order[:limit]
        following = None
        if len(order) > limit:
            last = page[-1]
            following = "0.0.0" if self.stuck else f"{last[0]}.{SIGNS[last[1]]}.{last[2]}"
        return {
            "ok": True,
            "fiscal_year": self.year,
            "periods": list(periods),
            "items": [dict(self.rows[k]) for k in page],
            "next": following,
            "watermark": "0",
            "ident": str(self.ident),
        }

    def load_sql(self, acc: str, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        assert route == "/v1/gl/vouchers/load"
        self.loads.append(dict(fields))
        if self.load_error is not None:
            raise self.load_error
        if (fields["period"], fields["sign"], fields["no"]) in self.existing:
            return {"ok": True}
        raise DocumentNotFound(404, "not_found", "凭证不存在")

    def list_page(self, acc: str, request: Any) -> dict[str, Any]:
        raise AssertionError("总账凭证不走 vouchers/list")

    def load(self, acc: str, type_: str, doc_id: int) -> dict[str, Any]:
        raise AssertionError("总账凭证不走 vouchers/load")


ACCOUNTS = [{"acc": "999", "operator_file": "/x", "sources": ["gl"]}]


@pytest.fixture
def env(tmp_path):
    store = Store(str(tmp_path / "s.db"))
    yield store, FakeGl()
    store.close()


def _source(env, stop: threading.Event | None = None, **top: Any) -> GlSource:
    store, bridge = env
    cfg = make_config("/unused/state.db", accounts=ACCOUNTS, **top)
    flag = stop or threading.Event()
    ctx = SourceContext(cfg, cfg.accounts[0], store, bridge, lambda: NOW, flag.is_set)
    store.register("999", "gl_voucher")
    return GlSource(ctx)


def _scope() -> Scope:
    return Scope("999", "gl_voucher", "2026-10-02T01:00:00Z")


def _events(store: Store) -> list[dict[str, Any]]:
    rows = store.pending_outbox(1000)
    store.mark_sent([row.seq for row in rows])
    return [json.loads(row.payload) for row in rows]


def _kinds(store: Store) -> list[tuple[str, str]]:
    return [(ev["code"], ev["kind"]) for ev in _events(store)]


def _status(store: Store):
    return store.status({("999", "gl_voucher")})[0]


def test_first_run_is_silent_and_records_scope(env) -> None:
    store, bridge = env
    bridge.put(voucher(9, "记", 1))
    bridge.put(voucher(8, "转", 3))
    bridge.put(voucher(7, "记", 9))
    _source(env).poll(_scope())
    assert _events(store) == []
    snaps = store.all_entity_snaps("999", "gl_voucher")
    assert sorted(snaps) == ["2026|8|转|3", "2026|9|记|1"]
    assert snaps["2026|9|记|1"].code == "记-1"
    st = _status(store)
    assert st.watermark == "103|2026|8,9,10,11,12"
    assert st.last_scan_at is not None
    assert bridge.calls[0] == {"closed_periods": 1, "limit": 2}


def test_backfill_events_emits_created(env) -> None:
    store, bridge = env
    bridge.put(voucher(9, "记", 1))
    _source(env, backfill_events=True).poll(_scope())
    assert _kinds(store) == [("记-1", "created")]


def test_status_kinds_suppress_modified(env) -> None:
    store, bridge = env
    src = _source(env)
    bridge.put(voucher(9, "记", 1))
    src.poll(_scope())
    bridge.put(voucher(9, "记", 2))
    src.poll(_scope())
    assert _kinds(store) == [("记-2", "created")]
    bridge.change(9, "记", 1, checker="李四", cashier="王五", fingerprint="x1")
    src.poll(_scope())
    events = _events(store)
    assert [ev["kind"] for ev in events] == ["audited", "signed"]
    assert events[0]["prev"]["checker"] is None and events[0]["curr"]["checker"] == "李四"
    assert events[0]["id"] == 0 and events[0]["curr"]["period"] == 9
    bridge.change(9, "记", 1, bookkeeper="赵六", posted=True, fingerprint="x2")
    src.poll(_scope())
    assert _kinds(store) == [("记-1", "posted")]
    bridge.change(9, "记", 2, void=True, fingerprint="x3")
    src.poll(_scope())
    assert _kinds(store) == [("记-2", "voided")]
    bridge.change(9, "记", 2, debit_total=200, fingerprint="x4")
    src.poll(_scope())
    assert _kinds(store) == [("记-2", "modified")]
    src.poll(_scope())
    assert _events(store) == []


def test_date_only_change_is_modified(env) -> None:
    store, bridge = env
    src = _source(env)
    bridge.put(voucher(9, "记", 1))
    src.poll(_scope())
    bridge.change(9, "记", 1, date="2026-09-20")
    src.poll(_scope())
    assert _kinds(store) == [("记-1", "modified")]


def test_reaudit_gets_new_event_id(env) -> None:
    store, bridge = env
    src = _source(env)
    bridge.put(voucher(9, "记", 1))
    src.poll(_scope())
    ids = []
    for checker in ("李四", "", "李四"):
        bridge.change(9, "记", 1, checker=checker)
        src.poll(_scope())
        ids.extend(ev["event_id"] for ev in _events(store))
    assert len(ids) == 3 and len(set(ids)) == 3


def test_unaudit_and_unsign(env) -> None:
    assert change_kinds({"checker": "李四", "cashier": "王五"}, {"checker": None, "cashier": None}) == [
        "unaudited",
        "unsigned",
    ]
    # 取消记账、取消作废没有专门的种类，发 modified。
    assert change_kinds({"posted": True}, {"posted": False}) == ["modified"]
    assert change_kinds({"void": True}, {"void": False}) == ["modified"]


def test_deleted_when_missing_from_full_digest(env) -> None:
    store, bridge = env
    src = _source(env)
    bridge.put(voucher(9, "记", 1))
    bridge.put(voucher(9, "记", 2))
    src.poll(_scope())
    bridge.drop(9, "记", 2)
    src.poll(_scope())
    events = _events(store)
    assert [(ev["code"], ev["kind"], ev["curr"]) for ev in events] == [("记-2", "deleted", None)]
    assert events[0]["prev"]["no"] == 2
    assert sorted(store.all_entity_snaps("999", "gl_voucher")) == ["2026|9|记|1"]
    assert bridge.loads == []


def test_empty_digest_confirms_each_voucher(env) -> None:
    store, bridge = env
    src = _source(env)
    bridge.put(voucher(9, "记", 1))
    bridge.put(voucher(10, "收", 2))
    src.poll(_scope())
    bridge.rows.clear()
    src.poll(_scope())
    assert bridge.loads == [{"period": 10, "sign": "收", "no": 2}, {"period": 9, "sign": "记", "no": 1}]
    assert sorted(kind for _, kind in _kinds(store)) == ["deleted", "deleted"]
    assert store.all_entity_snaps("999", "gl_voucher") == {}


def test_empty_digest_aborts_when_a_voucher_still_exists(env) -> None:
    store, bridge = env
    src = _source(env)
    bridge.put(voucher(9, "记", 1))
    src.poll(_scope())
    before = _status(store).watermark
    bridge.rows.clear()
    bridge.existing.add((9, "记", 1))
    with pytest.raises(BridgeContractError, match="仍能读取"):
        src.poll(_scope())
    bridge.existing.clear()
    bridge.load_error = ConnectionError("连不上")
    with pytest.raises(BridgeContractError, match="ConnectionError"):
        src.poll(_scope())
    assert _events(store) == []
    assert _status(store).watermark == before
    assert len(store.all_entity_snaps("999", "gl_voucher")) == 1


def test_empty_digest_confirm_limits(env) -> None:
    store, bridge = env
    bridge.put(voucher(9, "记", 1))
    bridge.put(voucher(9, "记", 2))
    _source(env).poll(_scope())
    bridge.rows.clear()
    with pytest.raises(BridgeContractError, match="未发删除事件"):
        _source(env, empty_scan_confirm_max=0).poll(_scope())
    with pytest.raises(BridgeContractError, match="超过逐张确认上限"):
        _source(env, empty_scan_confirm_max=1).poll(_scope())
    stop = threading.Event()
    stop.set()
    with pytest.raises(StopRequested):
        _source(env, stop=stop).poll(_scope())
    assert bridge.loads == []


def test_paging_keeps_scope_and_rejects_stuck_cursor(env) -> None:
    store, bridge = env
    for no in range(1, 6):
        bridge.put(voucher(9, "记", no))
    src = _source(env)
    src.poll(_scope())
    assert len(store.all_entity_snaps("999", "gl_voucher")) == 5
    assert len(bridge.calls) == 3
    assert bridge.calls[1] == {"fiscal_year": 2026, "periods": [8, 9, 10, 11, 12], "after": "9.1.2", "limit": 2}
    bridge.stuck = True
    with pytest.raises(BridgeContractError, match="没有前进"):
        src.poll(_scope())


def test_period_leaving_scope_is_dropped_and_entering_is_silent(env) -> None:
    store, bridge = env
    src = _source(env)
    bridge.put(voucher(8, "记", 1))
    bridge.put(voucher(9, "记", 1))
    src.poll(_scope())
    # 9 月结账：8 月滑出扫描范围，快照静默删掉。
    bridge.open.remove(9)
    bridge.closed.append(9)
    src.poll(_scope())
    assert _events(store) == []
    assert sorted(store.all_entity_snaps("999", "gl_voucher")) == ["2026|9|记|1"]
    assert _status(store).watermark == "102|2026|9,10,11,12"
    # 反结账 9、8 月：8 月重新进入范围，7 月成为最近已结账月份也进入范围，都只记快照。
    bridge.put(voucher(7, "转", 5))
    bridge.open[:0] = [8, 9]
    bridge.closed = [1, 2, 3, 4, 5, 6, 7]
    src.poll(_scope())
    assert _events(store) == []
    assert sorted(store.all_entity_snaps("999", "gl_voucher")) == ["2026|7|转|5", "2026|8|记|1", "2026|9|记|1"]
    # 之后这些期间里的变化照常发事件。
    bridge.put(voucher(8, "记", 2))
    src.poll(_scope())
    assert _kinds(store) == [("记-2", "created")]


def test_new_fiscal_year_is_silent(env) -> None:
    store, bridge = env
    src = _source(env)
    bridge.put(voucher(12, "记", 1))
    src.poll(_scope())
    bridge.year = 2027
    bridge.open, bridge.closed = list(range(1, 13)), []
    bridge.rows.clear()
    bridge.put(voucher(1, "记", 1))
    src.poll(_scope())
    assert _events(store) == []
    assert sorted(store.all_entity_snaps("999", "gl_voucher")) == ["2027|1|记|1"]
    assert bridge.loads == []


def test_ident_regression_records_reset(env) -> None:
    store, bridge = env
    src = _source(env)
    bridge.put(voucher(9, "记", 1))
    src.poll(_scope())
    bridge.ident = 50
    bridge.change(9, "记", 1, checker="李四")
    src.poll(_scope())
    assert _kinds(store) == [("记-1", "audited")]
    st = _status(store)
    assert st.watermark_reset == "101->50"
    assert st.watermark == "50|2026|8,9,10,11,12|1"


def test_recreated_voucher_after_restore_gets_new_event_id(env) -> None:
    store, bridge = env
    src = _source(env)
    src.poll(_scope())
    bridge.put(voucher(9, "记", 1))
    src.poll(_scope())
    first = _events(store)
    assert [ev["kind"] for ev in first] == ["created"]
    # 库被还原到这张凭证之前：自增计数回退，凭证不见了；之后重做出同号、同内容的凭证。
    bridge.drop(9, "记", 1)
    bridge.ident = 100
    src.poll(_scope())
    assert _kinds(store) == [("记-1", "deleted")]
    bridge.put(voucher(9, "记", 1))
    src.poll(_scope())
    again = _events(store)
    assert [ev["kind"] for ev in again] == ["created"]
    assert again[0]["event_id"] != first[0]["event_id"]
    assert _status(store).watermark == "101|2026|8,9,10,11,12|1"


def test_backfill_skips_gl_enabled_later(env) -> None:
    store, bridge = env
    store.register("999", "sale_order")
    store.commit_cycle(Scope("999", "sale_order", "2026-10-02T01:00:00Z"), "1", [], [])
    bridge.put(voucher(9, "记", 1))
    _source(env, backfill_events=True).poll(_scope())
    assert _events(store) == []


def test_watermark_drop_alone_is_not_a_reset(env) -> None:
    store, bridge = env
    src = _source(env)
    bridge.put(voucher(9, "记", 1))
    bridge.put(voucher(9, "记", 2))
    src.poll(_scope())
    bridge.drop(9, "记", 2)
    src.poll(_scope())
    assert _status(store).watermark_reset is None


@pytest.mark.parametrize(
    ("patch", "match"),
    [
        ({"fingerprint": ""}, "缺少 sign 或 fingerprint"),
        ({"period": 13}, "period"),
        ({"debit_total": None}, "debit_total"),
    ],
)
def test_bad_rows_are_contract_errors(env, patch: dict[str, Any], match: str) -> None:
    store, bridge = env
    row = voucher(9, "记", 1)
    bridge.rows[(9, "记", 1)] = row
    row.update(patch)
    with pytest.raises(BridgeContractError, match=match):
        _source(env).poll(_scope())
    assert store.watermark("999", "gl_voucher") is None


def test_bad_response_shapes(env) -> None:
    store, bridge = env
    bridge.put(voucher(9, "记", 1))
    for bad in ({"ok": False}, {"ok": True, "fiscal_year": 2026, "periods": [9], "items": [], "ident": "x"}):
        bridge.list_gl_digest = lambda acc, fields, bad=bad: bad
        with pytest.raises(BridgeContractError):
            _source(env).poll(_scope())


def test_parse_mark_round_trip() -> None:
    mark = Mark(123, 2026, (8, 9, 10))
    assert parse_mark(mark.text()) == mark
    assert parse_mark("456|2026|") == Mark(456, 2026, ())
    assert parse_mark(None) is None
    assert parse_mark("789") is None
    assert parse_mark("a|b|c") is None
    restored = Mark(50, 2026, (9,), 2)
    assert restored.text() == "50|2026|9|2"
    assert parse_mark(restored.text()) == restored


def test_scan_only_marks_scanned(env) -> None:
    store, _ = env
    _source(env).scan(_scope())
    st = _status(store)
    assert (st.last_scan_at, st.last_poll_at, st.watermark) == ("2026-10-02T01:00:00Z", None, None)


def test_runs_inside_poller(tmp_path) -> None:
    store = Store(str(tmp_path / "p.db"))
    try:
        bridge = FakeGl()
        bridge.put(voucher(9, "记", 1))
        cfg = make_config(str(tmp_path / "p.db"), accounts=ACCOUNTS, delete_scan_minutes=0)
        poller = Poller(cfg, store, bridge, now=lambda: NOW)
        poller.run_once()
        bridge.change(9, "记", 1, checker="李四")
        poller.run_once()
        assert _kinds(store) == [("记-1", "audited")]
        assert _status(store).last_error is None
    finally:
        store.close()


class _Client:
    def __init__(self) -> None:
        self.seen: list[Any] = []

    def gl_digest(self, call: Any, query: Any) -> dict[str, Any]:
        self.seen.append((call, query))
        return {"ok": True}


def test_bridge_lister_sends_gl_digest_query() -> None:
    client = _Client()
    lister = BridgeLister(client, {"999": Operator("999", "2026", "op001", "pw")})  # type: ignore[arg-type]
    assert lister.list_gl_digest("999", {"fiscal_year": 2026, "periods": [9], "after": "9.1.2", "limit": 2}) == {
        "ok": True
    }
    call, query = client.seen[0]
    assert (call.acc, call.year, call.operator) == ("999", "2026", "op001")
    assert (query.fiscal_year, query.periods, query.after, query.limit) == (2026, [9], "9.1.2", 2)
    with pytest.raises(TypeError):
        lister.list_gl_digest("999", {"year": 2026})


def test_module_constants() -> None:
    assert glsrc.TYPE == "gl_voucher"
    assert glsrc.LOAD_ROUTE == "/v1/gl/vouchers/load"
