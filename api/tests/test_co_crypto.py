"""CO bridge key derivation, password encryption, and request MAC. Test vectors only."""

from __future__ import annotations

import hashlib

import pytest
from u8co_api.co_crypto import (
    SignedRequest,
    auth_headers,
    body_bytes,
    derive_keys,
    encrypt_password,
    fresh_signed,
    sign,
)

SECRET = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
K_MAC = "a85c2c38ccf25d1c837ec12b0a3707ee6aa584ed7834f8eac3302f81098b6407"
K_ENC = "3d9103ebd3f1448ce0dab668a4ce27c51a2ff0ac0b10c90cc5c300520e3411bc"
IV = bytes.fromhex("0f0e0d0c0b0a09080706050403020100")
PASSWORD = "测试Pass#01"
PASSWORD_ENC = "Dw4NDAsKCQgHBgUEAwIBAK86wvyLgq4AmzNMoENmeZU="
BODY = (
    '{"acc":"998","year":"2026","operator":"op001","password_enc":"'
    + PASSWORD_ENC
    + '","date":"2026-09-26","id":1000000003,"action":"verify"}'
)
BODY_SHA = "d1e19ce73e0391436e1e3eb76870e4237a113da406669a9f7e835abb920c2c58"
TS = "1790434800"
NONCE = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
SIG = "7ca396c444e8bd5f5ad0163ad12bb365688ce638d7bb4bb46da0b278e666061f"
SALE_PATH = "/u8co/v1/sale-orders/verify"


def _vector_body() -> dict[str, object]:
    return {
        "acc": "998",
        "year": "2026",
        "operator": "op001",
        "password_enc": PASSWORD_ENC,
        "date": "2026-09-26",
        "id": 1000000003,
        "action": "verify",
    }


def test_derive_keys() -> None:
    k_mac, k_enc = derive_keys(SECRET)
    assert k_mac.hex() == K_MAC
    assert k_enc.hex() == K_ENC


def test_secret_must_be_lowercase_hex() -> None:
    with pytest.raises(ValueError) as caught:
        derive_keys(SECRET.upper())
    assert SECRET.upper() not in str(caught.value)


def test_encrypt_password() -> None:
    assert encrypt_password(bytes.fromhex(K_ENC), PASSWORD, IV) == PASSWORD_ENC


def test_random_iv_changes_ciphertext() -> None:
    k_enc = bytes.fromhex(K_ENC)
    assert encrypt_password(k_enc, PASSWORD) != encrypt_password(k_enc, PASSWORD)


def test_body_and_signature() -> None:
    raw = body_bytes(_vector_body())
    assert raw.decode("ascii") == BODY
    assert hashlib.sha256(raw).hexdigest() == BODY_SHA
    req = SignedRequest("POST", SALE_PATH, raw, TS, NONCE)
    assert sign(bytes.fromhex(K_MAC), req) == SIG


def test_auth_headers() -> None:
    raw = BODY.encode("ascii")
    req = SignedRequest("POST", SALE_PATH, raw, TS, NONCE)
    headers = auth_headers(bytes.fromhex(K_MAC), req)
    assert headers["X-U8co-Ts"] == TS
    assert headers["X-U8co-Nonce"] == NONCE
    assert headers["X-U8co-Sig"] == SIG


def test_auth_headers_fill_ts_and_nonce() -> None:
    k_mac = bytes.fromhex(K_MAC)
    req = fresh_signed("POST", SALE_PATH, b"{}")
    headers = auth_headers(k_mac, req)
    assert headers["X-U8co-Ts"] == req.ts
    assert headers["X-U8co-Ts"].isdigit()
    assert len(req.nonce) == 32
    assert headers["X-U8co-Sig"] == sign(k_mac, req)


def test_body_keeps_unicode() -> None:
    assert body_bytes({"name": "演示"}) == '{"name":"演示"}'.encode()
