"""长时操作的读桥超时：存货核算记账、期末处理和经过存货核算的月末结账用 U8CO_BRIDGE_LONG_TIMEOUT，
其他路由仍用 U8CO_BRIDGE_TIMEOUT。不访问网络（_send 换成记录超时的假函数）。"""

from __future__ import annotations

import os

import pytest
from u8co_api.co_client import CoBridge, build_bridge, is_long_call
from u8co_api.config import load_settings
from u8co_api.main import shutdown_grace

SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
BRIDGE = "http://192.0.2.10:18089/u8co"
_AUTH = {"acc": "803", "year": "2026", "operator": "op001", "password": "pw", "date": "2026-10-01"}


@pytest.mark.parametrize(
    ("path", "payload", "long"),
    [
        ("/v1/ia/post", {"action": "post"}, True),
        ("/v1/ia/period_end", {"action": "run"}, True),
        ("/v1/periods/close", {"module": "ia", "action": "close"}, True),
        ("/v1/periods/close", {"action": "close", "through": True}, True),
        ("/v1/periods/close", {"module": "gl", "action": "close"}, False),
        ("/v1/periods/close", {"module": "st", "action": "reopen"}, False),
        ("/v1/openings/post", {"module": "ia", "action": "post"}, True),
        ("/v1/openings/post", {"module": "pu", "action": "post"}, False),
        ("/v1/vouchers/create", {"module": "ia"}, False),
        ("/v1/login-check", {}, False),
    ],
)
def test_is_long_call(path: str, payload: dict, long: bool) -> None:
    assert is_long_call(path, payload) is long


def _seen_timeouts(bridge: CoBridge, monkeypatch: pytest.MonkeyPatch) -> list[float]:
    seen: list[float] = []

    def fake_send(*args, **kwargs):
        # 与 CoBridge._send(self, method, path, body, headers, timeout) 同序，只取最后的 timeout。
        seen.append(kwargs["timeout"] if "timeout" in kwargs else args[-1])
        return 200, b'{"ok":true}'

    monkeypatch.setattr(CoBridge, "_send", fake_send)
    return seen


def test_call_picks_the_timeout_by_route(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = CoBridge(BRIDGE, SECRET, timeout=90, long_timeout=1000)
    seen = _seen_timeouts(bridge, monkeypatch)
    bridge.call("/v1/login-check", dict(_AUTH))
    bridge.call("/v1/ia/post", dict(_AUTH, fiscal_year=2026, period=9, action="post"))
    bridge.call("/v1/periods/close", dict(_AUTH, module="ia", fiscal_year=2026, period=9, action="close"))
    bridge.call("/v1/periods/close", dict(_AUTH, module="gl", fiscal_year=2026, period=9, action="close"))
    bridge.health()
    assert seen == [90, 1000, 1000, 90, 90]


def test_long_timeout_never_shorter_than_the_normal_one(monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = CoBridge(BRIDGE, SECRET, timeout=120, long_timeout=60)
    seen = _seen_timeouts(bridge, monkeypatch)
    bridge.call("/v1/ia/period_end", dict(_AUTH, fiscal_year=2026, period=9, action="run"))
    assert seen == [120]


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("U8CO_"):
            monkeypatch.delenv(name)
    trust = tmp_path / "trust.json"
    trust.write_text("[]", encoding="utf-8")
    secret = tmp_path / "bridge.secret"
    secret.write_text(SECRET + "\n", encoding="ascii")
    secret.chmod(0o600)
    monkeypatch.setenv("U8CO_TRUST_FILE", str(trust))
    monkeypatch.setenv("U8CO_BRIDGE_URL", BRIDGE)
    monkeypatch.setenv("U8CO_BRIDGE_SECRET_FILE", str(secret))
    monkeypatch.setenv("U8CO_ACCOUNTS", "803")

    def setter(**values: str) -> None:
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return setter


def test_long_timeout_default_and_override(env) -> None:
    loaded = load_settings()
    assert (loaded.bridge_timeout, loaded.bridge_long_timeout) == (90, 1000)
    assert loaded.configured is True
    assert build_bridge(loaded)._long_timeout == 1000
    env(U8CO_BRIDGE_LONG_TIMEOUT="1500")
    assert build_bridge(load_settings())._long_timeout == 1500


def test_unset_long_timeout_follows_a_longer_normal_timeout(env) -> None:
    # 升级前已设 U8CO_BRIDGE_TIMEOUT=1200、没设长超时：长超时取 1200，仍是已配置。
    env(U8CO_BRIDGE_TIMEOUT="1200")
    loaded = load_settings()
    assert (loaded.bridge_timeout, loaded.bridge_long_timeout) == (1200, 1200)
    assert loaded.configured is True
    assert "U8CO_BRIDGE_LONG_TIMEOUT" not in loaded.missing()


@pytest.mark.parametrize("value", ["60", "nope"])
def test_long_timeout_below_the_normal_one_is_unconfigured(env, value: str) -> None:
    env(U8CO_BRIDGE_LONG_TIMEOUT=value)
    loaded = load_settings()
    assert loaded.configured is False
    assert "U8CO_BRIDGE_LONG_TIMEOUT" in loaded.missing()


@pytest.mark.parametrize(("raw", "want"), [(None, 100), ("1100", 1100), ("0", 100), ("-5", 100), ("abc", 100)])
def test_shutdown_grace(monkeypatch: pytest.MonkeyPatch, raw: str | None, want: int) -> None:
    if raw is None:
        monkeypatch.delenv("U8CO_SHUTDOWN_GRACE", raising=False)
    else:
        monkeypatch.setenv("U8CO_SHUTDOWN_GRACE", raw)
    assert shutdown_grace() == want
