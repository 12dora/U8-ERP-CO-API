from __future__ import annotations

import json
import os

import pytest
from co.client.u8co_kinds import KIND_NAMES
from co.client.u8co_gl_arc import READ_ARCHIVES
from u8co_events.config import (
    ARCHIVE_PREFIX,
    GL_TYPES,
    NOTE_TYPES,
    PLAIN_ARCHIVES,
    PROCESS_TYPES,
    RV_ARCHIVES,
    SOURCE_NAMES,
    ConfigError,
    from_dict,
    load,
    read_secret,
)

from fakes import make_config

_BASE = {
    "bridge": {"base_url": "http://192.0.2.10:8765/u8co", "secret_file": "/run/secrets/u8co"},
    "accounts": [{"acc": "999", "operator_file": "/run/secrets/op.json"}],
}


def _with(**changes: object) -> dict:
    data = json.loads(json.dumps(_BASE))
    data.update(changes)
    return data


def test_defaults() -> None:
    cfg = from_dict(_with(), env={})
    assert cfg.accounts[0].types == KIND_NAMES
    assert cfg.poll_interval_seconds == 30
    assert cfg.page_limit == 500
    assert cfg.delete_scan_minutes == 30
    assert cfg.empty_scan_confirm_max == 50
    assert cfg.backfill_events is False
    assert cfg.redis.stream_prefix == "u8co:events:"
    assert cfg.redis.allow_nondurable_redis is False
    assert cfg.publisher.kind == "redis"
    assert cfg.health.listen == "127.0.0.1:8090"


def test_unknown_keys_rejected_at_every_level() -> None:
    with pytest.raises(ConfigError, match="poll_intervall"):
        from_dict(_with(poll_intervall=5), env={})
    with pytest.raises(ConfigError, match="secret"):
        from_dict(_with(bridge={"base_url": "http://192.0.2.10:1/u8co", "secret": "x"}), env={})
    with pytest.raises(ConfigError, match="type"):
        from_dict(_with(accounts=[{"acc": "999", "operator_file": "/x", "type": []}]), env={})
    with pytest.raises(ConfigError, match="maxlength"):
        from_dict(_with(redis={"maxlength": 5}), env={})


@pytest.mark.parametrize(
    "changes",
    [
        {"accounts": []},
        {"accounts": [{"acc": "99", "operator_file": "/x"}]},
        {"accounts": [{"acc": "999", "operator_file": "/x"}, {"acc": "999", "operator_file": "/y"}]},
        {"accounts": [{"acc": "999", "operator_file": "/x", "types": ["nope"]}]},
        {"accounts": [{"acc": "999", "operator_file": "/x", "types": ["sale_order", "sale_order"]}]},
        {"page_limit": 501},
        {"page_limit": True},
        {"empty_scan_confirm_max": 501},
        {"empty_scan_confirm_max": -1},
        {"empty_scan_confirm_max": 1.5},
        {"poll_interval_seconds": 1},
        {"backfill_events": "no"},
        {"bridge": {"base_url": "http://192.0.2.10/u8co", "secret_file": "/x"}},
        {"publisher": {"kind": "kafka"}},
        {"redis": {"url": "http://192.0.2.10"}},
        {"health": {"listen": "nope"}},
    ],
)
def test_bad_values(changes: dict) -> None:
    with pytest.raises(ConfigError):
        from_dict(_with(**changes), env={})


def test_env_overrides_paths_only() -> None:
    env = {
        "U8CO_EVENTS_STATE": "/data/state.db",
        "U8CO_EVENTS_SECRET_FILE": "/run/other",
        "U8CO_EVENTS_REDIS_PASSWORD_FILE": "/run/redis",
    }
    cfg = from_dict(_with(), env=env)
    assert cfg.state_path == "/data/state.db"
    assert cfg.bridge.secret_file == "/run/other"
    assert cfg.redis.password_file == "/run/redis"


def test_health_can_be_disabled() -> None:
    assert from_dict(_with(health={"listen": ""}), env={}).health.listen == ""


