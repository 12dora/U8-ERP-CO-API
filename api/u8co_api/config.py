"""进程配置。全部来自环境变量；共享密钥只从文件读，不进镜像、不进命令行。

桥地址和密钥没有缺省值。缺任何一项时服务照常启动，/v1/co 返回 503 unavailable。
账套白名单缺省为空，此时每个业务调用都返回 403 account_not_allowed。
U8CO_READONLY_ACCOUNTS 列出只开放读取的账套（必须都在 U8CO_ACCOUNTS 里），这些账套的写路由一律 403 account_read_only。
可选的 U8CO_BRIDGE_ROUTES_FILE 让指定账套走另一个桥；这个文件有任何问题进程都起不来。
"""

from __future__ import annotations

import json
import logging
import os
import re
import stat
from dataclasses import dataclass, field
from datetime import timedelta, timezone
from urllib.parse import urlsplit

from u8co_api.co_ic_map import IcMap, load_ic_map  # 公司间对照
from u8co_api.co_mgmt_lines import DEFAULT_LINES, MgmtLines, load_mgmt_lines  # 经营管理利润表行定义
from u8co_api.trust import TrustEntry, load_trust

_HEX_SECRET = re.compile(r"^[0-9a-f]{64}\Z")
_ACCOUNT = re.compile(r"^[0-9]{3}\Z")
_OFFSET = re.compile(r"^([+-])([0-9]{2}):([0-9]{2})\Z")
_HEADER = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,63}\Z")
_AUDIT_TARGETS = ("stdout", "stderr", "off")
_DOCKER_SECRETS = "/run/secrets/"
_ROUTES_LIMIT = 65536
_ROUTE_REQUIRED = frozenset(("accounts", "url", "secret_file"))
_ROUTE_KEYS = _ROUTE_REQUIRED | {"timeout"}

DEFAULT_TIMEZONE = "+08:00"
DEFAULT_USER_HEADER = "X-U8co-User"


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if raw == "":
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_str(name: str, default: str = "") -> str:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip()


def _co_int(name: str, default: int) -> int:
    # 解析不了记成 0，由 configured 判为未配置，不让进程起不来。
    raw = os.environ.get(name, "").strip()
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return 0


def _url_ok(value: str) -> bool:
    parts = urlsplit(value.rstrip("/"))
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return False
    if parts.path != "/u8co" or parts.query or parts.fragment:
        return False
    return _port_ok(parts)


def _port_ok(parts) -> bool:
    try:
        port = parts.port
    except ValueError:
        return False
    if port is None:
        return True
    return 1 <= port <= 65535


def _limits_ok(timeout: int, concurrency: int, rpm: int, callers: int) -> bool:
    return timeout >= 80 and concurrency >= 1 and rpm >= 1 and callers >= 1


def parse_accounts(raw: str, name: str = "U8CO_ACCOUNTS") -> tuple[str, ...]:
    found: list[str] = []
    for item in raw.split(","):
        token = item.strip()
        if not token:
            continue
        if _ACCOUNT.fullmatch(token) is None:
            raise SystemExit(f"{name} 里的账套号必须是三位数字: {token!r}")
        if token not in found:
            found.append(token)
    return tuple(found)


def parse_read_only(raw: str, accounts: tuple[str, ...]) -> tuple[str, ...]:
    """只读账套：格式同 U8CO_ACCOUNTS；列出的账套不在 U8CO_ACCOUNTS 里时进程起不来。"""
    found = parse_accounts(raw, "U8CO_READONLY_ACCOUNTS")
    outside = [acc for acc in found if acc not in accounts]
    if outside:
        raise SystemExit(f"U8CO_READONLY_ACCOUNTS 里的账套 {outside[0]} 不在 U8CO_ACCOUNTS 里")
    return found


