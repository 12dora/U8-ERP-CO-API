from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest
from co.client.u8co_errors import U8CoError, U8CoNotFound
from u8co_events.poller import Poller
from u8co_events.state import Store

from fakes import FakeLister, doc, make_config


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 28, 1, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)


@pytest.fixture
def env(tmp_path):
    path = str(tmp_path / "s.db")
    store = Store(path)
    lister = FakeLister()
    clock = Clock()
    yield path, store, lister, clock
    store.close()


def _kinds(store: Store) -> list[tuple[int, str]]:
    return [(row.doc_id, row.kind) for row in store.pending_outbox(1000)]


def _poller(path, store, lister, clock, **top) -> Poller:
    return Poller(make_config(path, **top), store, lister, now=clock)


def test_backfill_seeds_without_events(env) -> None:
    path, store, lister, clock = env
    for i in range(1, 6):
        lister.put("sale_order", doc(i, i))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=0)
    poller.run_once()
    assert store.outbox_size() == 0
    assert store.all_snapshot_ids("999", "sale_order") == {1, 2, 3, 4, 5}
    assert store.watermark("999", "sale_order") == "5"
    # page_limit=2：5 张单据读了 3 页。
    assert len(lister.calls) == 3


def test_backfill_events_true_emits_created(env) -> None:
    path, store, lister, clock = env
    lister.put("sale_order", doc(1, 1))
    _poller(path, store, lister, clock, delete_scan_minutes=0, backfill_events=True).run_once()
    assert _kinds(store) == [(1, "created")]


def test_incremental_kinds(env) -> None:
    path, store, lister, clock = env
    lister.put("sale_order", doc(1, 1))
    lister.put("sale_order", doc(2, 2))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=0)
    poller.run_once()
    lister.put("sale_order", doc(1, 10, verified=True, verifier="张三"))
    lister.put("sale_order", doc(2, 11, code="SO-X"))
    lister.put("sale_order", doc(3, 12))
    lister.calls.clear()
    poller.run_once()
    assert _kinds(store) == [(1, "verified"), (2, "modified"), (3, "created")]
    assert lister.calls[0][1].changed_since == "2"
    event = json.loads(store.pending_outbox(1)[0].payload)
    assert event["prev"]["verified"] is False
    assert event["curr"]["verifier"] == "张三"
    assert event["detected_at"] == "2026-09-28T01:00:00Z"
    # 再轮询一次没有新变化。
    poller.run_once()
    assert store.outbox_size() == 3


def test_failure_mid_pages_writes_nothing(env) -> None:
    path, store, lister, clock = env
    for i in range(1, 4):
        lister.put("sale_order", doc(i, i))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=0)
    poller.run_once()
    for i in range(1, 4):
        lister.put("sale_order", doc(i, 10 + i, closed=True))
    lister.calls.clear()
    lister.fail_at = 2
    poller.run_once()
    assert store.outbox_size() == 0
    assert store.watermark("999", "sale_order") == "3"
    assert "ConnectionError" in (store.status()[0].last_error or "")
    lister.fail_at = None
    poller.run_once()
    assert _kinds(store) == [(1, "closed"), (2, "closed"), (3, "closed")]
    assert store.status()[0].last_error is None


def test_delete_scan(env) -> None:
    path, store, lister, clock = env
    for i in range(1, 6):
        lister.put("sale_order", doc(i, i))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=30)
    poller.run_once()
    lister.drop("sale_order", 4)
    clock.advance(10)
    poller.run_once()
    assert store.outbox_size() == 0  # 还没到扫描间隔
    clock.advance(25)
    poller.run_once()
    assert _kinds(store) == [(4, "deleted")]
    assert store.all_snapshot_ids("999", "sale_order") == {1, 2, 3, 5}
    keys_calls = [req for _, req in lister.calls if req.keys_only]
    assert keys_calls and all(not req.changed_since for req in keys_calls)


