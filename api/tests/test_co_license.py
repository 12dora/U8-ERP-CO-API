"""U8 许可点数已满（u8_license_full）、Retry-After（含本服务的 rate_limited）和健康检查的许可字段。假桥，不访问网络。"""

from __future__ import annotations

import pytest
from tests.test_co_gate import FakeBridge, _client, _login, bridge_body
from u8co_api.co_bridge import to_api_error
from u8co_api.co_models import CoHealthOut, license_detail
from u8co_api.co_models_license import CoLicensePack, license_packs, license_source
from u8co_api.errors import ApiError
from u8co_api.limit import RETRY_DEFAULT, Limit

_FULL = "U8 许可点数已满（子系统 SA），请稍后重试：加密点数已饱和"


class _Cleaned(FakeBridge):
    def call(self, path: str, payload: dict) -> dict:
        self.calls.append((path, dict(payload)))
        return dict(bridge_body(path, payload), orphan_tasks_cleaned=2)


class _HealthBridge(FakeBridge):
    def __init__(self, body: dict) -> None:
        super().__init__()
        self.body = body

    def health(self) -> dict:
        return dict(self.body)


def test_license_full_maps_to_503_and_keeps_code_and_message() -> None:
    error = to_api_error(503, {"ok": False, "code": "u8_license_full", "message": _FULL})
    assert error.status == 503
    assert error.code == "u8_license_full"
    assert error.message == _FULL
    assert error.retry_after == 60


def test_license_full_status_does_not_depend_on_bridge_status() -> None:
    error = to_api_error(422, {"ok": False, "code": "u8_license_full", "message": _FULL})
    assert error.status == 503
    assert error.retry_after == 60


@pytest.mark.parametrize(("status", "code"), ((429, "busy"), (503, "busy_timeout"), (503, "stopping")))
def test_queue_errors_retry_after_five_seconds(status: int, code: str) -> None:
    error = to_api_error(status, {"ok": False, "code": code, "message": "稍后"})
    assert error.status == status
    assert error.retry_after == 5


@pytest.mark.parametrize(
    ("status", "code"),
    ((422, "login_failed"), (503, "com_unavailable"), (503, "store_unavailable"), (504, "outcome_unknown")),
)
def test_other_errors_have_no_retry_after(status: int, code: str) -> None:
    assert to_api_error(status, {"ok": False, "code": code, "message": "x"}).retry_after is None


def test_route_sends_retry_after_and_same_envelope() -> None:
    fake = FakeBridge()
    fake.error = to_api_error(503, {"ok": False, "code": "u8_license_full", "message": _FULL})
    response = _client(fake).post("/v1/co/login-check", json=_login())
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "60"
    assert response.json() == {
        "error": {
            "code": "u8_license_full",
            "message": _FULL,
            "retryable": True,
            "hint": "U8 许可点数已满，60 秒后重试",
        }
    }


def test_route_busy_timeout_retry_after() -> None:
    fake = FakeBridge()
    fake.error = to_api_error(503, {"ok": False, "code": "busy_timeout", "message": "排队超时，可以重试"})
    response = _client(fake).post("/v1/co/login-check", json=_login())
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"


def test_plain_errors_have_no_retry_after_header() -> None:
    fake = FakeBridge()
    fake.error = ApiError(503, "unavailable", "CO 桥不可达")
    response = _client(fake).post("/v1/co/login-check", json=_login())
    assert response.status_code == 503
    assert "Retry-After" not in response.headers


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_rate_limited_rpm_retry_after_is_time_until_window_frees() -> None:
    clock = _Clock()
    client = _client(FakeBridge())
    client.app.state.co_limit = Limit(rpm=1, concurrency=4, window=60.0, clock=clock)
    assert client.post("/v1/co/login-check", json=_login()).status_code == 200
    clock.now += 20.5
    blocked = client.post("/v1/co/login-check", json=_login())
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert blocked.headers["Retry-After"] == "40"


def test_rate_limited_concurrency_retry_after_default() -> None:
    fake = FakeBridge()
    client = _client(fake)
    cap = Limit(rpm=100, concurrency=1)
    client.app.state.co_global_limit = cap
    assert cap.acquire("co")
    blocked = client.post("/v1/co/login-check", json=_login())
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == str(RETRY_DEFAULT) == "5"
    assert fake.calls == []
    cap.release("co")


def test_limit_retry_after_never_below_one_second() -> None:
    clock = _Clock()
    cap = Limit(rpm=1, concurrency=4, window=60.0, clock=clock)
    assert cap.acquire("k")
    cap.release("k")
    clock.now += 59.9
    assert not cap.acquire("k")
    assert cap.retry_after("k") == 1
    assert Limit(rpm=0, concurrency=1).retry_after("nobody") == RETRY_DEFAULT


def test_unauthorized_keeps_bearer_challenge() -> None:
    response = _client(FakeBridge(), auth=False).get("/v1/co/health")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert "Retry-After" not in response.headers


