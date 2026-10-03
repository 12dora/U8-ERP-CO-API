"""U8 CO bridge MAC and password encryption. Byte-compatible with co.client."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import dataclass

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

_MAC_LABEL = b"u8co/v1/mac"
_ENC_LABEL = b"u8co/v1/enc"
_SECRET = re.compile(r"^[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class SignedRequest:
    """一次签名。字段收在这里，避免 sign 和 auth_headers 超过 5 个参数。"""

    method: str
    path: str
    body: bytes
    ts: str
    nonce: str


def derive_keys(secret_hex: str) -> tuple[bytes, bytes]:
    # 只接受小写，避免同一份密钥因大小写不同派生出两套结果。
    if _SECRET.fullmatch(secret_hex) is None:
        raise ValueError("共享密钥必须是 64 位小写十六进制")
    secret = bytes.fromhex(secret_hex)
    k_mac = hmac.new(secret, _MAC_LABEL, hashlib.sha256).digest()
    k_enc = hmac.new(secret, _ENC_LABEL, hashlib.sha256).digest()
    return k_mac, k_enc


def encrypt_password(k_enc: bytes, password: str, iv: bytes | None = None) -> str:
    if len(k_enc) != 32:
        raise ValueError("加密密钥必须是 32 字节")
    if iv is None:
        iv = os.urandom(16)
    if len(iv) != 16:
        raise ValueError("IV 必须是 16 字节")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(password.encode("utf-8")) + padder.finalize()
    encryptor = Cipher(algorithms.AES(k_enc), modes.CBC(iv)).encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(iv + ciphertext).decode("ascii")


def body_bytes(payload: dict) -> bytes:
    # 键顺序按调用方字典。桥按原始字节验签名，分隔符不能带空格。
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def fresh_signed(method: str, path: str, body: bytes) -> SignedRequest:
    ts = str(int(time.time()))
    nonce = os.urandom(16).hex()
    return SignedRequest(method, path, body, ts, nonce)


def canonical(req: SignedRequest) -> bytes:
    digest = hashlib.sha256(req.body).hexdigest()
    text = "\n".join((req.method, req.path, req.ts, req.nonce, digest))
    return text.encode("ascii")


def sign(k_mac: bytes, req: SignedRequest) -> str:
    return hmac.new(k_mac, canonical(req), hashlib.sha256).hexdigest()


def auth_headers(k_mac: bytes, req: SignedRequest) -> dict[str, str]:
    return {
        "X-U8co-Ts": req.ts,
        "X-U8co-Nonce": req.nonce,
        "X-U8co-Sig": sign(k_mac, req),
    }
