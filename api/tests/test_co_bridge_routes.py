"""按账套分流的桥（U8CO_BRIDGE_ROUTES_FILE）：配置校验、选桥、缺省回退、健康检查形状。不访问任何网络。"""

from __future__ import annotations

import json
import os
import threading
import time

import pytest
from tests.support import audit_line
from tests.test_co_gate import _SECRET, FakeBridge, _client, _login
from u8co_api import co_route_health
from u8co_api.co_client import PROBE_READ_TIMEOUT, BridgeRoutes, CoBridge, build_routes
from u8co_api.config import BridgeRoute, load_settings
from u8co_api.errors import ApiError

DEFAULT_SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
ROUTE_SECRET = "ffeeddccbbaa99887766554433221100ffeeddccbbaa99887766554433221100"
DEFAULT_URL = "http://203.0.113.10:18089/u8co"
ROUTE_URL = "http://203.0.113.10:18100/u8co"


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("U8CO_"):
            monkeypatch.delenv(name)
    trust = tmp_path / "trust.json"
    trust.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("U8CO_TRUST_FILE", str(trust))
    monkeypatch.setenv("U8CO_BRIDGE_URL", DEFAULT_URL)
    monkeypatch.setenv("U8CO_BRIDGE_SECRET_FILE", _secret(tmp_path, "default.secret", DEFAULT_SECRET))
    monkeypatch.setenv("U8CO_ACCOUNTS", "803,801,906")

    def setter(**values: str) -> None:
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return setter


def _secret(tmp_path, name: str, text: str, mode: int = 0o600) -> str:
    path = tmp_path / name
    path.write_text(text + "\n", encoding="ascii")
    path.chmod(mode)
    return str(path)


def _route(tmp_path, **extra) -> dict:
    data = {"accounts": ["801"], "url": ROUTE_URL, "secret_file": _secret(tmp_path, "route.secret", ROUTE_SECRET)}
    data.update(extra)
    return data


def _routes_file(env, tmp_path, data: object) -> None:
    path = tmp_path / "routes.json"
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    env(U8CO_BRIDGE_ROUTES_FILE=str(path))


def test_unset_routes_file_keeps_a_single_default_bridge(env) -> None:
    loaded = load_settings()
    assert loaded.configured is True
    assert loaded.bridge_routes == ()
    assert build_routes(loaded).entries == ()


def test_valid_routes_file_is_parsed(env, tmp_path) -> None:
    _routes_file(env, tmp_path, {"routes": [_route(tmp_path, timeout=120), {**_route(tmp_path), "accounts": ["906"]}]})
    loaded = load_settings()
    first, second = loaded.bridge_routes
    assert first == BridgeRoute(accounts=("801",), url=ROUTE_URL, secret=ROUTE_SECRET, timeout=120)
    assert second.accounts == ("906",) and second.timeout == 0
    # 密钥不进 repr，免得出现在日志或异常里。
    assert ROUTE_SECRET not in repr(loaded)


def test_route_timeouts_follow_the_default_when_unset(env, tmp_path) -> None:
    _routes_file(env, tmp_path, {"routes": [_route(tmp_path, timeout=120), {**_route(tmp_path), "accounts": ["906"]}]})
    env(U8CO_BRIDGE_LONG_TIMEOUT="1500")
    routes = build_routes(load_settings())
    (name0, accs0, bridge0), (name1, accs1, bridge1) = routes.entries
    assert (name0, accs0, name1, accs1) == ("routes[0]", ("801",), "routes[1]", ("906",))
    assert (bridge0._timeout, bridge0._long_timeout) == (120, 1500)
    assert (bridge1._timeout, bridge1._long_timeout) == (90, 1500)
    assert routes.pick("801")[0] == "routes[0]"
    assert routes.pick("803") is None


def test_routes_are_not_built_when_the_default_bridge_is_unconfigured(env, tmp_path) -> None:
    _routes_file(env, tmp_path, {"routes": [_route(tmp_path)]})
    env(U8CO_BRIDGE_URL="ftp://203.0.113.10/u8co")
    loaded = load_settings()
    assert loaded.configured is False
    assert build_routes(loaded).entries == ()


