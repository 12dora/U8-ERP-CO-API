"""JWKS 缓存。未知 kid 最多每五分钟刷新一次。

拉取失败会记住 fail_interval 秒，坏 kid 不能让每个请求都去访问网络。已有刷新在进行时，
其它请求不排队，直接失败。启动时预取每个来源。
来源是 jwks_url；没配 jwks_url 时按发行者的 OIDC discovery 文档取 jwks_uri（首次刷新时解析，之后不变）。
取密钥的地址必须是 https：路径上的中间人换掉公钥就能伪造令牌。http 只允许本机回环地址，或信任项显式写了
allow_insecure_http。跳转只跟随 https 到 https，其它一律拒绝。
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import override
from urllib.parse import urlsplit

from jwt.algorithms import ECAlgorithm, RSAAlgorithm

from u8co_api.errors import ApiError, unauthorized, unavailable

_MAX_BODY = 256 * 1024
_READERS = {"RSA": RSAAlgorithm, "EC": ECAlgorithm}
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def url_allowed(url: str, allow_insecure_http: bool = False) -> bool:
    """https 总是可以；http 只给回环地址或显式放行的信任项。"""
    parts = urlsplit(url)
    if not parts.hostname:
        return False
    if parts.scheme == "https":
        return True
    if parts.scheme != "http":
        return False
    return allow_insecure_http or parts.hostname.lower() in _LOOPBACK


@dataclass(frozen=True)
class KeySource:
    jwks_url: str = ""
    issuer: str = ""
    allow_insecure_http: bool = False

    @property
    def key(self) -> str:
        base = self.jwks_url or "discovery:" + self.issuer
        return base + ("#insecure" if self.allow_insecure_http else "")


@dataclass(frozen=True)
class Jwk:
    key: object
    kty: str
    alg: str | None


class JwksCache:
    def __init__(self, source: KeySource | str, fetch=None, clock=None, intervals=(300.0, 30.0)) -> None:
        self._source = source if isinstance(source, KeySource) else KeySource(jwks_url=source)
        self._url = self._source.jwks_url
        self._fetch = fetch or fetch_json
        self._clock = clock or time.monotonic
        self._min_interval, self._fail_interval = intervals
        self._lock = threading.Lock()
        self._keys: dict[str, Jwk] = {}
        self._fetched_at: float | None = None
        self._failed_at: float | None = None
        self._refreshing = False

    def prefetch(self) -> None:
        try:
            self._refresh()
        except ApiError:
            return

    def signing_key(self, kid: str) -> Jwk:
        found = self._keys.get(kid)
        if found is not None:
            return found
        self._refresh()
        found = self._keys.get(kid)
        if found is None:
            raise unauthorized("令牌签名密钥未知")
        return found

    def _refresh(self) -> None:
        if not self._begin_refresh():
            return
        try:
            keys = index_jwks(self._fetch(self._jwks_url()))
        except Exception as exc:
            self._note_failure()
            raise unavailable("无法获取签名密钥") from exc
        else:
            self._note_success(keys)
        finally:
            self._end_refresh()

    def _jwks_url(self) -> str:
        if not self._url:
            self._url = discover_jwks_url(self._source.issuer, self._fetch, self._source.allow_insecure_http)
        if not url_allowed(self._url, self._source.allow_insecure_http):
            raise unavailable("签名密钥地址必须是 https")
        return self._url

    def _begin_refresh(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._refreshing:
                return _idle_or_fail(self._keys)
            if _recent(self._failed_at, self._fail_interval, now):
                return _idle_or_fail(self._keys)
            if _recent(self._fetched_at, self._min_interval, now):
                return False
            self._refreshing = True
            return True

    def _note_failure(self) -> None:
        with self._lock:
            self._failed_at = self._clock()

    def _note_success(self, keys: dict[str, Jwk]) -> None:
        with self._lock:
            self._keys = keys
            self._fetched_at = self._clock()
            self._failed_at = None

    def _end_refresh(self) -> None:
        with self._lock:
            self._refreshing = False


def _idle_or_fail(keys: dict) -> bool:
    if keys:
        return False
    raise unavailable("无法获取签名密钥")


def _recent(stamp: float | None, interval: float, now: float) -> bool:
    return stamp is not None and now - stamp < interval


def index_jwks(payload: dict) -> dict[str, Jwk]:
    if not isinstance(payload, dict):
        raise unavailable("签名密钥格式无效")
    items = payload.get("keys", [])
    if not isinstance(items, list):
        raise unavailable("签名密钥格式无效")
    found: dict[str, Jwk] = {}
    for item in items:
        key = _public_key(item)
        if key is not None:
            found[str(item["kid"])] = key
    return found


def _public_key(item: object) -> Jwk | None:
    # 只收签名用的 RSA 和 EC 公钥；对称密钥（oct）一律忽略。
    if not isinstance(item, dict) or not item.get("kid"):
        return None
    reader = _READERS.get(str(item.get("kty")))
    if reader is None or item.get("use") == "enc":
        return None
    alg = item.get("alg")
    if alg is not None and not isinstance(alg, str):
        return None
    try:
        key = reader.from_jwk(json.dumps(item))
    except Exception:
        return None
    if hasattr(key, "private_bytes"):
        return None
    return Jwk(key, str(item["kty"]), alg)


class JwksRegistry:
    """每个来源一份缓存。共用同一来源的信任项共用缓存。"""

    def __init__(self, sources: tuple[KeySource, ...], fetch=None, clock=None) -> None:
        self._items: dict[str, JwksCache] = {}
        for source in sources:
            if source.key not in self._items:
                self._items[source.key] = JwksCache(source, fetch=fetch, clock=clock)

    def for_source(self, source: KeySource) -> JwksCache:
        cache = self._items.get(source.key)
        if cache is None:
            raise unauthorized("令牌签名密钥未知")
        return cache

    def prefetch(self) -> None:
        for cache in self._items.values():
            cache.prefetch()


def discover_jwks_url(issuer: str, fetch, allow_insecure_http: bool = False) -> str:
    """按 OpenID Connect Discovery 取 jwks_uri。文档里的 issuer 必须与配置一致，jwks_uri 同样要求 https。"""
    if not issuer or not url_allowed(issuer, allow_insecure_http):
        raise unavailable("认证服务未配置")
    document = fetch(issuer.rstrip("/") + "/.well-known/openid-configuration")
    if not isinstance(document, dict) or str(document.get("issuer", "")).rstrip("/") != issuer.rstrip("/"):
        raise unavailable("OIDC 发现文档的发行者不匹配")
    url = document.get("jwks_uri")
    if not isinstance(url, str) or not url_allowed(url, allow_insecure_http):
        raise unavailable("OIDC 发现文档没有有效的 jwks_uri")
    return url


class _HttpsRedirectOnly(urllib.request.HTTPRedirectHandler):
    # 只跟随 https 到 https 的跳转；http 来源或跳到 http/ftp 等一律当失败处理，防止降级。
    @override
    def redirect_request(self, req, *args):
        # args 依次是 fp, code, msg, headers, newurl（urllib 的固定签名）。
        fp, code, _msg, headers, newurl = args
        if urlsplit(req.full_url).scheme != "https" or urlsplit(newurl).scheme != "https":
            raise urllib.error.HTTPError(newurl, code, "拒绝跳转到非 https 地址", headers, fp)
        return super().redirect_request(req, *args)


_OPENER = urllib.request.build_opener(_HttpsRedirectOnly)


def fetch_json(url: str) -> dict:
    if not url or urlsplit(url).scheme not in {"http", "https"}:
        raise unavailable("认证服务未配置")
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with _OPENER.open(request, timeout=5) as response:
            body = response.read(_MAX_BODY + 1)
    except Exception as exc:
        raise unavailable("无法获取签名密钥") from exc
    return parse_json_body(body)


def parse_json_body(body: bytes) -> dict:
    if len(body) > _MAX_BODY:
        raise unavailable("签名密钥响应过大")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise unavailable("签名密钥格式无效") from exc
    if not isinstance(data, dict):
        raise unavailable("签名密钥格式无效")
    return data