def parse_zone(raw: str) -> timezone:
    match = _OFFSET.fullmatch(raw)
    if match is None:
        raise SystemExit(f"U8CO_TIMEZONE 必须写成 +HH:MM 或 -HH:MM: {raw!r}")
    sign, hours, minutes = match.groups()
    if int(hours) > 14 or int(minutes) > 59:
        raise SystemExit(f"U8CO_TIMEZONE 超出范围: {raw!r}")
    delta = timedelta(hours=int(hours), minutes=int(minutes))
    return timezone(-delta if sign == "-" else delta)


def _audit_target(raw: str) -> str:
    if raw in _AUDIT_TARGETS or raw.startswith("/"):
        return raw
    raise SystemExit("U8CO_AUDIT_LOG 只能是 stdout、stderr、off 或绝对路径")


def _user_header(raw: str) -> str:
    if _HEADER.fullmatch(raw) is None:
        raise SystemExit(f"U8CO_USER_HEADER 不是合法的请求头名: {raw!r}")
    return raw


def _leeway(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit("U8CO_JWT_LEEWAY 必须是 0 到 300 的整数") from exc
    if not 0 <= value <= 300:
        raise SystemExit("U8CO_JWT_LEEWAY 必须是 0 到 300 的整数")
    return value


def read_secret_file(path: str) -> str:
    """读共享密钥文件。读不到、格式不对或权限过宽时返回空串，服务按未配置处理。"""
    if not path:
        return ""
    value, problem = _read_secret(path)
    if problem:
        logging.getLogger("u8co.api").warning("U8CO_BRIDGE_SECRET_FILE %s，按未配置处理", problem)
        return ""
    return value


def _read_secret(path: str) -> tuple[str, str]:
    # 返回（密钥，问题说明）。问题说明不含文件内容。缺省桥与分流桥共用同一套检查。
    try:
        with open(path, encoding="ascii") as handle:
            if not _private_enough(path, os.fstat(handle.fileno())):
                return "", "对属组或其他用户可读写（应为 0400 或 0600）"
            value = handle.read(200).strip()
    except (OSError, UnicodeDecodeError):
        return "", "指向的文件读不到"
    if _HEX_SECRET.fullmatch(value) is None:
        return "", "的内容必须是 64 位小写十六进制"
    return value, ""


def _private_enough(path: str, info: os.stat_result) -> bool:
    # 与桥端一致：密钥文件不能给属组或其他用户任何权限。
    # /run/secrets/ 是 docker secrets 的 tmpfs，权限由 docker 管，不查。
    if os.path.abspath(path).startswith(_DOCKER_SECRETS):
        return True
    if not stat.S_ISREG(info.st_mode):
        return True
    return info.st_mode & 0o077 == 0


@dataclass(frozen=True)
class BridgeRoute:
    """按账套分流的一个桥（U8CO_BRIDGE_ROUTES_FILE 里的一项）。timeout 为 0 时沿用 U8CO_BRIDGE_TIMEOUT。"""

    accounts: tuple[str, ...]
    url: str
    secret: str = field(default="", repr=False)
    timeout: int = 0


def load_bridge_routes(path: str, allowed: tuple[str, ...]) -> tuple[BridgeRoute, ...]:
    """读分流文件并严格校验，任何问题都让进程起不来（不会悄悄退回缺省桥）。没设路径时返回空元组。"""
    if not path:
        return ()
    data = _routes_json(path)
    if not isinstance(data, dict) or set(data) != {"routes"} or not isinstance(data["routes"], list):
        raise _routes_error('顶层必须是只含 "routes" 列表的对象')
    found: list[BridgeRoute] = []
    seen: set[str] = set()
    for index, raw in enumerate(data["routes"]):
        route = _route(f"routes[{index}]", raw, allowed)
        again = sorted(seen.intersection(route.accounts))
        if again:
            raise _routes_error(f"账套 {again[0]} 出现在多条路由里")
        seen.update(route.accounts)
        found.append(route)
    return tuple(found)


def _routes_error(message: str) -> SystemExit:
    return SystemExit(f"U8CO_BRIDGE_ROUTES_FILE: {message}")


def _routes_json(path: str) -> object:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read(_ROUTES_LIMIT + 1)
    except (OSError, UnicodeDecodeError) as exc:
        raise _routes_error("文件读不到") from exc
    if len(text) > _ROUTES_LIMIT:
        raise _routes_error("文件超过 64 KiB")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise _routes_error(f"不是合法的 JSON（第 {exc.lineno} 行）") from exc


def _route(where: str, raw: object, allowed: tuple[str, ...]) -> BridgeRoute:
    if not isinstance(raw, dict):
        raise _routes_error(f"{where} 必须是对象")
    unknown = sorted(set(raw) - _ROUTE_KEYS)
    if unknown:
        raise _routes_error(f"{where} 有不认识的键: {', '.join(unknown)}")
    absent = sorted(_ROUTE_REQUIRED - set(raw))
    if absent:
        raise _routes_error(f"{where} 缺少: {', '.join(absent)}")
    return BridgeRoute(
        accounts=_route_accounts(where, raw["accounts"], allowed),
        url=_route_url(where, raw["url"]),
        secret=_route_secret(where, raw["secret_file"]),
        timeout=_route_timeout(where, raw.get("timeout", 0)),
    )


def _route_accounts(where: str, raw: object, allowed: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise _routes_error(f"{where}.accounts 必须是非空的账套号列表")
    for item in raw:
        if not isinstance(item, str) or _ACCOUNT.fullmatch(item) is None:
            raise _routes_error(f"{where}.accounts 里的账套号必须是三位数字的字符串: {item!r}")
        if item not in allowed:
            raise _routes_error(f"{where}.accounts 里的账套 {item} 不在 U8CO_ACCOUNTS 里")
    if len(set(raw)) != len(raw):
        raise _routes_error(f"{where}.accounts 有重复的账套号")
    return tuple(raw)


def _route_url(where: str, raw: object) -> str:
    if not isinstance(raw, str) or not _url_ok(raw):
        raise _routes_error(f"{where}.url 必须是 http(s)://主机[:端口]/u8co")
    return raw


def _route_secret(where: str, raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise _routes_error(f"{where}.secret_file 必须是密钥文件路径")
    value, problem = _read_secret(raw.strip())
    if problem:
        raise _routes_error(f"{where}.secret_file {problem}")
    return value


def _route_timeout(where: str, raw: object) -> int:
    # 不写为 0，沿用 U8CO_BRIDGE_TIMEOUT；写了就与 U8CO_BRIDGE_TIMEOUT 一样不能小于 80 秒。
    if raw == 0 and not isinstance(raw, bool):
        return 0
    if not isinstance(raw, int) or isinstance(raw, bool) or raw < 80:
        raise _routes_error(f"{where}.timeout 必须是不小于 80 的整数（秒）")
    return raw


@dataclass(frozen=True)
class Settings:
    trust: tuple[TrustEntry, ...] = ()
    enabled: bool = True
    bridge_url: str = ""
    bridge_secret: str = field(default="", repr=False)
    bridge_timeout: int = 90
    # 长时操作（存货核算记账、期末处理、经过存货核算的月末结账）读桥的超时，不小于 bridge_timeout。
    bridge_long_timeout: int = 1000
    concurrency: int = 8
    caller_concurrency: int = 4
    rpm: int = 30
    accounts: tuple[str, ...] = ()
    # 只开放读取的账套（U8CO_READONLY_ACCOUNTS，U8CO_ACCOUNTS 的子集）。写路由在访问桥和写入策略之前 403。
    read_only_accounts: tuple[str, ...] = ()
    timezone: str = DEFAULT_TIMEZONE
    user_header: str = DEFAULT_USER_HEADER
    audit_log: str = "stdout"
    jwt_leeway: int = 60
    # 按账套分流的桥（U8CO_BRIDGE_ROUTES_FILE）。为空时所有账套走缺省桥。
    bridge_routes: tuple[BridgeRoute, ...] = ()
    # 写入策略文件（U8CO_WRITE_POLICY_FILE，与桥 writePolicyFile 同一份 JSON）。为空时不启用。
    write_policy_file: str = ""
    # 公司间对照（U8CO_IC_MAP_FILE）。没设时为 None，公司间路由返回 404 ic_not_configured。
    ic_map: IcMap | None = None
    # 经营管理利润表行定义（U8CO_MGMT_LINES_FILE）。没设时用内置的通用一级科目定义。
    mgmt_lines: MgmtLines = DEFAULT_LINES

    @property
    def configured(self) -> bool:
        if not _url_ok(self.bridge_url):
            return False
        if _HEX_SECRET.fullmatch(self.bridge_secret) is None:
            return False
        if self.bridge_long_timeout < self.bridge_timeout:
            return False
        return _limits_ok(self.bridge_timeout, self.concurrency, self.rpm, self.caller_concurrency)

    def missing(self) -> list[str]:
        """启动时提示用：列出缺的配置项名，不含任何值。"""
        found: list[str] = []
        if not _url_ok(self.bridge_url):
            found.append("U8CO_BRIDGE_URL")
        if _HEX_SECRET.fullmatch(self.bridge_secret) is None:
            found.append("U8CO_BRIDGE_SECRET_FILE")
        if not _limits_ok(self.bridge_timeout, self.concurrency, self.rpm, self.caller_concurrency):
            found.append("U8CO_BRIDGE_TIMEOUT/U8CO_RPM/U8CO_CONCURRENCY/U8CO_CALLER_CONCURRENCY")
        if self.bridge_long_timeout < self.bridge_timeout:
            found.append("U8CO_BRIDGE_LONG_TIMEOUT")
        if not self.accounts:
            found.append("U8CO_ACCOUNTS")
        if not self.trust:
            found.append("U8CO_TRUST_FILE 或 U8CO_OIDC_ISSUER")
        return found


def load_settings() -> Settings:
    zone = _env_str("U8CO_TIMEZONE", DEFAULT_TIMEZONE)
    parse_zone(zone)
    # 没设 U8CO_BRIDGE_LONG_TIMEOUT 时取 1000 与 U8CO_BRIDGE_TIMEOUT 中较大的：已把普通超时调到 1000 以上的部署升级后照常可用。
    bridge_timeout = _co_int("U8CO_BRIDGE_TIMEOUT", 90)
    accounts = parse_accounts(_env_str("U8CO_ACCOUNTS"))
    return Settings(
        trust=load_trust(os.environ),
        enabled=_flag("U8CO_ENABLED", True),
        bridge_url=_env_str("U8CO_BRIDGE_URL"),
        bridge_secret=read_secret_file(_env_str("U8CO_BRIDGE_SECRET_FILE")),
        bridge_timeout=bridge_timeout,
        bridge_long_timeout=_co_int("U8CO_BRIDGE_LONG_TIMEOUT", max(1000, bridge_timeout)),
        concurrency=_co_int("U8CO_CONCURRENCY", 8),
        caller_concurrency=_co_int("U8CO_CALLER_CONCURRENCY", 4),
        rpm=_co_int("U8CO_RPM", 30),
        accounts=accounts,
        read_only_accounts=parse_read_only(_env_str("U8CO_READONLY_ACCOUNTS"), accounts),
        timezone=zone,
        user_header=_user_header(_env_str("U8CO_USER_HEADER", DEFAULT_USER_HEADER)),
        audit_log=_audit_target(_env_str("U8CO_AUDIT_LOG", "stdout")),
        jwt_leeway=_leeway(_env_str("U8CO_JWT_LEEWAY", "60")),
        bridge_routes=load_bridge_routes(_env_str("U8CO_BRIDGE_ROUTES_FILE"), accounts),
        write_policy_file=_env_str("U8CO_WRITE_POLICY_FILE"),
        ic_map=_ic_map(_env_str("U8CO_IC_MAP_FILE")),
        mgmt_lines=load_mgmt_lines(_env_str("U8CO_MGMT_LINES_FILE")),
    )


def _ic_map(path: str) -> IcMap | None:
    # 没设路径时不启用公司间路由；设了就严格校验，文件有问题进程起不来。
    return load_ic_map(path) if path else None
