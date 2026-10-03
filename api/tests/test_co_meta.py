"""GET /v1/co/meta：只读权限可调、代理到桥的 meta、进程内缓存 60 秒、桥出错的映射。"""

from __future__ import annotations

import threading
import time

from tests.support import base_claims
from tests.test_co_gate import FakeBridge, _client
from u8co_api.co_meta import MetaCache
from u8co_api.errors import ApiError

_META = {
    "ok": True,
    "version": "1",
    "revision": "ab" * 32,
    "complete": True,
    "kinds": [
        {
            "name": "sale_order",
            "ops": {"create": True},
            "writable": {"create": {"head": {"exact": ["ccuscode"], "spans": []}, "lines": None}},
        }
    ],
    "archives": [{"name": "customer", "tags": None, "tags_error": "U8 EAI 字段表无法读取"}],
    "gl": {"head": ["sign"]},
    "list_kinds": ["sale_order"],
    "routes": [{"path": "/u8co/v1/meta", "keys": []}],
    "features": {},
}


class MetaBridge(FakeBridge):
    def __init__(self) -> None:
        super().__init__()
        self.meta_calls = 0

    def meta(self) -> dict:
        self.meta_calls += 1
        if self.error is not None:
            raise self.error
        return dict(_META)


def _reader(fake: MetaBridge):
    return _client(fake, claims=base_claims(u8co_read=True))


def test_read_only_caller_gets_bridge_meta_unchanged() -> None:
    fake = MetaBridge()
    ok = _reader(fake).get("/v1/co/meta")
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["revision"] == _META["revision"]
    assert body["kinds"][0]["writable"]["create"]["lines"] is None
    assert body["archives"][0]["tags_error"] == "U8 EAI 字段表无法读取"
    assert fake.meta_calls == 1
    assert fake.calls == []


def test_meta_is_cached_per_app() -> None:
    fake = MetaBridge()
    client = _reader(fake)
    for _ in range(3):
        assert client.get("/v1/co/meta").status_code == 200
    assert fake.meta_calls == 1


def test_meta_needs_a_co_claim() -> None:
    fake = MetaBridge()
    denied = _client(fake, claims=base_claims()).get("/v1/co/meta")
    assert denied.status_code == 403
    assert fake.meta_calls == 0


def test_meta_needs_a_token() -> None:
    fake = MetaBridge()
    denied = _client(fake, auth=False).get("/v1/co/meta")
    assert denied.status_code == 401
    assert fake.meta_calls == 0


def test_meta_is_404_when_co_is_disabled() -> None:
    fake = MetaBridge()
    missing = _client(fake, co_enabled=False).get("/v1/co/meta")
    assert missing.status_code == 404
    assert fake.meta_calls == 0


def test_outcome_unknown_becomes_unavailable_and_is_not_cached() -> None:
    fake = MetaBridge()
    client = _reader(fake)
    fake.error = ApiError(504, "outcome_unknown", "结果未知")
    failed = client.get("/v1/co/meta")
    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "unavailable"
    fake.error = None
    assert client.get("/v1/co/meta").status_code == 200
    assert fake.meta_calls == 2


def test_other_bridge_errors_pass_through() -> None:
    fake = MetaBridge()
    fake.error = ApiError(502, "bad_response", "桥的响应无法解析")
    failed = _reader(fake).get("/v1/co/meta")
    assert failed.status_code == 502
    assert failed.json()["error"]["code"] == "bad_response"


def test_meta_is_in_openapi_as_read() -> None:
    client = _reader(MetaBridge())
    spec = client.get("/v1/openapi.json").json()
    operation = spec["paths"]["/v1/co/meta"]["get"]
    assert operation["operationId"] == "coMeta"
    assert "权限：只读" in operation["description"]


def test_cache_expires_after_ttl() -> None:
    now = [100.0]
    cache = MetaCache(ttl=60.0, clock=lambda: now[0])
    calls: list[int] = []

    def fetch() -> dict:
        calls.append(1)
        return {"n": len(calls)}

    assert cache.get(fetch) == {"n": 1}
    now[0] = 159.9
    assert cache.get(fetch) == {"n": 1}
    now[0] = 160.0
    assert cache.get(fetch) == {"n": 2}


def test_cache_does_not_store_failures() -> None:
    cache = MetaCache(ttl=60.0, clock=lambda: 0.0)

    def boom() -> dict:
        raise ApiError(503, "unavailable", "x")

    try:
        cache.get(boom)
    except ApiError:
        pass
    assert cache.get(lambda: {"ok": True}) == {"ok": True}


def _gate_fetch(release: threading.Event, started: threading.Event, calls: list[int], error: bool = False):
    def fetch() -> dict:
        calls.append(1)
        started.set()
        release.wait(5)
        if error:
            raise ApiError(503, "unavailable", "x")
        return {"n": len(calls)}

    return fetch


def _followers(cache: MetaCache, count: int) -> tuple[list[threading.Thread], list[object]]:
    results: list[object] = []

    def follow() -> None:
        try:
            results.append(cache.get(lambda: {"unexpected": True}))
        except ApiError as exc:
            results.append(exc.code)

    threads = [threading.Thread(target=follow) for _ in range(count)]
    for thread in threads:
        thread.start()
    return threads, results


def test_single_flight_shares_one_bridge_call() -> None:
    cache = MetaCache(ttl=60.0)
    release, started, calls = threading.Event(), threading.Event(), []
    leader: list[dict] = []
    lead = threading.Thread(target=lambda: leader.append(cache.get(_gate_fetch(release, started, calls))))
    lead.start()
    assert started.wait(5)
    threads, results = _followers(cache, 3)
    release.set()
    for thread in [lead, *threads]:
        thread.join(5)
    assert calls == [1]
    assert leader == [{"n": 1}]
    assert results == [{"n": 1}] * 3


def test_single_flight_failure_reaches_followers_and_is_not_cached() -> None:
    cache = MetaCache(ttl=60.0)
    release, started, calls = threading.Event(), threading.Event(), []
    lead = threading.Thread(target=lambda: _swallow(cache, _gate_fetch(release, started, calls, error=True)))
    lead.start()
    assert started.wait(5)
    threads, results = _followers(cache, 2)
    time.sleep(0.2)  # 让跟随者先进入等待，再放行领头的失败
    release.set()
    for thread in [lead, *threads]:
        thread.join(5)
    assert results == ["unavailable", "unavailable"]
    assert cache.get(lambda: {"ok": True}) == {"ok": True}


def test_follower_gives_up_after_wait() -> None:
    cache = MetaCache(ttl=60.0, wait=0.05)
    release, started, calls = threading.Event(), threading.Event(), []
    lead = threading.Thread(target=lambda: cache.get(_gate_fetch(release, started, calls)))
    lead.start()
    assert started.wait(5)
    threads, results = _followers(cache, 1)
    threads[0].join(5)
    release.set()
    lead.join(5)
    assert results == ["unavailable"]


def _swallow(cache: MetaCache, fetch) -> None:
    try:
        cache.get(fetch)
    except ApiError:
        pass