@pytest.mark.parametrize(
    ("data", "needle"),
    [
        ({"routes": [], "extra": 1}, "routes"),
        ([], "routes"),
        ({"routes": {}}, "routes"),
        ({"routes": ["801"]}, "必须是对象"),
        ({"routes": [{"accounts": ["801"], "url": ROUTE_URL}]}, "secret_file"),
        ({"routes": [{"accounts": ["801"], "url": ROUTE_URL, "secret_file": "/x", "token": "y"}]}, "token"),
    ],
)
def test_bad_structure_stops_startup(env, tmp_path, data: object, needle: str) -> None:
    _routes_file(env, tmp_path, data)
    with pytest.raises(SystemExit, match=needle):
        load_settings()


@pytest.mark.parametrize(
    ("extra", "needle"),
    [
        ({"accounts": []}, "非空"),
        ({"accounts": [801]}, "三位数字"),
        ({"accounts": ["95"]}, "三位数字"),
        ({"accounts": ["907"]}, "不在 U8CO_ACCOUNTS"),
        ({"accounts": ["801", "801"]}, "重复"),
        ({"url": "http://203.0.113.10:18100/other"}, "url"),
        ({"url": "ftp://203.0.113.10/u8co"}, "url"),
        ({"timeout": 30}, "timeout"),
        ({"timeout": True}, "timeout"),
        ({"timeout": "120"}, "timeout"),
        ({"secret_file": ""}, "secret_file"),
    ],
)
def test_bad_route_fields_stop_startup(env, tmp_path, extra: dict, needle: str) -> None:
    _routes_file(env, tmp_path, {"routes": [_route(tmp_path, **extra)]})
    with pytest.raises(SystemExit, match=needle):
        load_settings()


def test_account_in_two_routes_stops_startup(env, tmp_path) -> None:
    _routes_file(env, tmp_path, {"routes": [_route(tmp_path), {**_route(tmp_path), "accounts": ["906", "801"]}]})
    with pytest.raises(SystemExit, match="801 出现在多条路由里"):
        load_settings()


def test_bad_secret_files_stop_startup(env, tmp_path) -> None:
    loose = _secret(tmp_path, "loose.secret", ROUTE_SECRET, mode=0o644)
    weak = _secret(tmp_path, "weak.secret", "abc")
    for path, needle in ((loose, "0600"), (weak, "64 位"), (str(tmp_path / "absent"), "读不到")):
        _routes_file(env, tmp_path, {"routes": [_route(tmp_path, secret_file=path)]})
        with pytest.raises(SystemExit, match=needle) as caught:
            load_settings()
        assert ROUTE_SECRET not in str(caught.value)


def test_unreadable_or_invalid_routes_file_stops_startup(env, tmp_path) -> None:
    env(U8CO_BRIDGE_ROUTES_FILE=str(tmp_path / "missing.json"))
    with pytest.raises(SystemExit, match="读不到"):
        load_settings()
    _routes_file(env, tmp_path, "{not json")
    with pytest.raises(SystemExit, match="JSON"):
        load_settings()


def _routed_client(default: FakeBridge, routed: FakeBridge):
    client = _client(default, co_accounts=("803", "801"))
    client.app.state.co_routes = BridgeRoutes((("routes[0]", ("801",), routed),))
    return client


def test_routed_account_uses_its_bridge_and_others_fall_back(capsys) -> None:
    default, routed = FakeBridge(), FakeBridge()
    client = _routed_client(default, routed)
    assert client.post("/v1/co/login-check", json=_login(acc="801")).status_code == 200
    assert [payload["acc"] for _path, payload in routed.calls] == ["801"]
    assert default.calls == []
    assert audit_line(capsys.readouterr().out, "/v1/co/login-check")["bridge"] == "routes[0]"
    assert client.post("/v1/co/login-check", json=_login()).status_code == 200
    assert [payload["acc"] for _path, payload in default.calls] == ["803"]
    assert len(routed.calls) == 1
    out = capsys.readouterr().out
    assert audit_line(out, "/v1/co/login-check")["bridge"] == "default"
    assert _SECRET not in out


