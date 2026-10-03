"""Environment settings: no built-in bridge, empty account list, secret only from a file."""

from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

import pytest
from u8co_api.audit import configure_audit
from u8co_api.co_client import build_bridge
from u8co_api.config import _private_enough, load_settings, parse_zone

SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
BRIDGE = "http://192.0.2.10:18089/u8co"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("U8CO_"):
            monkeypatch.delenv(name)
    trust = tmp_path / "trust.json"
    trust.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("U8CO_TRUST_FILE", str(trust))

    def setter(**values: str) -> None:
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return setter


def _secret_file(tmp_path, text: str = SECRET, mode: int = 0o600) -> str:
    path = tmp_path / "bridge.secret"
    path.write_text(text + "\n", encoding="ascii")
    path.chmod(mode)
    return str(path)


def test_defaults_deny_everything(env) -> None:
    loaded = load_settings()
    assert loaded.enabled is True
    assert loaded.bridge_url == ""
    assert loaded.bridge_secret == ""
    assert loaded.configured is False
    assert loaded.accounts == ()
    assert loaded.trust == ()
    assert loaded.timezone == "+08:00"
    assert loaded.user_header == "X-U8co-User"
    assert loaded.audit_log == "stdout"
    assert loaded.jwt_leeway == 60
    assert build_bridge(loaded) is None
    assert "U8CO_BRIDGE_URL" in loaded.missing()
    assert "U8CO_ACCOUNTS" in loaded.missing()


def test_full_environment(env, tmp_path) -> None:
    env(
        U8CO_BRIDGE_URL=BRIDGE,
        U8CO_BRIDGE_SECRET_FILE=_secret_file(tmp_path),
        U8CO_BRIDGE_TIMEOUT="95",
        U8CO_CONCURRENCY="6",
        U8CO_CALLER_CONCURRENCY="3",
        U8CO_RPM="20",
        U8CO_ACCOUNTS="803, 902,803",
        U8CO_TIMEZONE="-05:30",
        U8CO_USER_HEADER="X-End-User",
        U8CO_AUDIT_LOG="stderr",
        U8CO_JWT_LEEWAY="30",
        U8CO_ENABLED="0",
    )
    loaded = load_settings()
    assert loaded.configured is True
    assert loaded.enabled is False
    assert loaded.bridge_timeout == 95
    assert (loaded.concurrency, loaded.caller_concurrency, loaded.rpm) == (6, 3, 20)
    assert loaded.accounts == ("803", "902")
    assert loaded.user_header == "X-End-User"
    assert loaded.audit_log == "stderr"
    assert loaded.jwt_leeway == 30
    assert SECRET not in repr(loaded)
    assert build_bridge(loaded) is None
    env(U8CO_ENABLED="1")
    assert build_bridge(load_settings()) is not None


def test_secret_is_only_read_from_a_file(env, tmp_path) -> None:
    env(U8CO_BRIDGE_URL=BRIDGE, U8CO_BRIDGE_SECRET=SECRET)
    assert load_settings().configured is False
    env(U8CO_BRIDGE_SECRET_FILE=str(tmp_path / "missing.secret"))
    assert load_settings().configured is False
    env(U8CO_BRIDGE_SECRET_FILE=_secret_file(tmp_path, SECRET.upper()))
    assert load_settings().configured is False
    env(U8CO_BRIDGE_SECRET_FILE=_secret_file(tmp_path))
    assert load_settings().configured is True


def test_secret_file_readable_by_others_is_refused(env, tmp_path, caplog) -> None:
    env(U8CO_BRIDGE_URL=BRIDGE)
    for mode in (0o640, 0o604, 0o660):
        env(U8CO_BRIDGE_SECRET_FILE=_secret_file(tmp_path, mode=mode))
        with caplog.at_level(logging.WARNING, logger="u8co.api"):
            loaded = load_settings()
        assert loaded.configured is False
        assert loaded.bridge_secret == ""
    assert "0400" in caplog.text
    env(U8CO_BRIDGE_SECRET_FILE=_secret_file(tmp_path, mode=0o400))
    assert load_settings().configured is True