def test_partial_scan_emits_no_deletes(env) -> None:
    path, store, lister, clock = env
    for i in range(1, 6):
        lister.put("sale_order", doc(i, i))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=30)
    poller.run_once()
    lister.drop("sale_order", 5)
    clock.advance(31)
    # 增量 1 页，扫描第 1 页成功、第 2 页失败。
    lister.calls.clear()
    lister.fail_at = 3
    poller.run_once()
    assert store.outbox_size() == 0
    assert 5 in store.all_snapshot_ids("999", "sale_order")


def _empty_scan_setup(env, count: int = 2, **top):
    path, store, lister, clock = env
    for i in range(1, count + 1):
        lister.put("sale_order", doc(i, i))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=30, **top)
    poller.run_once()
    clock.advance(31)
    return store, lister, poller


def test_empty_scan_never_deletes_everything(env) -> None:
    # 列表一行都不返回，但单据逐张读得到：列表或权限出了问题，不删。
    store, lister, poller = _empty_scan_setup(env)
    lister.list_hidden = True
    poller.run_once()
    assert store.outbox_size() == 0
    assert store.all_snapshot_ids("999", "sale_order") == {1, 2}
    error = store.status()[0].scan_error or ""
    assert "没有返回任何单据" in error
    assert "2 张中至少 1 张（id=1）仍能读取" in error


def test_empty_scan_confirmed_by_load_deletes(env) -> None:
    store, lister, poller = _empty_scan_setup(env)
    lister.docs["sale_order"].clear()
    poller.run_once()
    assert sorted(_kinds(store)) == [(1, "deleted"), (2, "deleted")]
    assert store.all_snapshot_ids("999", "sale_order") == set()
    assert [doc_id for _, _, doc_id in lister.loads] == [1, 2]
    assert store.status()[0].scan_error is None


def test_empty_scan_one_loadable_keeps_all(env) -> None:
    store, lister, poller = _empty_scan_setup(env, count=3)
    lister.list_hidden = True
    lister.drop("sale_order", 1)
    lister.drop("sale_order", 3)
    poller.run_once()
    assert store.outbox_size() == 0
    assert store.all_snapshot_ids("999", "sale_order") == {1, 2, 3}
    assert "3 张中至少 1 张（id=2）仍能读取" in (store.status()[0].scan_error or "")
    # 读到第一张还在的就停，不再为 3 号登录 U8。
    assert [doc_id for _, _, doc_id in lister.loads] == [1, 2]


def test_empty_scan_status_only_404_is_not_proof(env) -> None:
    # 没带 code 的 404（客户端按状态码补成 not_found）、桥的「未知路径」都不算单据不存在。
    store, lister, poller = _empty_scan_setup(env)
    lister.docs["sale_order"].clear()
    lister.load_errors[1] = U8CoNotFound(404, "not_found", "")
    poller.run_once()
    assert store.outbox_size() == 0
    assert store.all_snapshot_ids("999", "sale_order") == {1, 2}
    assert "id=1 出错" in (store.status()[0].scan_error or "")
    assert [doc_id for _, _, doc_id in lister.loads] == [1]


def test_empty_scan_honours_stop(env) -> None:
    store, lister, poller = _empty_scan_setup(env, count=3)
    lister.docs["sale_order"].clear()
    stop = threading.Event()
    real_load = lister.load

    def load_then_stop(acc, type_, doc_id):
        stop.set()
        return real_load(acc, type_, doc_id)

    lister.load = load_then_stop
    poller.run_once(stop)
    assert store.outbox_size() == 0
    assert store.all_snapshot_ids("999", "sale_order") == {1, 2, 3}
    assert [doc_id for _, _, doc_id in lister.loads] == [1]
    # 停止不是错误：不记扫描错误。
    assert store.status()[0].scan_error is None


def test_empty_scan_other_load_error_keeps_all(env) -> None:
    store, lister, poller = _empty_scan_setup(env)
    lister.docs["sale_order"].clear()
    lister.load_errors[1] = U8CoError(403, "no_permission", "没有权限")
    poller.run_once()
    assert store.outbox_size() == 0
    assert store.all_snapshot_ids("999", "sale_order") == {1, 2}
    error = store.status()[0].scan_error or ""
    assert "no_permission" in error
    assert [doc_id for _, _, doc_id in lister.loads] == [1]