def test_routing_never_bypasses_the_account_allow_list() -> None:
    default, routed = FakeBridge(), FakeBridge()
    client = _client(default, co_accounts=("803",))
    client.app.state.co_routes = BridgeRoutes((("routes[0]", ("801",), routed),))
    denied = client.post("/v1/co/login-check", json=_login(acc="801"))
    assert denied.status_code == 403
    assert routed.calls == [] and default.calls == []


def test_health_without_routes_keeps_the_old_shape() -> None:
    client = _client(FakeBridge())
    assert client.get("/v1/co/health").json() == {"ok": True, "version": "test"}
    assert "bridge_routes" not in client.get("/healthz").json()


class ProbeBridge(FakeBridge):
    """分流桥的替身：健康检查走 probe（短超时），记下被探测的次数。"""

    def __init__(self, release: threading.Event | None = None) -> None:
        super().__init__()
        self.probes = 0
        self._release = release

    def probe(self) -> dict:
        self.probes += 1
        if self._release is not None:
            self._release.wait(5)
        return self.health()


def test_health_reports_each_routed_bridge(capsys) -> None:
    down = ProbeBridge()
    down.error = ApiError(503, "unavailable", "CO 桥不可达")
    client = _client(FakeBridge(), co_accounts=("803", "801", "906"))
    client.app.state.co_routes = BridgeRoutes(
        (("routes[0]", ("801",), ProbeBridge()), ("routes[1]", ("906",), down)),
    )
    reply = client.get("/v1/co/health")
    assert reply.status_code == 200
    assert reply.json() == {
        "ok": True,
        "version": "test",
        "routes": [
            {"route": "routes[0]", "accounts": ["801"], "ok": True, "version": "test"},
            {"route": "routes[1]", "accounts": ["906"], "ok": False, "error": "unavailable"},
        ],
    }
    assert audit_line(capsys.readouterr().out, "/v1/co/health")["bridge"] == "default"
    assert client.get("/healthz").json()["bridge_routes"] == 2


def test_hung_routed_bridges_do_not_stall_health(monkeypatch: pytest.MonkeyPatch) -> None:
    # 两个卡住的分流桥并行探测，按总时限一起判为不可用；缺省桥照常报告。
    monkeypatch.setattr(co_route_health, "PROBE_DEADLINE", 0.3)
    release = threading.Event()
    hung_a, hung_b, fine = ProbeBridge(release), ProbeBridge(release), ProbeBridge()
    client = _client(FakeBridge(), co_accounts=("803", "801", "906", "907"))
    client.app.state.co_routes = BridgeRoutes(
        (("routes[0]", ("801",), hung_a), ("routes[1]", ("906",), hung_b), ("routes[2]", ("907",), fine)),
    )
    try:
        started = time.monotonic()
        reply = client.get("/v1/co/health")
        took = time.monotonic() - started
    finally:
        release.set()
    assert reply.status_code == 200
    assert took < 2.0
    routes = reply.json()["routes"]
    assert [item["ok"] for item in routes] == [False, False, True]
    assert routes[0]["error"] == routes[1]["error"] == "unavailable"
    assert (hung_a.probes, hung_b.probes, fine.probes) == (1, 1, 1)
    assert reply.json()["ok"] is True


def test_probe_uses_a_short_read_timeout_and_health_keeps_the_configured_one() -> None:
    bridge = CoBridge(ROUTE_URL, ROUTE_SECRET, timeout=90)
    seen: list[float] = []

    def fake_send(method, path, body, headers, timeout):
        seen.append(timeout)
        return 200, b'{"ok":true,"version":"t"}'

    bridge._send = fake_send
    assert bridge.probe() == {"ok": True, "version": "t"}
    assert bridge.health() == {"ok": True, "version": "t"}
    assert seen == [PROBE_READ_TIMEOUT, 90]
    assert PROBE_READ_TIMEOUT < 80
