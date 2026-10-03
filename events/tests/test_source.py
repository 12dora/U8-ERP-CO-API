"""附加数据源接进轮询循环：类型行、停止、积压、错误记录、删除扫描节奏。数据源用假的，不连桥。"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from u8co_events.bridge import not_found_from
from u8co_events.config import ConfigError
from u8co_events.diff import Scope
from u8co_events.entities import EntityCommit, EntitySnap
from u8co_events.poller import Poller
from u8co_events.source import SOURCE_MODULES, SourceContext, StopRequested, default_factory, make_event
from u8co_events.state import Store

from fakes import FakeLister, doc, make_config


class FakeSource:
    """每次 poll 提交一个快照；按 fail / stop_on 模拟出错和收到停止信号。"""

    def __init__(self, ctx: SourceContext) -> None:
        self.ctx = ctx
        self.polls: list[str] = []
        self.scans: list[str] = []
        self.fail: dict[str, Exception] = {}
        self.stop_on: str | None = None

    def poll(self, scope: Scope) -> None:
        self.polls.append(scope.type)
        if scope.type in self.fail:
            raise self.fail[scope.type]
        if scope.type == self.stop_on:
            raise StopRequested("逐条确认时收到停止信号")
        snap = EntitySnap(key=f"k{len(self.polls)}", fingerprint=str(len(self.polls)), state={"n": len(self.polls)})
        event = make_event(scope, "processed", None, snap)
        self.ctx.store.commit_entities(scope, EntityCommit(upserts=[snap], events=[event], watermark="7"))

    def scan(self, scope: Scope) -> None:
        self.scans.append(scope.type)
        self.ctx.store.commit_entities(scope, EntityCommit(polled=False, scanned=True))


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 10, 2, 1, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def env(tmp_path):
    path = str(tmp_path / "s.db")
    store = Store(path)
    yield path, store, FakeLister(), Clock()
    store.close()


def _accounts(*sources: str) -> list[dict]:
    return [{"acc": "999", "operator_file": "/x", "types": ["sale_order"], "sources": ["vouchers", *sources]}]


def _poller(env, sources: tuple[str, ...], **top) -> tuple[Poller, dict[str, FakeSource]]:
    path, store, lister, clock = env
    built: dict[str, FakeSource] = {}

    def factory(name: str):
        def build(ctx: SourceContext) -> FakeSource:
            built[name] = FakeSource(ctx)
            return built[name]

        return build

    cfg = make_config(path, accounts=_accounts(*sources), **top)
    factories = {name: factory(name) for name in SOURCE_MODULES}
    return Poller(cfg, store, lister, now=clock, factories=factories), built


def test_sources_run_after_voucher_types(env) -> None:
    _, store, lister, _ = env
    lister.put("sale_order", doc(1, 1))
    poller, built = _poller(env, ("arap_process", "gl"), delete_scan_minutes=0)
    assert set(built) == {"arap_process", "gl"}
    assert built["gl"].ctx.account.acc == "999"
    poller.run_once()
    assert built["arap_process"].polls == ["ar_process", "ap_process"]
    assert built["gl"].polls == ["gl_voucher"]
    assert [call[1].type for call in lister.calls] == ["sale_order"]
    types = [st.type for st in store.status()]
    assert types == ["ap_process", "ar_process", "gl_voucher", "sale_order"]
    assert store.watermark("999", "gl_voucher") == "7"
    assert [row.type for row in store.pending_outbox(10)] == ["ar_process", "ap_process", "gl_voucher"]


def test_source_types_registered_before_first_poll(env) -> None:
    _, store, _, _ = env
    _poller(env, ("archives",), delete_scan_minutes=0)
    archive_rows = [st.type for st in store.status() if st.type.startswith("archive:")]
    assert "archive:customer" in archive_rows
    assert "archive:project" not in archive_rows


def test_source_errors_are_recorded_per_type(env) -> None:
    _, store, _, _ = env
    poller, built = _poller(env, ("arap_process",))
    built["arap_process"].fail["ar_process"] = ConnectionError("假桥：连接断开")
    poller.run_once()
    status = {st.type: st for st in store.status()}
    assert "ConnectionError" in (status["ar_process"].last_error or "")
    # 一个类型失败不挡后面的类型；失败的类型不做删除扫描。
    assert status["ap_process"].last_error is None
    assert built["arap_process"].scans == ["ap_process"]
    assert status["ap_process"].last_scan_at is not None


def test_scan_follows_delete_scan_minutes(env) -> None:
    _, _, _, clock = env
    poller, built = _poller(env, ("gl",), delete_scan_minutes=30)
    poller.run_once()
    poller.run_once()
    assert built["gl"].scans == ["gl_voucher"]
    clock.now += timedelta(minutes=31)
    poller.run_once()
    assert built["gl"].scans == ["gl_voucher", "gl_voucher"]


def test_scan_errors_go_to_scan_error(env, monkeypatch) -> None:
    _, store, _, _ = env
    poller, built = _poller(env, ("gl",))

    def broken(scope: Scope) -> None:
        raise RuntimeError("扫描坏了")

    monkeypatch.setattr(built["gl"], "scan", broken)
    poller.run_once()
    st = store.status({("999", "gl_voucher")})[0]
    assert (st.last_error, "扫描坏了" in (st.scan_error or "")) == (None, True)


def test_stop_requested_is_not_an_error(env) -> None:
    _, store, _, _ = env
    poller, built = _poller(env, ("gl",), delete_scan_minutes=0)
    built["gl"].stop_on = "gl_voucher"
    poller.run_once()
    assert store.status({("999", "gl_voucher")})[0].last_error is None


def test_stop_signal_skips_sources(env) -> None:
    poller, built = _poller(env, ("gl",), delete_scan_minutes=0)
    stop = threading.Event()
    stop.set()
    poller.run_once(stop)
    assert built["gl"].polls == []
    assert built["gl"].ctx.stopping()


def test_backlog_pauses_sources(env) -> None:
    _, store, _, _ = env
    poller, built = _poller(env, ("gl",), delete_scan_minutes=0, outbox_high_water=1000)
    for i in range(1000):
        store.commit_entities(Scope("999", "x", "t"), EntityCommit(events=[_event(i)]))
    poller.run_once()
    assert built["gl"].polls == []


def _event(i: int):
    return make_event(Scope("999", "x", "t"), "processed", None, EntitySnap(key=str(i), fingerprint=str(i)))


def test_default_factory_reports_missing_module(monkeypatch) -> None:
    monkeypatch.setitem(SOURCE_MODULES, "gl", "u8co_events.no_such_source_module")
    with pytest.raises(ConfigError, match="尚未实现"):
        default_factory("gl")


@pytest.mark.parametrize("path", ["/u8co/v1/archives/get", "/u8co/v1/gl/vouchers/load"])
def test_proof_paths_for_sources(path: str) -> None:
    raw = '{"ok": false, "code": "not_found", "message": "档案不存在"}'.encode()
    assert not_found_from(path, 404, raw) is not None
    assert not_found_from("/u8co/v1/archives/list", 404, raw) is None
