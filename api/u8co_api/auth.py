"""离线校验 Bearer JWT。按 (iss, aud) 找到唯一的信任项，再用它的 JWKS 验签。

权限只认信任项里配置的声明（值必须是布尔 true，字符串 "true" 不算），或者 scope/scp 里配置的 scope。
配置了 accounts_claim 时，令牌只能操作该声明列出的账套，并且仍然受 U8CO_ACCOUNTS 限制。
信任项写了 accounts 时再与之取交集（静态上限，令牌声明放不宽它）。
经营管理权限（Caller.mgmt）同样只认信任项的 mgmt_claim / mgmt_scope；信任项没配时一律为 false。
Caller.subject 是令牌的 sub：幂等记录、频率名额、审计的终端用户都按「信任项:客户端:sub」区分，不只看 azp。
perm_evaluate、on_behalf_header 直接取自信任项（缺省 false），令牌里的声明不能打开它们。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import jwt

from u8co_api.config import Settings
from u8co_api.errors import unauthorized
from u8co_api.jwks import Jwk, JwksCache, JwksRegistry
from u8co_api.trust import TrustEntry

_ACCOUNT = re.compile(r"^[0-9]{3}\Z")
_SPLIT = re.compile(r"[\s,]+")


@dataclass(frozen=True)
class Caller:
    client_id: str
    name: str
    write: bool = False
    read: bool = False
    # None 表示令牌不限制账套（信任项没配 accounts_claim）。
    accounts: frozenset[str] | None = None
    # 经营管理查询（/v1/co/mgmt/*）。写权限不代替它。
    mgmt: bool = False
    # 令牌的 sub（去掉首尾空白，最多 200 字符）；没有时为空串。
    subject: str = ""
    # 信任项的 perm_evaluate：可以调 perm/evaluate（查别的操作员的权限）。
    perm_evaluate: bool = False
    # 信任项的 on_behalf_header：机器调用方代人调用时，审计的终端用户取 U8CO_USER_HEADER 头。
    on_behalf_header: bool = False


def bearer_token(header: str | None) -> str:
    if not header:
        raise unauthorized()
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise unauthorized()
    return token.strip()


def verify_bearer(token: str, settings: Settings, keys: JwksCache | JwksRegistry) -> Caller:
    spec = _spec(token, settings.trust)
    alg, kid = _header(token, spec)
    key = _signing(keys, spec, kid, alg)
    payload = _decode(token, key, spec, (alg, settings.jwt_leeway))
    return caller_from(payload, spec)


def _spec(token: str, trust: tuple[TrustEntry, ...]) -> TrustEntry:
    if not trust:
        raise unauthorized("尚未配置信任的发行者")
    issuer, audiences = _peek(token)
    found = [item for item in trust if item.issuer == issuer and item.audience in audiences]
    if len(found) != 1:
        raise unauthorized("访问令牌的发行者或受众不匹配")
    return found[0]


def _signing(keys: JwksCache | JwksRegistry, spec: TrustEntry, kid: str, alg: str):
    cache = keys if isinstance(keys, JwksCache) else keys.for_source(spec.key_source)
    found: Jwk = cache.signing_key(kid)
    # 密钥类型必须与算法一致；JWKS 声明了 alg 时也必须一致。
    if found.kty != ("EC" if alg.startswith("ES") else "RSA"):
        raise unauthorized("令牌算法与签名密钥不匹配")
    if found.alg is not None and found.alg != alg:
        raise unauthorized("令牌算法与签名密钥不匹配")
    return found.key


def _peek(token: str) -> tuple[str, frozenset[str]]:
    try:
        payload = jwt.decode(token, options=_PEEK)
    except jwt.PyJWTError as exc:
        raise unauthorized() from exc
    issuer = payload.get("iss")
    if not isinstance(issuer, str) or not issuer:
        raise unauthorized()
    return issuer, _audiences(payload.get("aud"))


def _audiences(raw: object) -> frozenset[str]:
    if isinstance(raw, str) and raw:
        return frozenset({raw})
    if isinstance(raw, list):
        return frozenset(item for item in raw if isinstance(item, str) and item)
    return frozenset()


def _header(token: str, spec: TrustEntry) -> tuple[str, str]:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise unauthorized() from exc
    alg = header.get("alg")
    kid = header.get("kid")
    if alg not in spec.algorithms or not isinstance(kid, str) or not kid:
        raise unauthorized("令牌算法或密钥标识不被接受")
    return alg, kid


_PEEK = {
    "verify_signature": False,
    "verify_aud": False,
    "verify_iss": False,
    "verify_exp": False,
    "verify_nbf": False,
    "verify_iat": False,
}


def _decode(token: str, key, spec: TrustEntry, rule: tuple[str, int]) -> dict:
    alg, leeway = rule
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=[alg],
            audience=spec.audience,
            issuer=spec.issuer,
            leeway=leeway,
            options={"require": ["exp", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise unauthorized() from exc
    if not isinstance(payload, dict):
        raise unauthorized()
    return payload


def caller_from(payload: dict, spec: TrustEntry) -> Caller:
    scopes = _scopes(payload)
    return Caller(
        client_id=_client(payload),
        name=spec.name,
        write=_granted(payload, spec.write_claim, spec.write_scope, scopes),
        read=_granted(payload, spec.read_claim, spec.read_scope, scopes),
        accounts=_allowed_accounts(payload, spec),
        mgmt=_mgmt(payload, spec, scopes),
        subject=_subject(payload),
        perm_evaluate=spec.perm_evaluate,
        on_behalf_header=spec.on_behalf_header,
    )


def caller_key(caller: Caller) -> str:
    """「信任项:客户端」，令牌有 sub 且与客户端不同时再加「:sub」。幂等记录、频率名额按它区分调用方：
    同一个 azp 下的两个用户互不可见、互不挤占。"""
    text = f"{caller.name}:{caller.client_id}"
    if caller.subject and caller.subject != caller.client_id:
        text += f":{caller.subject}"
    return text


def _mgmt(payload: dict, spec: TrustEntry, scopes: frozenset[str]) -> bool:
    if not spec.mgmt_claim and not spec.mgmt_scope:
        return False
    if spec.mgmt_claim and payload.get(spec.mgmt_claim) is True:
        return True
    return bool(spec.mgmt_scope) and spec.mgmt_scope in scopes


def _granted(payload: dict, claim: str, scope: str, scopes: frozenset[str]) -> bool:
    if payload.get(claim) is True:
        return True
    return bool(scope) and scope in scopes


def _scopes(payload: dict) -> frozenset[str]:
    found: set[str] = set()
    for key in ("scope", "scp"):
        raw = payload.get(key)
        if isinstance(raw, str):
            found.update(raw.split())
        elif isinstance(raw, list):
            found.update(item for item in raw if isinstance(item, str))
    return frozenset(found)


def _allowed_accounts(payload: dict, spec: TrustEntry) -> frozenset[str] | None:
    # 令牌账套声明（信任项配了 accounts_claim 时）∩ 信任项静态上限（写了 accounts 时）；两者都没配为 None（不限）。
    found = _accounts(payload.get(spec.accounts_claim)) if spec.accounts_claim else None
    if spec.accounts is None:
        return found
    cap = frozenset(spec.accounts)
    return cap if found is None else found & cap


def _accounts(raw: object) -> frozenset[str]:
    # 列表或逗号、空格分隔的字符串。不是三位数字的项忽略，只会收窄权限。
    if isinstance(raw, str):
        raw = _SPLIT.split(raw.strip())
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(item for item in raw if isinstance(item, str) and _ACCOUNT.fullmatch(item))


def _subject(payload: dict) -> str:
    value = payload.get("sub")
    if isinstance(value, str) and value.strip():
        return value.strip()[:200]
    return ""


def _client(payload: dict) -> str:
    for key in ("azp", "client_id", "sub"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:200]
    return "unknown"
