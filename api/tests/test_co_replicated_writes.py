"""第二级写入总开关（桥 enableReplicatedWrites）：403 feature_disabled 原样放行并带提示，与 test_account_only 区分；
健康检查透传桥的 replicated_writes（顶层与各分流桥），旧版桥不报则省略；OpenAPI 说明提到 feature_disabled。假桥，不访问网络。
"""

from __future__ import annotations

import pytest
from tests.test_co_bridge_routes import ProbeBridge
from tests.test_co_gate import FakeBridge, _client
from tests.test_co_core_routes import _auth
from tests.test_co_update_close_gen import _spec
from u8co_api.co_bridge import to_api_error
from u8co_api.co_client import BridgeRoutes
from u8co_api.errors import DEFAULT_HINTS, ApiError

_CLOSE = ("/v1/co/periods/close", {"module": "gl", "fiscal_year": 2026, "period": 9, "action": "close"})
_TIER2_OPS = (
    ("/v1/co/periods/close", "post"),
    ("/v1/co/openings/post", "post"),
    ("/v1/co/ia/post", "post"),
    ("/v1/co/gl/vouchers/unpost", "post"),
    ("/v1/co/gl/transfer/pnl", "post"),
    ("/v1/co/arap/bad_debt", "post"),
    ("/v1/co/arap/exchange_gain", "post"),
)


def test_bridge_code_maps_to_403_with_the_default_hint() -> None:
    error = to_api_error(403, {"ok": False, "code": "feature_disabled", "message": "第二级写入未打开"})
    assert (error.status, error.code) == (403, "feature_disabled")
    hint = DEFAULT_HINTS["feature_disabled"]
    assert "enableReplicatedWrites" in hint and len(hint) <= 60


def test_feature_disabled_passes_through_with_hint() -> None:
    fake = FakeBridge()
    fake.error = ApiError(403, "feature_disabled", "第二级写入未打开")
    path, body = _CLOSE
    denied = _client(fake).post(path, json=_auth(**body))
    assert denied.status_code == 403
    error = denied.json()["error"]
    assert (error["code"], error["message"]) == ("feature_disabled", "第二级写入未打开")
    assert error["retryable"] is False
    assert error["hint"] == DEFAULT_HINTS["feature_disabled"]
    assert len(fake.calls) == 1


def test_test_account_only_keeps_its_own_hint() -> None:
    fake = FakeBridge()
    fake.error = ApiError(403, "test_account_only", "月末结账只对配置为测试账套的账套开放")
    path, body = _CLOSE
    error = _client(fake).post(path, json=_auth(**body)).json()["error"]
    assert error["code"] == "test_account_only"
    assert error["hint"] == DEFAULT_HINTS["test_account_only"]


class _FlagBridge(ProbeBridge):
    def __init__(self, value: object) -> None:
        super().__init__()
        self._value = value

    def health(self) -> dict:
        return {"ok": True, "version": "test", "replicated_writes": self._value}


@pytest.mark.parametrize("value", [True, False])
def test_health_passes_the_flag_through(value: bool) -> None:
    data = _client(_FlagBridge(value)).get("/v1/co/health").json()
    assert data == {"ok": True, "version": "test", "replicated_writes": value}


@pytest.mark.parametrize("value", ["true", 1, None, {"on": True}])
def test_health_drops_a_non_bool_flag(value: object) -> None:
    assert _client(_FlagBridge(value)).get("/v1/co/health").json() == {"ok": True, "version": "test"}


def test_health_reports_the_flag_per_routed_bridge() -> None:
    client = _client(FakeBridge(), co_accounts=("803", "801", "906"))
    client.app.state.co_routes = BridgeRoutes(
        (("routes[0]", ("801",), _FlagBridge(True)), ("routes[1]", ("906",), _FlagBridge("yes"))),
    )
    routes = client.get("/v1/co/health").json()["routes"]
    assert routes == [
        {"route": "routes[0]", "accounts": ["801"], "ok": True, "version": "test", "replicated_writes": True},
        {"route": "routes[1]", "accounts": ["906"], "ok": True, "version": "test"},
    ]


def test_openapi_mentions_the_switch() -> None:
    spec = _spec()
    assert "feature_disabled" in spec["info"]["description"]
    for path, method in _TIER2_OPS:
        assert "feature_disabled" in spec["paths"][path][method]["description"], path
    health = spec["components"]["schemas"]["CoHealthOut"]["properties"]
    assert "enableReplicatedWrites" in health["replicated_writes"]["description"]
