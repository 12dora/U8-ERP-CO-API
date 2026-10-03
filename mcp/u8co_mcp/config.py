"""配置文件和本地密钥。

配置文件：环境变量 U8CO_MCP_CONFIG，缺省 ~/.config/u8co/mcp.json。配置文件本身不放密钥，只写密钥文件的路径。
密钥文件（客户端密钥、令牌文件、U8 口令文件）必须是普通文件，权限不能给同组和其他用户（chmod 600）。
U8 口令也可以放在环境变量 U8CO_MCP_PASSWORD 里，优先于 u8.password_file。
经营管理查询（mgmt，可选）：stdio 方式另配各账套的只读登录，每个账套的口令放在自己的口令文件里；
incoming 方式不能配共用登录，改由 person_proxy（身份绑定服务）按调用者本人绑定的 U8 操作员查询。
HTTP 方式（http，可选）：多人共用的托管服务，令牌只能是 incoming（取自每个 HTTP 请求的 Bearer）。
incoming 方式只提供指南和经营管理工具，u8 段可以省略；allow_insecure_http 也只能和 incoming 一起用。
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

CONFIG_ENV = "U8CO_MCP_CONFIG"
PASSWORD_ENV = "U8CO_MCP_PASSWORD"
CONFIG_DEFAULT = "~/.config/u8co/mcp.json"

_TOP_KEYS = {
    "base_url",
    "token",
    "u8",
    "read_only",
    "timeout_s",
    "long_timeout_s",
    "ca_file",
    "mgmt",
    "http",
    "allow_insecure_http",
}
_TOP_REQUIRED = {"base_url", "token"}
_U8_KEYS = {"acc", "year", "operator", "password_file"}
_MGMT_KEYS = {"enabled", "claim", "scope", "accounts", "accounts_claim", "person_proxy"}
_MGMT_ACC_KEYS = {"acc", "operator", "password_file"}
_PROXY_KEYS = {"url", "token_file", "timeout_s"}
MGMT_CLAIM_DEFAULT = "u8co_mgmt"
MGMT_ACCOUNTS_CLAIM_DEFAULT = "u8co_accs"
MGMT_MAX_ACCOUNTS = 12
_HTTP_KEYS = {"enabled", "host", "port", "path", "user_header", "allowed_origins"}
USER_HEADER_DEFAULT = "X-U8co-User"
# 令牌类型 → (允许的键, 必填的键)
_TOKEN_KEYS = {
    "client_credentials": (
        {"type", "token_url", "client_id", "client_secret_file", "scope"},
        {"token_url", "client_id", "client_secret_file"},
    ),
    "file": ({"type", "path"}, {"path"}),
    "env": ({"type", "name"}, {"name"}),
    # incoming：只用于 HTTP 方式，令牌取自每个请求的 Authorization: Bearer，本服务不持有任何客户端密钥。
    "incoming": ({"type"}, set()),
}
_ACC = re.compile(r"^\d{3}\Z")
_YEAR = re.compile(r"^\d{4}\Z")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\Z")
# 与 API 信任配置的声明名规则一致。
_CLAIM = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:/-]{0,127}\Z")
_HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}\Z")
_HTTP_PATH = re.compile(r"^/[A-Za-z0-9._~/-]{0,127}\Z")
_ORIGIN = re.compile(r"^https?://[A-Za-z0-9.-]+(?::\d{1,5})?\Z")


class ConfigError(Exception):
    """配置或本地密钥不可用。消息里只有路径和键名，不含密钥内容。"""


@dataclass(frozen=True)
class TokenConfig:
    type: str
    token_url: str = ""
    client_id: str = ""
    client_secret_file: Path | None = None
    scope: str = ""
    path: Path | None = None
    name: str = ""


@dataclass(frozen=True)
class U8Config:
    acc: str
    operator: str
    year: str = ""
    password_file: Path | None = None


@dataclass(frozen=True)
class MgmtAccount:
    acc: str
    # incoming 方式（经身份绑定服务）下为空：不在本服务保存共用的 U8 登录。
    operator: str = ""
    password_file: Path | None = None


@dataclass(frozen=True)
class PersonProxyConfig:
    """身份绑定服务：按调用者令牌找到其本人绑定的 U8 操作员，以该操作员调 API 的经营管理路由。"""

    url: str
    token_file: Path
    timeout_s: float = 90.0


@dataclass(frozen=True)
class MgmtConfig:
    """经营管理查询：enabled 且令牌带 claim 声明（值为 true）时才列出 u8_mgmt_* 工具；API 仍会自己校验。"""

    accounts: tuple[MgmtAccount, ...]
    enabled: bool = True
    claim: str = MGMT_CLAIM_DEFAULT
    # 可选：令牌的 scope 含这个值时也列出（对应 API 信任项的 mgmt_scope）；空串表示只看 claim。
    scope: str = ""
    # 令牌的账套声明名（对应 API 信任项的 accounts_claim）：没给 accounts 时只取声明里的账套；空串表示不看。
    accounts_claim: str = MGMT_ACCOUNTS_CLAIM_DEFAULT
    # incoming 方式必填、其余方式不能配：经营管理调用一律经它以调用者本人的 U8 操作员执行。
    person_proxy: PersonProxyConfig | None = None

    def account(self, acc: str) -> MgmtAccount | None:
        return next((item for item in self.accounts if item.acc == acc), None)


@dataclass(frozen=True)
class HttpConfig:
    """HTTP 方式（MCP Streamable HTTP）。enabled 或命令行 --http 时启用；--host / --port 覆盖这里的值。"""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8095
    path: str = "/mcp"
    # 终端用户标识头（令牌 sub 为 UUID 时带上，只用于 API 审计），与 API 的 U8CO_USER_HEADER 一致。
    user_header: str = USER_HEADER_DEFAULT
    # 允许的浏览器来源。请求带 Origin 头而不在这里时拒绝（防 DNS 重绑定）；服务端调用通常不带 Origin。
    allowed_origins: tuple[str, ...] = ()


@dataclass(frozen=True)
class Config:
    base_url: str
    token: TokenConfig
    # incoming 方式可以没有：不提供用 u8 段登录的通用工具。
    u8: U8Config | None
    read_only: bool = False
    timeout_s: float = 90.0
    # 长时操作（存货核算记账、期末处理，经过存货核算的月末结账）的超时，见 http.is_long_call。
    long_timeout_s: float = 1000.0
    ca_file: Path | None = None
    mgmt: MgmtConfig | None = None
    http: HttpConfig | None = None


def config_path() -> Path:
    return Path(os.environ.get(CONFIG_ENV) or CONFIG_DEFAULT).expanduser()


def load_config(path: Path | None = None) -> Config:
    target = path or config_path()
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"找不到配置文件 {target}（可用环境变量 {CONFIG_ENV} 指定）") from None
    except (OSError, UnicodeDecodeError):
        raise ConfigError(f"读不了配置文件 {target}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise ConfigError(f"配置文件 {target} 不是合法的 JSON") from None
    return parse_config(data)


def parse_config(data: object) -> Config:
    _check_keys(data, "配置", _TOP_KEYS, _TOP_REQUIRED)
    assert isinstance(data, dict)
    read_only = data.get("read_only", False)
    if not isinstance(read_only, bool):
        raise ConfigError("read_only 必须是 true 或 false")
    timeout_s = _timeout(data.get("timeout_s", 90))
    token = _parse_token(data["token"])
    insecure = _insecure(data, token)
    mgmt = None
    if data.get("mgmt") is not None:
        mgmt = _parse_mgmt(data["mgmt"], token.type == "incoming", (insecure, timeout_s))
    return Config(
        base_url=_url(data["base_url"], "base_url", insecure=insecure),
        token=token,
        u8=_parse_u8_section(data, token),
        read_only=read_only,
        timeout_s=timeout_s,
        long_timeout_s=_long_timeout(data.get("long_timeout_s"), timeout_s),
        ca_file=_opt_path(data.get("ca_file"), "ca_file"),
        mgmt=mgmt,
        http=_parse_http(data["http"]) if data.get("http") is not None else None,
    )


def _insecure(data: dict, token: TokenConfig) -> bool:
    insecure = data.get("allow_insecure_http", False)
    if not isinstance(insecure, bool):
        raise ConfigError("allow_insecure_http 必须是 true 或 false")
    if insecure and token.type != "incoming":
        # 只有托管的 HTTP 方式（与 API 同在一个容器网络）才需要；本机的机器令牌不能走明文 http。
        raise ConfigError("allow_insecure_http 只能在 token.type 为 incoming（HTTP 方式）时使用")
    return insecure


def _parse_u8_section(data: dict, token: TokenConfig) -> U8Config | None:
    if token.type == "incoming":
        # 托管方式任何地方都不能有共用的 U8 口令（通用工具本来就不提供，口令也不许配）。
        if isinstance(data.get("u8"), dict) and "password_file" in data["u8"]:
            raise ConfigError("token.type 为 incoming 时不能配置 u8.password_file：托管方式不保存共用的 U8 口令")
        if os.environ.get(PASSWORD_ENV):
            raise ConfigError(f"token.type 为 incoming 时不能设置环境变量 {PASSWORD_ENV}：托管方式不保存共用的 U8 口令")
    if data.get("u8") is not None:
        return _parse_u8(data["u8"])
    if token.type == "incoming":
        return None
    raise ConfigError("配置 缺少 u8")


def _check_keys(obj: object, where: str, allowed: set[str], required: set[str]) -> None:
    if not isinstance(obj, dict):
        raise ConfigError(f"{where} 必须是 JSON 对象")
    unknown = sorted(set(obj) - allowed)
    if unknown:
        raise ConfigError(f"{where} 有未知的键：{', '.join(unknown)}")
    missing = sorted(required - set(obj))
    if missing:
        raise ConfigError(f"{where} 缺少 {', '.join(missing)}")


def _text(value: object, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where} 必须是非空字符串")
    return value.strip()


def _opt_path(value: object, where: str) -> Path | None:
    if value is None:
        return None
    return Path(_text(value, where)).expanduser()


def _timeout(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 600:
        raise ConfigError("timeout_s 必须是 1 到 600 之间的数字（秒）")
    return float(value)


def _long_timeout(value: object, timeout_s: float) -> float:
    # 桥对长时操作最多等 iaCommandSeconds（上限 7200）+ 60 秒，所以上限放到 7300。不写时取 1000 与 timeout_s 中较大的。
    if value is None:
        return max(1000.0, timeout_s)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not timeout_s <= value <= 7300:
        raise ConfigError("long_timeout_s 必须是不小于 timeout_s、不大于 7300 的数字（秒）")
    return float(value)


def _url(value: object, where: str, keep_slash: bool = False, insecure: bool = False) -> str:
    text = _text(value, where)
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ConfigError(f"{where} 必须是 http(s):// 开头的地址")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ConfigError(f"{where} 不能带用户名、口令、查询串或片段")
    if parts.scheme == "http" and not insecure and not _loopback(parts.hostname):
        raise ConfigError(f"{where} 必须用 https（只有本机回环地址可以用 http，或显式设置 allow_insecure_http）")
    # 令牌端点照原样用（有的身份服务的令牌地址以 / 结尾，去掉会 404）；base_url 去掉结尾的 /。
    return text if keep_slash else text.rstrip("/")


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _parse_token(obj: object) -> TokenConfig:
    kind = obj.get("type") if isinstance(obj, dict) else None
    if kind not in _TOKEN_KEYS:
        raise ConfigError("token.type 必须是 client_credentials、file、env 或 incoming")
    allowed, required = _TOKEN_KEYS[kind]
    _check_keys(obj, "token", allowed, required | {"type"})
    assert isinstance(obj, dict)
    if kind == "incoming":
        return TokenConfig(type=kind)
    if kind == "file":
        return TokenConfig(type=kind, path=_opt_path(obj["path"], "token.path"))
    if kind == "env":
        name = _text(obj["name"], "token.name")
        if not _ENV_NAME.match(name):
            raise ConfigError("token.name 必须是环境变量名")
        return TokenConfig(type=kind, name=name)
    scope = obj.get("scope", "")
    if not isinstance(scope, str):
        raise ConfigError("token.scope 必须是字符串")
    return TokenConfig(
        type=kind,
        token_url=_url(obj["token_url"], "token.token_url", keep_slash=True),
        client_id=_text(obj["client_id"], "token.client_id"),
        client_secret_file=_opt_path(obj["client_secret_file"], "token.client_secret_file"),
        scope=scope.strip(),
    )


def _parse_u8(obj: object) -> U8Config:
    _check_keys(obj, "u8", _U8_KEYS, {"acc", "operator"})
    assert isinstance(obj, dict)
    acc = _text(obj["acc"], "u8.acc")
    if not _ACC.match(acc):
        raise ConfigError("u8.acc 必须是三位数字账套号")
    year = obj.get("year")
    if isinstance(year, int) and not isinstance(year, bool):
        year = str(year)
    if year is not None and (not isinstance(year, str) or not _YEAR.match(year)):
        raise ConfigError("u8.year 必须是四位年度")
    return U8Config(
        acc=acc,
        operator=_text(obj["operator"], "u8.operator"),
        year=year or "",
        password_file=_opt_path(obj.get("password_file"), "u8.password_file"),
    )


def _parse_mgmt(obj: object, incoming: bool, proxy_opts: tuple[bool, float]) -> MgmtConfig:
    """proxy_opts：(allow_insecure_http, 缺省超时秒数)。"""
    _check_keys(obj, "mgmt", _MGMT_KEYS, {"accounts"})
    assert isinstance(obj, dict)
    enabled = obj.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError("mgmt.enabled 必须是 true 或 false")
    claim = _text(obj.get("claim", MGMT_CLAIM_DEFAULT), "mgmt.claim")
    if not _CLAIM.match(claim):
        raise ConfigError("mgmt.claim 必须是令牌声明名（字母、数字、_ . : / -）")
    scope = "" if obj.get("scope") is None else _text(obj["scope"], "mgmt.scope")
    if scope and not _CLAIM.match(scope):
        raise ConfigError("mgmt.scope 必须是一个 scope 值（字母、数字、_ . : / -，不含空格）")
    accounts_claim = obj.get("accounts_claim", MGMT_ACCOUNTS_CLAIM_DEFAULT)
    if not isinstance(accounts_claim, str) or (accounts_claim and not _CLAIM.match(accounts_claim)):
        raise ConfigError("mgmt.accounts_claim 必须是令牌声明名（字母、数字、_ . : / -），或空串表示不看")
    items = obj["accounts"]
    if not isinstance(items, list) or not 1 <= len(items) <= MGMT_MAX_ACCOUNTS:
        raise ConfigError(f"mgmt.accounts 必须是 1 到 {MGMT_MAX_ACCOUNTS} 个账套登录的数组")
    accounts = tuple(_parse_mgmt_account(item, f"mgmt.accounts.{index}", incoming) for index, item in enumerate(items))
    accs = [item.acc for item in accounts]
    if len(set(accs)) != len(accs):
        raise ConfigError("mgmt.accounts 里的账套不能重复")
    proxy = _parse_person_proxy(obj.get("person_proxy"), incoming, proxy_opts)
    return MgmtConfig(
        accounts=accounts,
        enabled=enabled,
        claim=claim,
        scope=scope,
        accounts_claim=accounts_claim,
        person_proxy=proxy,
    )


def _parse_mgmt_account(obj: object, where: str, incoming: bool) -> MgmtAccount:
    if incoming:
        # 多人共用的服务不能持有共用的 U8 登录：否则每个有经营管理权限的人都以同一个操作员看全部数据。
        if isinstance(obj, dict) and ("password_file" in obj or "operator" in obj):
            raise ConfigError(
                f"{where} 不能配置 operator、password_file：incoming 方式按调用者本人绑定的 U8 操作员查询，"
                "改配 mgmt.person_proxy"
            )
        _check_keys(obj, where, {"acc"}, {"acc"})
    else:
        _check_keys(obj, where, _MGMT_ACC_KEYS, _MGMT_ACC_KEYS)
    assert isinstance(obj, dict)
    acc = _text(obj["acc"], f"{where}.acc")
    if not _ACC.match(acc):
        raise ConfigError(f"{where}.acc 必须是三位数字账套号")
    if incoming:
        return MgmtAccount(acc=acc)
    password_file = Path(_text(obj["password_file"], f"{where}.password_file")).expanduser()
    return MgmtAccount(acc=acc, operator=_text(obj["operator"], f"{where}.operator"), password_file=password_file)


def _parse_person_proxy(obj: object, incoming: bool, proxy_opts: tuple[bool, float]) -> PersonProxyConfig | None:
    if not incoming:
        if obj is not None:
            raise ConfigError("mgmt.person_proxy 只能在 token.type 为 incoming（HTTP 方式）时使用")
        return None
    if obj is None:
        raise ConfigError("token.type 为 incoming 时 mgmt 必须配置 person_proxy（身份绑定服务），不能用共用的 U8 登录")
    _check_keys(obj, "mgmt.person_proxy", _PROXY_KEYS, {"url", "token_file"})
    assert isinstance(obj, dict)
    insecure, default_timeout = proxy_opts
    timeout = obj.get("timeout_s", default_timeout)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 600:
        raise ConfigError("mgmt.person_proxy.timeout_s 必须是 1 到 600 之间的数字（秒）")
    return PersonProxyConfig(
        url=_url(obj["url"], "mgmt.person_proxy.url", keep_slash=True, insecure=insecure),
        token_file=Path(_text(obj["token_file"], "mgmt.person_proxy.token_file")).expanduser(),
        timeout_s=float(timeout),
    )


def _parse_http(obj: object) -> HttpConfig:
    _check_keys(obj, "http", _HTTP_KEYS, set())
    assert isinstance(obj, dict)
    enabled = obj.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError("http.enabled 必须是 true 或 false")
    port = obj.get("port", 8095)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ConfigError("http.port 必须是 1 到 65535 的整数")
    path = _text(obj.get("path", "/mcp"), "http.path")
    if not _HTTP_PATH.match(path):
        raise ConfigError("http.path 必须以 / 开头，只含字母、数字和 ._~/-")
    header = _text(obj.get("user_header", USER_HEADER_DEFAULT), "http.user_header")
    if not _HEADER.match(header):
        raise ConfigError("http.user_header 必须是 HTTP 头名（字母开头，只含字母、数字和 -）")
    origins = obj.get("allowed_origins", [])
    if not isinstance(origins, list) or not all(isinstance(o, str) and _ORIGIN.match(o) for o in origins):
        raise ConfigError("http.allowed_origins 必须是来源（如 https://chat.example.com）的数组")
    return HttpConfig(
        enabled=enabled,
        host=_text(obj.get("host", "127.0.0.1"), "http.host"),
        port=port,
        path=path,
        user_header=header,
        allowed_origins=tuple(origins),
    )


def read_secret_file(path: Path | None, what: str) -> str:
    """读一个本地密钥文件：必须是普通文件、权限 600，内容去掉首尾空白后不能为空。"""
    if path is None:
        raise ConfigError(f"没有配置{what}文件")
    try:
        mode = os.stat(path).st_mode
    except OSError:
        raise ConfigError(f"找不到{what}文件 {path}") from None
    if not stat.S_ISREG(mode):
        raise ConfigError(f"{what}文件 {path} 不是普通文件")
    if os.name != "nt" and mode & 0o077:
        raise ConfigError(f"{what}文件 {path} 权限过宽，请 chmod 600")
    try:
        text = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        raise ConfigError(f"读不了{what}文件 {path}") from None
    if not text:
        raise ConfigError(f"{what}文件 {path} 是空的")
    return text


def read_password(u8: U8Config) -> str:
    value = os.environ.get(PASSWORD_ENV, "")
    if value:
        return value
    if u8.password_file is None:
        raise ConfigError(f"没有 U8 口令：设置环境变量 {PASSWORD_ENV} 或配置 u8.password_file")
    return read_secret_file(u8.password_file, " U8 口令")