def test_docker_secrets_skip_the_mode_check() -> None:
    info = os.stat_result((0o100644, 0, 0, 1, 0, 0, 64, 0, 0, 0))
    assert _private_enough("/run/secrets/u8co_bridge_secret", info) is True
    assert _private_enough("/config/bridge.secret", info) is False
    assert _private_enough("/run/secrets/../../config/bridge.secret", info) is False


def test_invalid_bridge_settings_stay_unconfigured(env, tmp_path) -> None:
    env(U8CO_BRIDGE_SECRET_FILE=_secret_file(tmp_path))
    for url in ("http://192.0.2.10:99999/u8co", "http://192.0.2.10:18089/other", "ftp://192.0.2.10/u8co"):
        env(U8CO_BRIDGE_URL=url)
        assert load_settings().configured is False
    env(U8CO_BRIDGE_URL=BRIDGE)
    for name, value in (("U8CO_BRIDGE_TIMEOUT", "79"), ("U8CO_BRIDGE_TIMEOUT", "nope"), ("U8CO_RPM", "0")):
        env(**{name: value})
        assert load_settings().configured is False
        env(U8CO_BRIDGE_TIMEOUT="80", U8CO_RPM="30")
    env(U8CO_CONCURRENCY="0")
    assert load_settings().configured is False
    env(U8CO_CONCURRENCY="8", U8CO_CALLER_CONCURRENCY="0")
    assert load_settings().configured is False
    env(U8CO_CALLER_CONCURRENCY="4")
    assert load_settings().configured is True


@pytest.mark.parametrize(
    ("name", "value"),
    (
        ("U8CO_ACCOUNTS", "803,12"),
        ("U8CO_TIMEZONE", "Asia/Example"),
        ("U8CO_TIMEZONE", "+15:00"),
        ("U8CO_USER_HEADER", "bad header"),
        ("U8CO_AUDIT_LOG", "relative.log"),
        ("U8CO_JWT_LEEWAY", "301"),
    ),
)
def test_malformed_values_stop_the_process(env, name: str, value: str) -> None:
    env(**{name: value})
    with pytest.raises(SystemExit):
        load_settings()


def test_zone_offsets() -> None:
    assert parse_zone("+08:00").utcoffset(None) == timedelta(hours=8)
    assert parse_zone("-03:30").utcoffset(None) == -timedelta(hours=3, minutes=30)


def test_explicit_missing_trust_file_stops_the_process(env, tmp_path) -> None:
    env(U8CO_TRUST_FILE=str(tmp_path / "missing.json"))
    with pytest.raises(SystemExit):
        load_settings()


def test_trust_from_file_and_env_are_merged(env, tmp_path) -> None:
    path = tmp_path / "trust.json"
    row = {"name": "app-a", "issuer": "https://idp.example.com/a", "audience": "u8co-api"}
    path.write_text(json.dumps([row]), encoding="utf-8")
    env(
        U8CO_TRUST_FILE=str(path),
        U8CO_OIDC_ISSUER="https://idp.example.com/b",
        U8CO_OIDC_AUDIENCE="u8co-api",
        U8CO_OIDC_WRITE_CLAIM="app_write",
        U8CO_OIDC_READ_CLAIM="app_read",
        U8CO_OIDC_ALGORITHMS="RS256, ES256",
    )
    trust = load_settings().trust
    assert [item.name for item in trust] == ["app-a", "default"]
    assert trust[1].write_claim == "app_write"
    assert trust[1].read_claim == "app_read"
    assert trust[1].algorithms == ("RS256", "ES256")
    assert trust[1].jwks_url == ""
    env(U8CO_OIDC_ISSUER="https://idp.example.com/a")
    with pytest.raises(SystemExit):
        load_settings()


def test_audit_can_go_to_a_file(tmp_path) -> None:
    target = tmp_path / "audit.log"
    configure_audit(str(target))
    logging.getLogger("u8co.audit").info('{"endpoint":"/healthz"}')
    configure_audit("off")
    logging.getLogger("u8co.audit").info('{"endpoint":"/dropped"}')
    configure_audit("stdout")
    assert target.read_text(encoding="utf-8").splitlines() == ['{"endpoint":"/healthz"}']