def test_empty_scan_over_confirm_max_keeps_all(env) -> None:
    store, lister, poller = _empty_scan_setup(env, count=3, empty_scan_confirm_max=2)
    lister.docs["sale_order"].clear()
    poller.run_once()
    assert store.outbox_size() == 0
    assert store.all_snapshot_ids("999", "sale_order") == {1, 2, 3}
    assert "empty_scan_confirm_max=2" in (store.status()[0].scan_error or "")
    assert lister.loads == []


def test_empty_scan_confirm_disabled_keeps_old_behaviour(env) -> None:
    store, lister, poller = _empty_scan_setup(env, empty_scan_confirm_max=0)
    lister.docs["sale_order"].clear()
    poller.run_once()
    assert store.outbox_size() == 0
    assert store.all_snapshot_ids("999", "sale_order") == {1, 2}
    assert "没有返回任何单据" in (store.status()[0].scan_error or "")
    assert lister.loads == []


def test_backpressure_pauses_polling(env) -> None:
    path, store, lister, clock = env
    for i in range(1, 1002):
        lister.put("sale_order", doc(i, i))
    poller = _poller(
        path, store, lister, clock, delete_scan_minutes=0, backfill_events=True, outbox_high_water=1000, page_limit=500
    )
    poller.run_once()
    assert store.outbox_size() == 1001
    lister.put("sale_order", doc(2000, 5000))
    lister.calls.clear()
    poller.run_once()
    assert lister.calls == []
    assert store.watermark("999", "sale_order") == "1001"


def test_restart_resumes_from_committed_state(env) -> None:
    path, store, lister, clock = env
    lister.put("sale_order", doc(1, 1))
    _poller(path, store, lister, clock, delete_scan_minutes=0).run_once()
    lister.put("sale_order", doc(1, 5, verified=True))
    store.close()
    reopened = Store(path)
    try:
        _poller(path, reopened, lister, clock, delete_scan_minutes=0).run_once()
        assert [(r.doc_id, r.kind) for r in reopened.pending_outbox(10)] == [(1, "verified")]
    finally:
        reopened.close()


def test_bad_bridge_response_is_an_error(env) -> None:
    path, store, lister, clock = env

    class Broken(FakeLister):
        def list_page(self, acc, request):
            return {"ok": True, "items": [], "next": None, "watermark": "abc"}

    _poller(path, store, Broken(), clock, delete_scan_minutes=0).run_once()
    assert store.watermark("999", "sale_order") is None
    assert "watermark" in (store.status()[0].last_error or "")


def test_run_forever_stops(env) -> None:
    path, store, lister, clock = env
    stop = threading.Event()
    stop.set()
    _poller(path, store, lister, clock).run_forever(stop)
    assert lister.calls == []


def test_watermark_regression_triggers_full_rediff(env) -> None:
    path, store, lister, clock = env
    for i in range(1, 4):
        lister.put("sale_order", doc(i, i))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=0)
    poller.run_once()
    assert store.watermark("999", "sale_order") == "3"
    # 库被还原到更早的状态：3 号单据没了，2 号的状态不同，还有一张快照里没有的 4 号，水位变小。
    lister.drop("sale_order", 3)
    lister.put("sale_order", doc(2, 2, verified=True))
    lister.put("sale_order", doc(4, 1))
    lister.high = 2
    lister.calls.clear()
    poller.run_once()
    assert sorted(_kinds(store)) == [(2, "verified"), (3, "deleted"), (4, "created")]
    # 第一次是按旧水位的增量，之后是不带 changed_since 的整轮完整行。
    assert lister.calls[0][1].changed_since == "3"
    assert all(not req.changed_since and not req.keys_only for _, req in lister.calls[1:])
    st = store.status()[0]
    assert st.watermark == "2"
    assert st.watermark_reset == "3->2"
    assert st.watermark_reset_at == "2026-09-28T01:00:00Z"
    assert store.all_snapshot_ids("999", "sale_order") == {1, 2, 4}
    # 之后按新水位正常增量，不再重对比。
    lister.calls.clear()
    poller.run_once()
    assert [req.changed_since for _, req in lister.calls] == ["2"]


