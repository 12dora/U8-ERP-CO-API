from __future__ import annotations

from datetime import UTC, datetime

import pytest
from u8co_events.alerts import reseed_problem, type_problems
from u8co_events.diff import Scope
from u8co_events.state import Store, TypeStatus

from fakes import make_config

NOW = datetime(2026, 9, 28, 12, 0, 0, tzinfo=UTC)


def _st(**fields) -> TypeStatus:
    base = dict(
        account="999",
        type="sale_order",
        watermark="10",
        snapshots=1,
        last_poll_at=None,
        last_ok_at="2026-09-28T11:59:00Z",
        last_scan_at="2026-09-28T11:50:00Z",
        last_error=None,
    )
    base.update(fields)
    return TypeStatus(**base)


def test_healthy_type_has_no_problems(tmp_path) -> None:
    assert type_problems(_st(), NOW, make_config(str(tmp_path / "s.db"))) == []


def test_scan_error_and_stale_scan(tmp_path) -> None:
    cfg = make_config(str(tmp_path / "s.db"), delete_scan_minutes=30)
    problems = type_problems(_st(scan_error="boom", last_scan_at="2026-09-28T10:00:00Z"), NOW, cfg)
    assert len(problems) == 2
    assert "boom" in problems[0]
    assert "删除扫描超过 90 分钟" in problems[1]
    off = make_config(str(tmp_path / "s.db"), delete_scan_minutes=0)
    assert type_problems(_st(last_scan_at="2026-01-01T00:00:00Z"), NOW, off) == []


def test_watermark_reset_alert_expires(tmp_path) -> None:
    cfg = make_config(str(tmp_path / "s.db"))
    recent = _st(watermark_reset_at="2026-09-28T06:00:00Z", watermark_reset="11->5")
    assert "11->5" in type_problems(recent, NOW, cfg)[0]
    old = _st(watermark_reset_at="2026-09-26T06:00:00Z", watermark_reset="11->5")
    assert type_problems(old, NOW, cfg) == []


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.db")
    yield s
    s.close()


def test_reseed_refused_when_streams_exist(tmp_path, store: Store) -> None:
    cfg = make_config(str(tmp_path / "s.db"))
    asked: list[str] = []

    def exists(name: str) -> bool:
        asked.append(name)
        return True

    problem = reseed_problem(cfg, store, exists)
    assert problem is not None and "u8co:events:999" in problem and "allow_reseed" in problem
    assert asked == ["u8co:events:999"]
    assert reseed_problem(cfg, store, lambda _: False) is None
    allowed = make_config(str(tmp_path / "s.db"), allow_reseed=True)
    assert reseed_problem(allowed, store, exists) is None


def test_reseed_not_checked_for_existing_state(tmp_path, store: Store) -> None:
    store.commit_cycle(Scope("999", "sale_order", "2026-09-28T01:00:00Z"), "1", [], [])
    cfg = make_config(str(tmp_path / "s.db"))
    assert reseed_problem(cfg, store, lambda _: True) is None
