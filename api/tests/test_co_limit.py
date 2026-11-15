"""The global in-flight cap answers 429 before the bridge is called."""

from tests.support import base_claims, sign
from tests.test_co_gate import FakeBridge, _client, _login
from u8co_api.errors import ApiError
from u8co_api.limit import Limit


def test_global_cap_is_rate_limited():
    fake = FakeBridge()
    client = _client(fake)
    cap = Limit(rpm=100, concurrency=1)
    client.app.state.co_global_limit = cap
    assert cap.acquire("co")
    blocked = client.post("/v1/co/login-check", json=_login())
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert fake.calls == []
    cap.release("co")
    ok = client.post("/v1/co/login-check", json=_login())
    assert ok.status_code == 200
    assert len(fake.calls) == 1


def test_caller_cap_is_below_the_global_cap():
    client = _client()
    assert client.app.state.co_limit.concurrency == 4
    assert client.app.state.co_global_limit.concurrency == 8


def test_health_does_not_spend_caller_rpm():
    fake = FakeBridge()
    client = _client(fake)
    client.app.state.co_limit = Limit(rpm=1, concurrency=4)
    assert client.get("/v1/co/health").status_code == 200
    assert client.get("/v1/co/health").status_code == 200
    first = client.post("/v1/co/login-check", json=_login())
    assert first.status_code == 200
    blocked = client.post("/v1/co/login-check", json=_login())
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"


def test_bridge_error_releases_the_slot():
    fake = FakeBridge()
    fake.error = ApiError(409, "u8_rejected", "失败")
    client = _client(fake)
    denied = client.post("/v1/co/login-check", json=_login())
    assert denied.status_code == 409
    assert all(value == 0 for value in client.app.state.co_limit._inflight.values())
    assert all(value == 0 for value in client.app.state.co_global_limit._inflight.values())
    fake.error = None
    assert client.post("/v1/co/login-check", json=_login()).status_code == 200


def test_caller_limit_is_per_client_under_one_trust_entry():
    fake = FakeBridge()
    client = _client(fake, claims=base_claims(u8co_write=True, azp="tool-a"))
    client.app.state.co_limit = Limit(rpm=1, concurrency=4)
    assert client.post("/v1/co/login-check", json=_login()).status_code == 200
    assert client.post("/v1/co/login-check", json=_login()).status_code == 429
    other = {"Authorization": "Bearer " + sign(base_claims(u8co_write=True, azp="tool-b"))}
    assert client.post("/v1/co/login-check", json=_login(), headers=other).status_code == 200
    assert set(client.app.state.co_limit._hits) == {"tool:tool-a", "tool:tool-b"}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_idle_keys_are_dropped_after_a_window():
    clock = _Clock()
    cap = Limit(rpm=5, concurrency=2, window=60.0, clock=clock)
    for number in range(50):
        with cap.slot("user-" + str(number)):
            pass
    assert cap._inflight == {}
    assert len(cap._hits) == 50
    clock.now += 61
    with cap.slot("other"):
        assert set(cap._hits) == {"other"}
        assert cap._inflight == {"other": 1}
    assert cap._inflight == {}


def test_busy_key_survives_the_sweep_and_still_counts():
    clock = _Clock()
    cap = Limit(rpm=1, concurrency=1, window=60.0, clock=clock)
    assert cap.acquire("slow")
    clock.now += 61
    assert cap.acquire("other")
    assert "slow" in cap._inflight
    assert not cap.acquire("slow")
    cap.release("slow")
    assert cap.acquire("slow")


def test_refused_new_key_leaves_no_entry():
    cap = Limit(rpm=0, concurrency=1)
    assert not cap.acquire("nobody")
    assert cap._hits == {}
    assert cap._inflight == {}
