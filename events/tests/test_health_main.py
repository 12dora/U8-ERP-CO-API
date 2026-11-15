"""健康检查、状态报告和命令行入口。离线：不连桥、不连 Redis。"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta

from u8co_events import health, main, publisher
from u8co_events.diff import Scope
from u8co_events.readonly import ReadOnlyState
from u8co_events.state import Store, TypeStatus

from fakes_pub import BASE_CONF, FakeRedis, make_config, make_event

T0 = datetime(2026, 9, 28, 1, 0, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def _store_with_one_type(path) -> Store:
    store = Store(path)
    store.register("999", "sale_order")
    scope = Scope("999", "sale_order", "2026-09-28T01:00:00Z")
    store.commit_cycle(scope, "6000", [], [make_event(1, "5000")])
    return store


def test_report_lag_and_outbox(tmp_path):
    cfg = make_config(str(tmp_path / "s.sqlite3"))
    store = _store_with_one_type(cfg.state_path)
    clock = Clock()
    monitor = health.Monitor(cfg, now=clock)
    clock.now = T0 + timedelta(seconds=90)
    report = monitor.report(store)
    store.close()
    assert report["ok"] is True
    assert report["outbox_size"] == 1
    [row] = report["types"]
    assert (row["account"], row["type"], row["watermark"]) == ("999", "sale_order", "6000")
    assert row["lag_seconds"] == 90


def test_report_flags_stale_type_and_dead_thread(tmp_path):
    cfg = make_config(str(tmp_path / "s.sqlite3"))
    store = _store_with_one_type(cfg.state_path)
    clock = Clock()
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    monitor = health.Monitor(
        cfg, threads={"poller": dead}, checks=[lambda: ["发布线程 1000 秒没有成功一轮"]], now=clock
    )
    clock.now = T0 + timedelta(hours=1)
    report = monitor.report(store)
    store.close()
    assert report["ok"] is False
    text = " ".join(report["problems"])
    assert "poller" in text and "发布线程" in text and "999/sale_order" in text
    assert "删除扫描" in text


def test_http_healthz_and_probe(tmp_path):
    cfg = make_config(str(tmp_path / "s.sqlite3"))
    _store_with_one_type(cfg.state_path).close()
    server = health.start_server("127.0.0.1:0", health.Monitor(cfg), cfg.state_path)
    try:
        port = server.server_address[1]
        assert health.probe(f"127.0.0.1:{port}") == 0
    finally:
        health.stop_server(server)
    assert health.probe(f"127.0.0.1:{port}") == 1


def _write_private(path, text: str) -> str:
    path.write_text(text, encoding="utf-8")
    os.chmod(path, 0o600)
    return str(path)


def _config_file(tmp_path, **top) -> str:
    data = json.loads(json.dumps(BASE_CONF))
    data["state_path"] = str(tmp_path / "state" / "s.sqlite3")
    data["bridge"]["secret_file"] = _write_private(tmp_path / "secret", "ab" * 32)
    op = {"acc": "999", "year": "2026", "operator": "op001", "password": "x"}
    data["accounts"][0]["operator_file"] = _write_private(tmp_path / "op.json", json.dumps(op))
    data.update(top)
    return _write_private(tmp_path / "config.json", json.dumps(data))


def test_cli_check_with_stdout_publisher(tmp_path, capsys):
    path = _config_file(tmp_path, publisher={"kind": "stdout"})
    assert main.main(["--config", path, "check"]) == main.EXIT_OK
    assert "配置检查通过" in capsys.readouterr().out


def test_cli_status_prints_json(tmp_path, capsys):
    path = _config_file(tmp_path, publisher={"kind": "stdout"})
    _store_with_one_type(tmp_path / "state" / "s.sqlite3").close()
    assert main.main(["--config", path, "status"]) == main.EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["outbox_size"] == 1
    assert report["types"][0]["type"] == "sale_order"


def test_cli_config_error_exit_code(tmp_path, capsys):
    bad = _write_private(tmp_path / "bad.json", json.dumps({"unknown_key": 1}))
    assert main.main(["--config", bad, "status"]) == main.EXIT_CONFIG
    assert "配置错误" in capsys.readouterr().err


def test_cli_check_refuses_loose_secret(tmp_path, capsys):
    path = _config_file(tmp_path, publisher={"kind": "stdout"})
    os.chmod(tmp_path / "secret", 0o644)
    assert main.main(["--config", path, "check"]) == main.EXIT_CONFIG


def test_scan_error_and_removed_types(tmp_path):
    cfg = make_config(str(tmp_path / "s.sqlite3"))
    store = _store_with_one_type(cfg.state_path)
    store.record_error(Scope("999", "sale_order", "2026-09-28T01:00:00Z"), "桥超时", scan=True)
    # 配置里已经去掉的类型留下的旧行：不显示、不影响健康。
    store.register("999", "dispatch")
    store.close()
    with closing(ReadOnlyState(cfg.state_path)) as state:
        report = health.Monitor(cfg, now=Clock()).report(state)
    assert [row["type"] for row in report["types"]] == ["sale_order"]
    assert report["ok"] is False
    assert "桥超时" in report["problems"][0]


class _FakeState:
    def __init__(self, rows: list[TypeStatus]) -> None:
        self._rows = rows

    def status(self, pairs=None):
        return [st for st in self._rows if pairs is None or (st.account, st.type) in pairs]

    def outbox_size(self) -> int:
        return 0


def test_watermark_reset_alerts_for_a_day_and_notes_are_warnings(tmp_path):
    cfg = make_config(str(tmp_path / "s.sqlite3"))
    at = "2026-09-28T01:00:00Z"
    row = TypeStatus("999", "sale_order", "10", 1, at, at, at, None, None, at, "900->10")
    clock = Clock()
    monitor = health.Monitor(cfg, notes=[lambda: ["stream 超长"]], now=clock)
    report = monitor.report(_FakeState([row]))
    assert report["ok"] is False
    assert "900->10" in report["problems"][0]
    assert report["warnings"] == ["stream 超长"]
    clock.now = T0 + timedelta(hours=25)
    fresh = TypeStatus(
        "999", "sale_order", "10", 1, at, clock.now.isoformat(), clock.now.isoformat(), None, None, at, "900->10"
    )
    assert health.Monitor(cfg, now=clock).report(_FakeState([fresh]))["ok"] is True


def test_readonly_state_never_creates_db(tmp_path):
    missing = tmp_path / "nope.sqlite3"
    try:
        ReadOnlyState(missing)
    except sqlite3.Error:
        pass
    assert not missing.exists()


def test_healthz_on_missing_db_is_503_and_creates_nothing(tmp_path):
    cfg = make_config(str(tmp_path / "missing.sqlite3"))
    server = health.start_server("127.0.0.1:0", health.Monitor(cfg), cfg.state_path)
    try:
        assert health.probe(f"127.0.0.1:{server.server_address[1]}") == 1
    finally:
        health.stop_server(server)
    assert not (tmp_path / "missing.sqlite3").exists()


def _redis_config_file(tmp_path, **top) -> str:
    return _config_file(tmp_path, redis={"url": "redis://redis:6379/0"}, **top)


def test_cli_redis_down_has_own_exit_code(tmp_path, monkeypatch, capsys):
    fake = FakeRedis()
    fake.down = True
    monkeypatch.setattr(publisher, "redis_client", lambda conf: fake)
    assert main.main(["--config", _redis_config_file(tmp_path), "check"]) == main.EXIT_REDIS_DOWN
    assert "Redis 不可用" in capsys.readouterr().err


def test_cli_refuses_reseed_when_streams_exist(tmp_path, monkeypatch, capsys):
    fake = FakeRedis()
    fake.streams["u8co:events:999"] = [("1-0", {})]
    monkeypatch.setattr(publisher, "redis_client", lambda conf: fake)
    assert main.main(["--config", _redis_config_file(tmp_path), "check"]) == main.EXIT_RESEED
    assert "--init" in capsys.readouterr().err


def test_reseed_allowed_with_init_or_when_state_exists(tmp_path):
    cfg = make_config(str(tmp_path / "s.sqlite3"), redis={"url": "redis://redis:6379/0"})
    fake = FakeRedis()
    fake.streams["u8co:events:999"] = [("1-0", {})]
    pub = publisher.build_publisher(cfg, client=fake)
    main.guard_reseed(cfg, pub, init=True)
    _store_with_one_type(cfg.state_path).close()
    main.guard_reseed(cfg, pub, init=False)


def test_fresh_state_and_no_streams_starts(tmp_path, monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(publisher, "redis_client", lambda conf: fake)
    assert main.main(["--config", _redis_config_file(tmp_path), "check"]) == main.EXIT_OK
