"""Generic OIDC trust with locally generated keys. No network: every fetch is a local dict."""

from __future__ import annotations

import time
import urllib.error
import urllib.request

import jwt
import pytest
from tests.support import (
    AUD,
    EC_KEY,
    JWK,
    JWK2,
    JWKS_URL,
    KEY,
    KEY2,
    base_claims,
    entry,
    public_jwk,
    settings,
    sign,
)
from u8co_api.auth import verify_bearer
from u8co_api.errors import ApiError
from u8co_api.jwks import JwksCache, JwksRegistry, KeySource, _HttpsRedirectOnly, index_jwks, url_allowed

ISS_B = "https://login.example.com/tenant-b/v2.0"
JWKS_B = "https://login.example.com/tenant-b/keys"
DISCOVERY_B = ISS_B + "/.well-known/openid-configuration"


class Fetcher:
    def __init__(self, documents: dict[str, dict]) -> None:
        self.documents = documents
        self.urls: list[str] = []

    def __call__(self, url: str) -> dict:
        self.urls.append(url)
        return self.documents[url]


def _registry(trust, documents: dict[str, dict]) -> JwksRegistry:
    return JwksRegistry(tuple(item.key_source for item in trust), fetch=Fetcher(documents))


def _status(token: str, config, keys) -> int:
    with pytest.raises(ApiError) as caught:
        verify_bearer(token, config, keys)
    return caught.value.status


def test_two_issuers_each_use_their_own_keys() -> None:
    trust = (entry(name="app-a"), entry(name="app-b", issuer=ISS_B, jwks_url=JWKS_B))
    keys = _registry(trust, {JWKS_URL: {"keys": [JWK]}, JWKS_B: {"keys": [JWK2]}})
    config = settings(trust=trust)
    first = verify_bearer(sign(base_claims(u8co_write=True)), config, keys)
    assert (first.name, first.write, first.read) == ("app-a", True, False)
    second = verify_bearer(sign(base_claims(iss=ISS_B, u8co_read=True), KEY2, "k2"), config, keys)
    assert (second.name, second.write, second.read) == ("app-b", False, True)
    forged = sign(base_claims(iss=ISS_B, u8co_write=True), KEY, "k1")
    assert _status(forged, config, keys) == 401


def test_discovery_resolves_jwks_uri_once() -> None:
    trust = (entry(name="app-b", issuer=ISS_B, jwks_url=""),)
    fetch = Fetcher({DISCOVERY_B: {"issuer": ISS_B, "jwks_uri": JWKS_B}, JWKS_B: {"keys": [JWK2]}})
    keys = JwksRegistry(tuple(item.key_source for item in trust), fetch=fetch)
    keys.prefetch()
    caller = verify_bearer(sign(base_claims(iss=ISS_B, u8co_write=True), KEY2, "k2"), settings(trust=trust), keys)
    assert caller.write is True
    assert fetch.urls == [DISCOVERY_B, JWKS_B]


def test_discovery_rejects_a_foreign_issuer() -> None:
    trust = (entry(name="app-b", issuer=ISS_B, jwks_url=""),)
    fetch = {DISCOVERY_B: {"issuer": "https://other.example.com", "jwks_uri": JWKS_B}, JWKS_B: {"keys": [JWK2]}}
    token = sign(base_claims(iss=ISS_B, u8co_write=True), KEY2, "k2")
    assert _status(token, settings(trust=trust), _registry(trust, fetch)) == 503


def test_discovery_rejects_a_plain_http_jwks_uri() -> None:
    trust = (entry(name="app-b", issuer=ISS_B, jwks_url=""),)
    plain = "http://login.example.com/tenant-b/keys"
    fetch = Fetcher({DISCOVERY_B: {"issuer": ISS_B, "jwks_uri": plain}, plain: {"keys": [JWK2]}})
    token = sign(base_claims(iss=ISS_B, u8co_write=True), KEY2, "k2")
    assert _status(token, settings(trust=trust), JwksRegistry((trust[0].key_source,), fetch=fetch)) == 503
    assert plain not in fetch.urls
    opted = (entry(name="app-b", issuer=ISS_B, jwks_url="", allow_insecure_http=True),)
    keys = JwksRegistry((opted[0].key_source,), fetch=fetch)
    assert verify_bearer(token, settings(trust=opted), keys).write is True


def test_plain_http_jwks_url_is_refused_at_fetch_time() -> None:
    fetch = Fetcher({"http://idp.example.com/certs": {"keys": [JWK]}})
    keys = JwksCache(KeySource(jwks_url="http://idp.example.com/certs"), fetch=fetch)
    assert _status(sign(base_claims()), settings(), keys) == 503
    assert fetch.urls == []


def test_url_allowed_rules() -> None:
    assert url_allowed("https://idp.example.com/certs")
    assert url_allowed("http://127.0.0.1:8080/certs")
    assert url_allowed("http://[::1]/certs")
    assert url_allowed("http://localhost/certs")
    assert not url_allowed("http://idp.example.com/certs")
    assert url_allowed("http://idp.example.com/certs", allow_insecure_http=True)
    assert not url_allowed("ftp://idp.example.com/certs", allow_insecure_http=True)
    assert not url_allowed("https:///certs")