def test_load_reads_file(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("U8CO_EVENTS_STATE", raising=False)
    monkeypatch.delenv("U8CO_EVENTS_SECRET_FILE", raising=False)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(_with(page_limit=7)), encoding="utf-8")
    assert load(path).page_limit == 7
    path.write_text("{", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON"):
        load(path)


def test_secret_file_permissions(tmp_path) -> None:
    path = tmp_path / "secret"
    path.write_text("ab\n", encoding="utf-8")
    os.chmod(path, 0o644)
    with pytest.raises(ConfigError, match="权限过宽"):
        read_secret(str(path))
    os.chmod(path, 0o600)
    assert read_secret(str(path)) == "ab"


def test_make_config_helper(tmp_path) -> None:
    assert make_config(str(tmp_path / "s.db")).page_limit == 2


@pytest.mark.parametrize(
    "url",
    ["redis://:secret@redis:6379/0", "redis://user:secret@redis:6379/0", "unix:///run/redis.sock?password=x"],
)
def test_inline_redis_password_rejected(url: str) -> None:
    with pytest.raises(ConfigError, match="password_file"):
        from_dict(_with(redis={"url": url}), env={})


def test_redis_user_without_password_allowed() -> None:
    assert from_dict(_with(redis={"url": "redis://events@redis:6379/0"}), env={}).redis.url.startswith("redis://")


def test_allow_reseed_and_pairs() -> None:
    cfg = from_dict(_with(accounts=[{"acc": "999", "operator_file": "/x", "types": ["sale_order"]}]), env={})
    assert cfg.allow_reseed is False
    assert cfg.pairs() == frozenset({("999", "sale_order")})
    assert from_dict(_with(allow_reseed=True), env={}).allow_reseed is True


# ---- 附加数据源（第 4 版状态库起） ----


def _account(**fields: object) -> dict:
    return _with(accounts=[{"acc": "999", "operator_file": "/x", **fields}])


def test_source_defaults_keep_voucher_behaviour() -> None:
    cfg = from_dict(_with(), env={})
    account = cfg.accounts[0]
    assert account.sources == ("vouchers",)
    assert account.archives == RV_ARCHIVES
    assert account.list_types() == KIND_NAMES
    assert account.all_types() == KIND_NAMES
    assert cfg.auto_id_lag == 500
    assert cfg.gl_closed_periods == 1
    assert cfg.pairs() == frozenset(("999", kind) for kind in KIND_NAMES)


def test_sources_add_type_rows() -> None:
    sources = ["vouchers", "arap_process", "gl", "notes", "archives"]
    cfg = from_dict(_account(types=["sale_order"], sources=sources, archives=["customer", "project"]), env={})
    account = cfg.accounts[0]
    assert account.list_types() == ("sale_order", *NOTE_TYPES)
    assert account.source_types("arap_process") == PROCESS_TYPES
    assert account.source_types("gl") == GL_TYPES
    assert account.source_types("archives") == ("archive:customer", "archive:project")
    assert account.source_types("vouchers") == ()
    expected = ("sale_order", "ar_note", "ap_note", "ar_process", "ap_process", "gl_voucher")
    expected += ("archive:customer", "archive:project")
    assert cfg.pairs() == frozenset(("999", t) for t in expected)


def test_sources_without_vouchers() -> None:
    account = from_dict(_account(sources=["gl"]), env={}).accounts[0]
    assert account.types == ()
    assert account.all_types() == GL_TYPES


@pytest.mark.parametrize(
    "fields",
    [
        {"sources": []},
        {"sources": "gl"},
        {"sources": ["vouchers", "nope"]},
        {"sources": ["gl", "gl"]},
        {"sources": ["gl"], "types": ["sale_order"]},
        {"archives": ["customer"]},
        {"sources": ["archives"], "archives": []},
        {"sources": ["archives"], "archives": ["nope"]},
        {"sources": ["archives"], "archives": ["customer", "customer"]},
    ],
)
def test_bad_sources(fields: dict) -> None:
    with pytest.raises(ConfigError):
        from_dict(_account(**fields), env={})


@pytest.mark.parametrize(
    "changes",
    [{"auto_id_lag": 49}, {"auto_id_lag": 1.5}, {"gl_closed_periods": -1}, {"gl_closed_periods": 13}],
)
def test_bad_source_numbers(changes: dict) -> None:
    with pytest.raises(ConfigError):
        from_dict(_with(**changes), env={})


def test_source_numbers() -> None:
    cfg = from_dict(_with(auto_id_lag=2000, gl_closed_periods=0), env={})
    assert (cfg.auto_id_lag, cfg.gl_closed_periods) == (2000, 0)


def test_archive_lists_cover_bridge_archives() -> None:
    assert set(RV_ARCHIVES).isdisjoint(PLAIN_ARCHIVES)
    assert sorted(RV_ARCHIVES + PLAIN_ARCHIVES) == sorted(READ_ARCHIVES)


def test_equipment_reads_by_rowversion() -> None:
    # 设备台账 EQ_EQData 有 rowversion ufts，桥的 archives/list 支持 changed_since：按水位增量读，缺省开启。
    assert "equipment" in RV_ARCHIVES
    assert "equipment" not in PLAIN_ARCHIVES


def test_source_types_never_collide_with_voucher_kinds() -> None:
    names = {*PROCESS_TYPES, *GL_TYPES, *NOTE_TYPES, *(ARCHIVE_PREFIX + a for a in RV_ARCHIVES + PLAIN_ARCHIVES)}
    assert names.isdisjoint(KIND_NAMES)
    assert set(SOURCE_NAMES) >= {"vouchers", "arap_process", "archives", "gl", "notes"}
