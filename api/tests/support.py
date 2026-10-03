"""测试共用：本地生成的签名密钥、信任项、设置和测试客户端。不访问任何网络。"""

from __future__ import annotations

import json
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from fastapi.testclient import TestClient
from jwt.algorithms import ECAlgorithm, RSAAlgorithm
from u8co_api.co_access import is_read
from u8co_api.config import Settings
from u8co_api.jwks import JwksCache
from u8co_api.main import AppDeps, create_app
from u8co_api.trust import TrustEntry

ISS = "https://idp.example.com"
AUD = "u8co-api"
JWKS_URL = "https://idp.example.com/oauth/jwks"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
KEY2 = rsa.generate_private_key(public_exponent=65537, key_size=2048)
EC_KEY = ec.generate_private_key(ec.SECP256R1())


def public_jwk(key, kid: str, alg: str | None = "RS256") -> dict:
    reader = ECAlgorithm if isinstance(key, ec.EllipticCurvePrivateKey) else RSAAlgorithm
    data = json.loads(reader.to_jwk(key.public_key()))
    data["kid"] = kid
    data["use"] = "sig"
    if alg is not None:
        data["alg"] = alg
    return data


JWK = public_jwk(KEY, "k1")
JWK2 = public_jwk(KEY2, "k2")


def sign(claims: dict, key=KEY, kid: str = "k1", alg: str = "RS256") -> str:
    token = jwt.encode(claims, key, algorithm=alg, headers={"kid": kid})
    return token if isinstance(token, str) else token.decode("ascii")


def base_claims(**extra) -> dict:
    payload = {"iss": ISS, "aud": AUD, "exp": int(time.time()) + 600, "azp": "tool-a"}
    payload.update(extra)
    return payload


def entry(**kwargs) -> TrustEntry:
    data = {"name": "tool", "issuer": ISS, "audience": AUD, "jwks_url": JWKS_URL}
    data.update(kwargs)
    return TrustEntry(**data)


def settings(**kwargs) -> Settings:
    data = {"trust": (entry(),)}
    data.update(kwargs)
    return Settings(**data)


def static_jwks(*keys: dict) -> JwksCache:
    found = list(keys) or [JWK]
    return JwksCache(JWKS_URL, fetch=lambda _url: {"keys": found})


def make_client(claims: dict | None = None, auth: bool = True, **kw) -> TestClient:
    deps = AppDeps(
        settings=settings(**kw),
        jwks=static_jwks(),
        co_bridge=None,
    )
    client = TestClient(create_app(deps))
    token = sign(claims or base_claims())
    if auth:
        client.headers["Authorization"] = f"Bearer {token}"
    client.token = token
    return client


def audit_line(out: str, endpoint: str) -> dict:
    for line in out.splitlines():
        if not line.startswith("{"):
            continue
        payload = json.loads(line)
        if payload.get("endpoint") == endpoint and payload.get("status") == 200:
            return payload
    raise AssertionError("missing audit line")


def write_keys(api_path: str, keys: set[str]) -> set[str]:
    """转给桥的键集合：写路由一律带 caller（桥的审计行记调用方），读路由不带。"""
    if is_read("co:" + api_path.removeprefix("/v1/co/")):
        return set(keys)
    return set(keys) | {"caller"}
