"""发布器、Redis 持久化检查和按消费组裁剪。假 Redis，离线。"""

from __future__ import annotations

import io
import json

import pytest
from u8co_events.publisher import (
    DurabilityError,
    PublishError,
    RedisStreamsPublisher,
    RedisUnavailable,
    StdoutPublisher,
    build_publisher,
    check_durability,
    stream_fields,
)
from u8co_events.trim import Trimmer

from fakes_pub import FakeRedis, make_config, make_row


def _redis_cfg(tmp_path, **redis):
    conf = {"url": "redis://redis:6379/0", "maxlen": 5000}
    conf.update(redis)
    return make_config(str(tmp_path / "s.sqlite3"), redis=conf)


def test_xadd_per_account_stream_and_waitaof_in_same_pipeline(tmp_path):
    fake = FakeRedis()
    pub = build_publisher(_redis_cfg(tmp_path), client=fake)
    rows = [make_row(1, 10), make_row(2, 11), make_row(3, 12, acc="998")]
    pub.publish(rows)
    assert [f["event_id"] for _, f in fake.streams["u8co:events:999"]] == [rows[0].event_id, rows[1].event_id]
    assert fake.streams["u8co:events:998"][0][1]["id"] == "12"
    # 落盘证明和写入在同一个 pipeline（同一条连接）里，而且排在所有 XADD 之后。
    assert fake.pipelines == [["XADD", "XADD", "XADD", "WAITAOF"]]
    assert fake.commands == [("WAITAOF", 1, 0, 10000)]


def test_stream_fields_carry_payload_verbatim():
    row = make_row(7, 70)
    fields = stream_fields(row)
    assert fields == {
        "event_id": row.event_id,
        "account": "999",
        "type": "sale_order",
        "kind": "modified",
        "id": "70",
        "payload": row.payload,
    }


def test_publish_failure_raises_publish_error(tmp_path):
    fake = FakeRedis()
    pub = build_publisher(_redis_cfg(tmp_path), client=fake)
    fake.fail_xadd = True
    with pytest.raises(PublishError):
        pub.publish([make_row(1)])


def test_waitaof_not_confirmed_is_failure(tmp_path):
    fake = FakeRedis()
    pub = build_publisher(_redis_cfg(tmp_path), client=fake)
    fake.waitaof_local = 0
    with pytest.raises(PublishError):
        pub.publish([make_row(1)])


def test_old_redis_with_always_skips_waitaof(tmp_path):
    fake = FakeRedis(version="7.0.15")
    pub = build_publisher(_redis_cfg(tmp_path), client=fake)
    pub.publish([make_row(1)])
    assert fake.pipelines == [["XADD"]]


@pytest.mark.parametrize(
    "conf,version",
    [
        ({"appendonly": "no"}, "7.4.1"),
        ({"appendfsync": "no"}, "7.4.1"),
        ({"appendfsync": "everysec"}, "7.0.15"),
        ({"no-appendfsync-on-rewrite": "yes"}, "7.0.15"),
        ({"maxmemory": "1000000", "maxmemory-policy": "allkeys-lru"}, "7.4.1"),
    ],
)
def test_nondurable_redis_refused(conf, version):
    with pytest.raises(DurabilityError):
        check_durability(FakeRedis(conf, version), allow_nondurable=False)


def test_everysec_accepted_when_waitaof_available():
    d = check_durability(FakeRedis({"appendfsync": "everysec"}), allow_nondurable=False)
    assert d.waitaof


def test_config_command_disabled_refused_unless_allowed():
    fake = FakeRedis()
    fake.fail_config = True
    with pytest.raises(DurabilityError):
        check_durability(fake, allow_nondurable=False)
    assert check_durability(fake, allow_nondurable=True).appendonly is False


def test_redis_down_is_unavailable_not_config_error():
    fake = FakeRedis()
    fake.down = True
    with pytest.raises(RedisUnavailable):
        check_durability(fake, allow_nondurable=True)


def test_allow_nondurable_redis_starts(tmp_path):
    fake = FakeRedis({"appendonly": "no"})
    pub = build_publisher(_redis_cfg(tmp_path, allow_nondurable_redis=True), client=fake)
    assert isinstance(pub, RedisStreamsPublisher)
    pub.publish([make_row(1)])
    # appendonly 关着时不能发 WAITAOF（Redis 会报错）。
    assert fake.commands == []


def _fill(fake: FakeRedis, stream: str, count: int) -> list[str]:
    for _ in range(count):
        fake.streams.setdefault(stream, []).append((f"{fake.next_ms()}-0", {}))
    return [entry_id for entry_id, _ in fake.streams[stream]]


def test_trim_keeps_everything_unread_by_slowest_group():
    fake = FakeRedis()
    ids = _fill(fake, "s", 10)
    fake.groups["s"] = {
        "fast": {"last": ids[9], "pending": None},
        "slow": {"last": ids[6], "pending": ids[3]},  # 第 4 条已投递未确认
    }
    trimmer = Trimmer(fake, maxlen=5)
    assert trimmer.trim("s") == 3
    assert [e for e, _ in fake.streams["s"]] == ids[3:]
    assert "未读事件不裁剪" in trimmer.warnings()[0]


def test_trim_below_maxlen_does_nothing():
    fake = FakeRedis()
    _fill(fake, "s", 3)
    fake.groups["s"] = {"g": {"last": "0-0", "pending": None}}
    assert Trimmer(fake, maxlen=5).trim("s") == 0
    assert len(fake.streams["s"]) == 3


def test_trim_new_group_protects_whole_stream():
    fake = FakeRedis()
    _fill(fake, "s", 8)
    fake.groups["s"] = {"g": {"last": "0-0", "pending": None}}
    trimmer = Trimmer(fake, maxlen=5)
    assert trimmer.trim("s") == 0
    assert trimmer.warnings()


def test_trim_without_groups_falls_back_to_maxlen():
    fake = FakeRedis()
    _fill(fake, "s", 8)
    assert Trimmer(fake, maxlen=5).trim("s") == 3


def test_trim_is_rate_limited_per_stream():
    fake = FakeRedis()
    _fill(fake, "s", 8)
    now = [0.0]
    trimmer = Trimmer(fake, maxlen=5, clock=lambda: now[0])
    trimmer.maybe_trim(["s"])
    _fill(fake, "s", 5)
    trimmer.maybe_trim(["s"])
    assert len(fake.streams["s"]) == 10
    now[0] = 61.0
    trimmer.maybe_trim(["s"])
    assert len(fake.streams["s"]) == 5


def test_stdout_publisher_writes_payload_lines():
    out = io.StringIO()
    rows = [make_row(1), make_row(2)]
    StdoutPublisher(out).publish(rows)
    lines = out.getvalue().splitlines()
    assert [json.loads(line)["n"] for line in lines] == [1, 2]


def test_stdout_kind_needs_no_redis(tmp_path):
    cfg = make_config(str(tmp_path / "s.sqlite3"), publisher={"kind": "stdout"})
    assert isinstance(build_publisher(cfg), StdoutPublisher)
