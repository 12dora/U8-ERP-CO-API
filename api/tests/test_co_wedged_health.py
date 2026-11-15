"""桥健康检查的 wedged_logins 透传：形状正确原样返回，active 为 true 时仍 200；形状不对整项省略。假桥，不访问网络。"""

from __future__ import annotations

import pytest
from tests.test_co_bridge_routes import ProbeBridge
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_update_close_gen import _spec
from u8co_api.co_client import BridgeRoutes

_WEDGED = {
    "active": False,
    "count_24h": 0,
    "last_at": None,
    "recycles_24h": 0,
    "restarts_24h": 0,
}


class _WedgedBridge(FakeBridge):
    def __init__(self, body: dict) -> None:
        super().__init__()
        self.body = body

    def health(self) -> dict:
        return dict(self.body)


class _RouteWedged(ProbeBridge):
    def __init__(self, value: object) -> None:
        super().__init__()
        self._value = value

    def health(self) -> dict:
        return {"ok": True, "version": "test", "wedged_logins": self._value}


def test_health_passes_wedged_logins() -> None:
    fake = _WedgedBridge({"ok": True, "version": "t", "wedged_logins": _WEDGED})
    response = _client(fake).get("/v1/co/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "version": "t", "wedged_logins": _WEDGED}


def test_health_active_wedged_logins_still_200() -> None:
    wedged = {**_WEDGED, "active": True, "count_24h": 2, "last_at": "2026-10-30T12:00:00Z"}
    fake = _WedgedBridge({"ok": True, "version": "t", "wedged_logins": wedged})
    response = _client(fake).get("/v1/co/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "version": "t", "wedged_logins": wedged}


@pytest.mark.parametrize(
    "raw",
    (
        {"active": 1, "count_24h": 0, "last_at": None, "recycles_24h": 0, "restarts_24h": 0},
        {"active": True},
        {"operator": "op001"},
    ),
)
def test_health_drops_malformed_wedged_logins(raw: object) -> None:
    fake = _WedgedBridge({"ok": True, "version": "t", "wedged_logins": raw})
    assert _client(fake).get("/v1/co/health").json() == {"ok": True, "version": "t"}


def test_health_from_old_bridge_omits_wedged_logins() -> None:
    assert _client(_WedgedBridge({"ok": True, "version": "t"})).get("/v1/co/health").json() == {
        "ok": True,
        "version": "t",
    }


def test_health_reports_wedged_logins_per_routed_bridge() -> None:
    client = _client(FakeBridge(), co_accounts=("803", "801", "906"))
    client.app.state.co_routes = BridgeRoutes(
        (("routes[0]", ("801",), _RouteWedged(_WEDGED)), ("routes[1]", ("906",), _RouteWedged({"active": 1}))),
    )
    routes = client.get("/v1/co/health").json()["routes"]
    assert routes == [
        {"route": "routes[0]", "accounts": ["801"], "ok": True, "version": "test", "wedged_logins": _WEDGED},
        {"route": "routes[1]", "accounts": ["906"], "ok": True, "version": "test"},
    ]


def test_openapi_has_wedged_logins() -> None:
    health = _spec()["components"]["schemas"]["CoHealthOut"]["properties"]
    assert "wedged_logins" in health