def _regressed_empty(env, **top):
    # 快照里有 1 号，库被还原到更早（水位 5 → 1），整轮读取为空。
    path, store, lister, clock = env
    lister.put("sale_order", doc(1, 5))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=0, **top)
    poller.run_once()
    lister.drop("sale_order", 1)
    lister.high = 1
    return store, lister, poller


def test_watermark_regression_empty_read_confirmed_deletes(env) -> None:
    # 快照还原后这类单据都没了：逐张确认都不存在，提交删除和新水位，不会一直 UNHEALTHY。
    store, lister, poller = _regressed_empty(env)
    poller.run_once()
    assert _kinds(store) == [(1, "deleted")]
    assert store.watermark("999", "sale_order") == "1"
    st = store.status()[0]
    assert st.last_error is None
    assert st.watermark_reset == "5->1"
    assert [doc_id for _, _, doc_id in lister.loads] == [1]
    assert store.all_snapshot_ids("999", "sale_order") == set()


def test_watermark_regression_empty_read_unconfirmed_changes_nothing(env) -> None:
    store, lister, poller = _regressed_empty(env)
    lister.load_errors[1] = U8CoError(503, "com_unavailable", "U8 不可用")
    poller.run_once()
    assert store.outbox_size() == 0
    assert store.watermark("999", "sale_order") == "5"
    error = store.status()[0].last_error or ""
    assert "水位倒退后的整轮读取没有返回任何单据" in error
    assert "com_unavailable" in error


def test_watermark_regression_empty_read_loadable_changes_nothing(env) -> None:
    # 列表读空但单据还读得到（列表或权限问题）：不删、水位不动。
    store, lister, poller = _regressed_empty(env)
    lister.put("sale_order", doc(1, 1))
    lister.list_hidden = True
    poller.run_once()
    assert store.outbox_size() == 0
    assert store.watermark("999", "sale_order") == "5"
    assert "至少 1 张（id=1）仍能读取" in (store.status()[0].last_error or "")


def test_watermark_regression_confirm_is_throttled(env) -> None:
    # 列表或权限问题持续时，每轮轮询照旧报错，但逐张确认（登录 U8、占点数）按 delete_scan_minutes 才再做一次。
    path, store, lister, clock = env
    lister.put("sale_order", doc(1, 5))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=30)
    poller.run_once()
    lister.put("sale_order", doc(1, 1))
    lister.high = 1
    lister.list_hidden = True
    clock.advance(1)
    poller.run_once()
    assert [doc_id for _, _, doc_id in lister.loads] == [1]
    for _ in range(3):
        clock.advance(5)
        poller.run_once()
    assert len(lister.loads) == 1
    error = store.status()[0].last_error or ""
    assert "至少 1 张（id=1）仍能读取" in error
    assert "30 分钟内不再确认" in error
    assert store.watermark("999", "sale_order") == "5"
    # 到了间隔再确认一次；这回单据真的没了，提交删除和新水位。
    lister.drop("sale_order", 1)
    clock.advance(16)
    poller.run_once()
    assert [doc_id for _, _, doc_id in lister.loads] == [1, 1]
    assert (1, "deleted") in _kinds(store)
    assert store.watermark("999", "sale_order") == "1"


def test_watermark_regression_empty_read_respects_confirm_max(env) -> None:
    store, lister, poller = _regressed_empty(env, empty_scan_confirm_max=0)
    poller.run_once()
    assert store.outbox_size() == 0
    assert store.watermark("999", "sale_order") == "5"
    assert lister.loads == []
    assert "水位倒退" in (store.status()[0].last_error or "")


def test_scan_error_survives_successful_polls(env) -> None:
    path, store, lister, clock = env
    lister.put("sale_order", doc(1, 1))
    poller = _poller(path, store, lister, clock, delete_scan_minutes=30)
    poller.run_once()
    clock.advance(31)
    lister.calls.clear()
    lister.fail_at = 2  # 增量成功，扫描失败
    poller.run_once()
    st = store.status()[0]
    assert st.last_error is None
    assert "ConnectionError" in (st.scan_error or "")
    lister.fail_at = None
    clock.advance(1)
    poller.run_once()
    assert store.status()[0].scan_error is None
