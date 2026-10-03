"""信任配置：接受哪些发行者签发的令牌，以及从令牌的哪个声明读权限。

来源有两个，合并使用：
- U8CO_TRUST_FILE 指向的 JSON 数组，每项一个调用方（缺省路径 /config/u8co-trust.json，文件不存在时跳过；
  显式设置了但文件不存在则启动失败）。
- U8CO_OIDC_* 环境变量描述的单个发行者，适合只有一个身份提供方的部署。
按 (issuer, audience) 匹配令牌，同一对只能出现一次。
jwks_url 必须是 https；http 只允许本机回环地址，或该项显式写 allow_insecure_http: true（缺省 false）。
经营管理查询（/v1/co/mgmt/*）另用 mgmt_claim / mgmt_scope：两者都没配的信任项不能调这些路由，写权限也不代替它。
可选的 accounts（账套号列表）是该信任项的静态账套上限：实际可用账套 = U8CO_ACCOUNTS ∩ 令牌账套声明 ∩ accounts。
不写 accounts 不加限制（与旧版一致）。
可选的 perm_evaluate（布尔，缺省 false）：只有为 true 的信任项能调 /v1/co/perm/evaluate（查别的操作员的权限）。
可选的 on_behalf_header（布尔，缺省 false）：为 true 的机器调用方代人调用时，审计的终端用户取 U8CO_USER_HEADER
头（同时记令牌 sub）；为 false 时令牌有 sub 就以 sub 为终端用户，头被忽略。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass

from u8co_api.jwks import KeySource, url_allowed

DEFAULT_TRUST_FILE = "/config/u8co-trust.json"
DEFAULT_WRITE_CLAIM = "u8co_write"
DEFAULT_READ_CLAIM = "u8co_read"
ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512")

_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,62}\Z")
_CLAIM = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}\Z")
_SCOPE = re.compile(r"[\x21-\x7e]{1,128}\Z")
_KEYS = frozenset(
    {
        "name",
        "caller",
        "issuer",
        "audience",
        "jwks_url",
        "algorithms",
        "write_claim",
        "read_claim",
        "write_scope",
        "read_scope",
        "accounts_claim",
        "allow_insecure_http",
        "mgmt_claim",
        "mgmt_scope",
        "accounts",
        "perm_evaluate",
        "on_behalf_header",
    }
)
_ENV = {
    "name": "U8CO_OIDC_NAME",
    "issuer": "U8CO_OIDC_ISSUER",
    "audience": "U8CO_OIDC_AUDIENCE",
    "jwks_url": "U8CO_OIDC_JWKS_URL",
    "algorithms": "U8CO_OIDC_ALGORITHMS",
    "write_claim": "U8CO_OIDC_WRITE_CLAIM",
    "read_claim": "U8CO_OIDC_READ_CLAIM",
    "write_scope": "U8CO_OIDC_WRITE_SCOPE",
    "read_scope": "U8CO_OIDC_READ_SCOPE",
    "accounts_claim": "U8CO_OIDC_ACCOUNTS_CLAIM",
    "allow_insecure_http": "U8CO_OIDC_ALLOW_INSECURE_HTTP",
    "mgmt_claim": "U8CO_OIDC_MGMT_CLAIM",
    "mgmt_scope": "U8CO_OIDC_MGMT_SCOPE",
}
_TRUE = frozenset({"1", "true", "yes", "on"})
_ACCOUNT = re.compile(r"[0-9]{3}\Z")


@dataclass(frozen=True)
class TrustEntry:
    name: str
    issuer: str
    audience: str
    jwks_url: str = ""
    algorithms: tuple[str, ...] = ("RS256",)
    write_claim: str = DEFAULT_WRITE_CLAIM
    read_claim: str = DEFAULT_READ_CLAIM
    write_scope: str = ""
    read_scope: str = ""
    accounts_claim: str = ""
    allow_insecure_http: bool = False
    # 经营管理查询的声明和 scope；都为空表示该发行者的令牌一律不能查经营管理数据。
    mgmt_claim: str = ""
    mgmt_scope: str = ""
    # 静态账套上限；None 表示不限（只受 U8CO_ACCOUNTS 和令牌账套声明约束）。
    accounts: tuple[str, ...] | None = None
    # 能否调 perm/evaluate；代人调用时是否信任终端用户头。都只能在信任配置里打开。
    perm_evaluate: bool = False
    on_behalf_header: bool = False

    @property
    def key_source(self) -> KeySource:
        if self.jwks_url:
            return KeySource(jwks_url=self.jwks_url, allow_insecure_http=self.allow_insecure_http)
        return KeySource(issuer=self.issuer, allow_insecure_http=self.allow_insecure_http)


def load_trust(env: Mapping[str, str]) -> tuple[TrustEntry, ...]:
    found = list(_from_file(env)) + list(_from_env(env))
    seen: set[tuple[str, str]] = set()
    for entry in found:
        pair = (entry.issuer, entry.audience)
        if pair in seen:
            raise SystemExit(f"信任配置的发行者与受众重复: {entry.name}")
        seen.add(pair)
    return tuple(found)


def _from_file(env: Mapping[str, str]) -> tuple[TrustEntry, ...]:
    explicit = env.get("U8CO_TRUST_FILE", "").strip()
    path = explicit or DEFAULT_TRUST_FILE
    if not os.path.isfile(path):
        if explicit:
            raise SystemExit(f"U8CO_TRUST_FILE 指向的文件不存在: {path}")
        return ()
    return load_trust_file(path)


def load_trust_file(path: str) -> tuple[TrustEntry, ...]:
    raw = _read_json(path)
    if not isinstance(raw, list):
        raise SystemExit(f"信任配置必须是 JSON 数组: {path}")
    return tuple(parse_entry(item) for item in raw)


def _from_env(env: Mapping[str, str]) -> tuple[TrustEntry, ...]:
    if not env.get("U8CO_OIDC_ISSUER", "").strip():
        return ()
    item: dict[str, object] = {"name": "default"}
    for key, name in _ENV.items():
        value = env.get(name, "").strip()
        if value:
            item[key] = value
    if isinstance(item.get("algorithms"), str):
        item["algorithms"] = [part.strip() for part in str(item["algorithms"]).split(",") if part.strip()]
    if "allow_insecure_http" in item:
        item["allow_insecure_http"] = str(item["allow_insecure_http"]).lower() in _TRUE
    return (parse_entry(item),)


def _read_json(path: str) -> object:
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"读不到信任配置 {path}: {exc}") from exc


def parse_entry(item: object) -> TrustEntry:
    if not isinstance(item, dict):
        raise SystemExit("信任配置的每一项必须是对象")
    unknown = sorted(set(item) - _KEYS)
    if unknown:
        raise SystemExit(f"信任配置有未知字段: {', '.join(unknown)}")
    name = _name(item)
    entry = TrustEntry(
        name=name,
        issuer=_text(item, "issuer", name),
        audience=_text(item, "audience", name),
        jwks_url=_optional(item, "jwks_url", name),
        algorithms=_algorithms(item.get("algorithms"), name),
        write_claim=_optional(item, "write_claim", name) or DEFAULT_WRITE_CLAIM,
        read_claim=_optional(item, "read_claim", name) or DEFAULT_READ_CLAIM,
        write_scope=_optional(item, "write_scope", name),
        read_scope=_optional(item, "read_scope", name),
        accounts_claim=_optional(item, "accounts_claim", name),
        allow_insecure_http=_bool(item, "allow_insecure_http", name),
        mgmt_claim=_optional(item, "mgmt_claim", name),
        mgmt_scope=_optional(item, "mgmt_scope", name),
        accounts=_accounts(item.get("accounts"), name),
        perm_evaluate=_bool(item, "perm_evaluate", name),
        on_behalf_header=_bool(item, "on_behalf_header", name),
    )
    _check(entry)
    return entry


def _name(item: dict) -> str:
    # caller 是旧版调用方文件的字段名，照样接受。
    raw = item.get("name", item.get("caller"))
    if not isinstance(raw, str) or _NAME.fullmatch(raw.strip()) is None:
        raise SystemExit(f"信任配置的 name 无效: {raw!r}")
    return raw.strip()


def _text(item: dict, key: str, name: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"信任配置 {name} 缺少 {key}")
    return value.strip()


def _optional(item: dict, key: str, name: str) -> str:
    value = item.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SystemExit(f"信任配置 {name} 的 {key} 必须是字符串")
    return value.strip()


def _bool(item: dict, key: str, name: str) -> bool:
    value = item.get(key, False)
    if not isinstance(value, bool):
        raise SystemExit(f"信任配置 {name} 的 {key} 必须是 true 或 false")
    return value


def _accounts(raw: object, name: str) -> tuple[str, ...] | None:
    # 不写为不限；写了就必须是非空、不重复的三位数字账套号列表。
    if raw is None:
        return None
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"信任配置 {name} 的 accounts 必须是非空的账套号数组")
    for item in raw:
        if not isinstance(item, str) or _ACCOUNT.fullmatch(item) is None:
            raise SystemExit(f"信任配置 {name} 的 accounts 里的账套号必须是三位数字的字符串: {item!r}")
    if len(set(raw)) != len(raw):
        raise SystemExit(f"信任配置 {name} 的 accounts 有重复的账套号")
    return tuple(raw)


def _algorithms(raw: object, name: str) -> tuple[str, ...]:
    if raw is None:
        return ("RS256",)
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"信任配置 {name} 的 algorithms 必须是非空数组")
    found: list[str] = []
    for item in raw:
        if item not in ALGORITHMS:
            raise SystemExit(f"信任配置 {name} 的算法不被支持: {item!r}")
        if item not in found:
            found.append(item)
    return tuple(found)


def _check(entry: TrustEntry) -> None:
    if not entry.issuer.startswith("https://"):
        raise SystemExit(f"发行者必须是 https URL: {entry.name}")
    if entry.jwks_url and not url_allowed(entry.jwks_url, entry.allow_insecure_http):
        raise SystemExit(f"jwks_url 必须是 https URL（http 仅限本机回环或 allow_insecure_http）: {entry.name}")
    _check_claims(entry)


def _check_claims(entry: TrustEntry) -> None:
    for claim in (entry.write_claim, entry.read_claim, entry.accounts_claim):
        if claim and _CLAIM.fullmatch(claim) is None:
            raise SystemExit(f"信任配置 {entry.name} 的声明名无效: {claim!r}")
    if entry.write_claim == entry.read_claim:
        raise SystemExit(f"信任配置 {entry.name} 的读、写声明不能相同")
    for scope in (entry.write_scope, entry.read_scope):
        if scope and _SCOPE.fullmatch(scope) is None:
            raise SystemExit(f"信任配置 {entry.name} 的 scope 无效: {scope!r}")
    if entry.write_scope and entry.write_scope == entry.read_scope:
        raise SystemExit(f"信任配置 {entry.name} 的读、写 scope 不能相同")
    _check_mgmt(entry)


def _check_mgmt(entry: TrustEntry) -> None:
    # 经营管理的声明、scope 必须独立于读写，否则读或写令牌会顺带获得经营管理权限。
    if entry.mgmt_claim and _CLAIM.fullmatch(entry.mgmt_claim) is None:
        raise SystemExit(f"信任配置 {entry.name} 的声明名无效: {entry.mgmt_claim!r}")
    if entry.mgmt_claim and entry.mgmt_claim in (entry.write_claim, entry.read_claim, entry.accounts_claim):
        raise SystemExit(f"信任配置 {entry.name} 的 mgmt_claim 不能与读、写或账套声明相同")
    if entry.mgmt_scope and _SCOPE.fullmatch(entry.mgmt_scope) is None:
        raise SystemExit(f"信任配置 {entry.name} 的 scope 无效: {entry.mgmt_scope!r}")
    if entry.mgmt_scope and entry.mgmt_scope in (entry.write_scope, entry.read_scope):
        raise SystemExit(f"信任配置 {entry.name} 的 mgmt_scope 不能与读、写 scope 相同")