@pytest.mark.parametrize(
    ("source", "target"),
    (
        ("https://idp.example.com/certs", "http://idp.example.com/certs"),
        ("https://idp.example.com/certs", "ftp://idp.example.com/certs"),
        ("http://127.0.0.1/certs", "http://127.0.0.1/other"),
    ),
)
def test_redirects_never_leave_https(source: str, target: str) -> None:
    request = urllib.request.Request(source)
    with pytest.raises(urllib.error.HTTPError):
        _HttpsRedirectOnly().redirect_request(request, None, 302, "Found", {}, target)


def test_https_to_https_redirect_is_followed() -> None:
    request = urllib.request.Request("https://idp.example.com/certs")
    followed = _HttpsRedirectOnly().redirect_request(request, None, 302, "Found", {}, "https://keys.example.com/certs")
    assert followed.full_url == "https://keys.example.com/certs"


def test_es256_when_the_entry_allows_it() -> None:
    trust = (entry(algorithms=("ES256",)),)
    keys = JwksCache(JWKS_URL, fetch=lambda _url: {"keys": [public_jwk(EC_KEY, "e1", "ES256"), JWK]})
    config = settings(trust=trust)
    caller = verify_bearer(sign(base_claims(u8co_read=True), EC_KEY, "e1", "ES256"), config, keys)
    assert caller.read is True
    assert _status(sign(base_claims(u8co_read=True)), config, keys) == 401


def test_algorithm_must_match_the_key() -> None:
    config = settings(trust=(entry(algorithms=("RS256", "PS256", "ES256")),))
    keys = JwksCache(JWKS_URL, fetch=lambda _url: {"keys": [JWK]})
    assert _status(sign(base_claims(), EC_KEY, "k1", "ES256"), config, keys) == 401
    assert _status(sign(base_claims(), KEY, "k1", "PS256"), config, keys) == 401
    loose = JwksCache(JWKS_URL, fetch=lambda _url: {"keys": [public_jwk(KEY, "k1", None)]})
    assert verify_bearer(sign(base_claims(), KEY, "k1", "PS256"), config, loose).write is False


def test_symmetric_and_unknown_tokens_are_401() -> None:
    config = settings()
    keys = JwksCache(JWKS_URL, fetch=lambda _url: {"keys": [JWK]})
    hs = jwt.encode(base_claims(u8co_write=True), "x" * 32, algorithm="HS256", headers={"kid": "k1"})
    assert _status(hs, config, keys) == 401
    assert _status(sign(base_claims(), KEY2, "k9"), config, keys) == 401
    assert _status(sign(base_claims(aud="other")), config, keys) == 401
    assert _status(sign(base_claims(iss="https://idp.example.com/other")), config, keys) == 401
    assert _status("not-a-token", config, keys) == 401
    assert _status(sign(base_claims()), settings(trust=()), keys) == 401


def test_audience_list_and_leeway() -> None:
    keys = JwksCache(JWKS_URL, fetch=lambda _url: {"keys": [JWK]})
    listed = sign(base_claims(aud=["other", AUD], u8co_write=True))
    assert verify_bearer(listed, settings(), keys).write is True
    late = sign(base_claims(exp=int(time.time()) - 30))
    assert verify_bearer(late, settings(), keys).client_id == "tool-a"
    assert _status(late, settings(jwt_leeway=0), keys) == 401


def test_scopes_and_compat_claims() -> None:
    keys = JwksCache(JWKS_URL, fetch=lambda _url: {"keys": [JWK]})
    scoped = settings(trust=(entry(write_scope="u8co.write", read_scope="u8co.read"),))
    by_scope = verify_bearer(sign(base_claims(scope="openid u8co.write")), scoped, keys)
    assert (by_scope.write, by_scope.read) == (True, False)
    by_scp = verify_bearer(sign(base_claims(scp=["u8co.read"])), scoped, keys)
    assert (by_scp.write, by_scp.read) == (False, True)
    unscoped = verify_bearer(sign(base_claims(scope="u8co.write")), settings(), keys)
    assert unscoped.write is False
    compat = settings(trust=(entry(write_claim="app_write", read_claim="app_read"),))
    legacy = verify_bearer(sign(base_claims(app_write=True, u8co_write=True)), compat, keys)
    assert (legacy.write, legacy.read) == (True, False)
    text = verify_bearer(sign(base_claims(u8co_write="true")), settings(), keys)
    assert text.write is False


def test_accounts_claim_parsing() -> None:
    keys = JwksCache(JWKS_URL, fetch=lambda _url: {"keys": [JWK]})
    config = settings(trust=(entry(accounts_claim="u8co_accounts"),))
    listed = verify_bearer(sign(base_claims(u8co_accounts=["803", "9x1", 902])), config, keys)
    assert listed.accounts == frozenset({"803"})
    text = verify_bearer(sign(base_claims(u8co_accounts="803,902 903")), config, keys)
    assert text.accounts == frozenset({"803", "902", "903"})
    missing = verify_bearer(sign(base_claims()), config, keys)
    assert missing.accounts == frozenset()


def test_jwks_index_skips_symmetric_and_encryption_keys() -> None:
    found = index_jwks(
        {
            "keys": [
                {"kty": "oct", "kid": "s1", "k": "c2VjcmV0"},
                dict(JWK, kid="enc", use="enc"),
                {"kty": "RSA", "kid": "broken", "n": "!!", "e": "AQAB"},
                JWK,
            ]
        }
    )
    assert set(found) == {"k1"}