def test_health_passes_license_fields() -> None:
    detail = {"SA": {"used": 3, "limit": 10, "full_24h": 0}, "GL": {"used": 1, "limit": 6, "full_24h": 2}}
    fake = _HealthBridge({"ok": True, "version": "t", "license": "near", "license_detail": detail, "workers": 4})
    response = _client(fake).get("/v1/co/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "version": "t", "license": "near", "license_detail": detail}


def test_health_from_old_bridge_omits_license_fields() -> None:
    response = _client(_HealthBridge({"ok": True, "version": "t"})).get("/v1/co/health")
    assert response.json() == {"ok": True, "version": "t"}


def test_health_drops_unknown_license_state() -> None:
    fake = _HealthBridge({"ok": True, "version": "t", "license": "Unknown:x", "license_detail": "SA:10"})
    assert _client(fake).get("/v1/co/health").json() == {"ok": True, "version": "t"}


def test_license_detail_keeps_only_two_letter_subsystems_and_known_counts() -> None:
    raw = {
        "SA": {"used": 3, "limit": 10, "full_24h": 0, "who": "x"},
        "Unknown": {"used": 1},
        "DN": {"used": -1, "limit": True, "full_24h": "2"},
        "gl": {"used": 1},
        "AP": {"used": 2.5, "limit": 6},
        "QM": "10",
    }
    assert license_detail(raw) == {"SA": {"used": 3, "limit": 10, "full_24h": 0}, "AP": {"limit": 6}}


def test_license_detail_empty_or_wrong_type_is_none() -> None:
    assert license_detail({}) is None
    assert license_detail([]) is None
    assert license_detail({"XX": {}}) is None


def test_model_accepts_every_documented_state() -> None:
    for state in ("ok", "near", "full", "unknown"):
        assert CoHealthOut(ok=True, version="t", license=state).license == state


def test_health_passes_license_source_and_packs() -> None:
    packs = {"01": {"used": 3, "limit": 10, "modules": ["SA", "ST"]}, "GL": {"used": 0, "limit": 4, "modules": []}}
    fake = _HealthBridge({"ok": True, "version": "t", "license_source": "leases", "license_packs": packs})
    response = _client(fake).get("/v1/co/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True, "version": "t", "license_source": "leases", "license_packs": packs}


def test_health_keeps_pack_without_limit() -> None:
    # 桥知道租约数、不知道总数时省略 limit：包照样返回，limit 不出现（也不是 null）。
    packs = {"XX": {"used": 3, "modules": ["SA"]}, "YY": {"used": 1, "limit": 4, "modules": ["GL"]}}
    fake = _HealthBridge({"ok": True, "version": "t", "license_source": "leases", "license_packs": packs})
    response = _client(fake).get("/v1/co/health")
    assert response.status_code == 200
    assert response.json()["license_packs"] == packs
    assert CoLicensePack(used=3, modules=["SA"]).limit is None


def test_health_drops_malformed_license_source_and_packs() -> None:
    fake = _HealthBridge({"ok": True, "version": "t", "license_source": "dongle", "license_packs": ["01"]})
    assert _client(fake).get("/v1/co/health").json() == {"ok": True, "version": "t"}


def test_license_source_keeps_only_known_values() -> None:
    assert license_source("leases") == "leases"
    assert license_source("tasklog") == "tasklog"
    for bad in ("LEASES", "", None, 1, ["leases"]):
        assert license_source(bad) is None


def test_license_packs_drops_malformed_entries() -> None:
    raw = {
        "SA": {"used": 3, "limit": 10, "modules": ["SA", "sa", "GL1", 7, "A1"], "who": "x"},
        "sa": {"used": 1, "limit": 2, "modules": []},
        "ABC": {"used": 1, "limit": 2, "modules": []},
        "02": {"used": -1, "limit": 2, "modules": []},
        "03": {"used": True, "limit": 2, "modules": []},
        "04": {"used": 1, "limit": "2", "modules": []},
        "05": {"used": 1, "limit": 2},
        "08": {"used": 1, "limit": None, "modules": []},
        "09": {"modules": []},
        "06": {"used": 1, "limit": 2, "modules": "SA"},
        "07": "10",
    }
    assert license_packs(raw) == {
        "SA": {"used": 3, "limit": 10, "modules": ["SA", "A1"]},
        "08": {"used": 1, "modules": []},
    }
    assert license_packs({}) is None
    assert license_packs([]) is None
    assert license_packs({"XX": {}}) is None


def test_license_packs_caps_packs_and_modules() -> None:
    modules = [f"M{n}" for n in range(10)] + [f"N{n}" for n in range(10)]
    raw = {f"{n:02d}": {"used": n, "limit": 99, "modules": modules} for n in range(40)}
    kept = license_packs(raw)
    assert kept is not None
    assert list(kept) == [f"{n:02d}" for n in range(32)]
    assert kept["00"]["modules"] == modules[:16]


def _approve() -> dict:
    return _login(type="qm_product_check", id=7, year="2026", date="2026-09-27")


def test_workflow_action_passes_orphan_tasks_cleaned() -> None:
    ok = _client(_Cleaned()).post("/v1/co/workflow/approve", json=_approve())
    assert ok.status_code == 200, ok.text
    assert ok.json()["orphan_tasks_cleaned"] == 2


def test_workflow_action_omits_orphan_tasks_cleaned_when_absent() -> None:
    ok = _client(FakeBridge()).post("/v1/co/workflow/approve", json=_approve())
    assert ok.status_code == 200, ok.text
    assert "orphan_tasks_cleaned" not in ok.json()
