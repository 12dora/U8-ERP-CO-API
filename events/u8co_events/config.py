"""事件服务配置。JSON 文件，每一层都只收认识的键；环境变量只能改路径。

拼错的键不能悄悄落回缺省值，所以未知键一律报错并列出可用的键。
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from co.client.u8co_kinds import KIND_NAMES


class ConfigError(RuntimeError):
    pass


DEFAULT_CONFIG = "/etc/u8co-events/config.json"
DEFAULT_STATE = "/var/lib/u8co-events/state.sqlite3"

_TOP_KEYS = frozenset(
    (
        "state_path",
        "bridge",
        "accounts",
        "poll_interval_seconds",
        "page_limit",
        "delete_scan_minutes",
        "backfill_events",
        "outbox_high_water",
        "redis",
        "publisher",
        "health",
        "allow_reseed",
        "empty_scan_confirm_max",
        "auto_id_lag",
        "gl_closed_periods",
    )
)
_BRIDGE_KEYS = frozenset(("base_url", "secret_file", "timeout_seconds"))
_ACCOUNT_KEYS = frozenset(("acc", "operator_file", "types", "sources", "archives"))
_REDIS_KEYS = frozenset(("url", "password_file", "stream_prefix", "maxlen", "allow_nondurable_redis"))
_PUBLISHER_KEYS = frozenset(("kind", "batch", "idle_seconds"))
_HEALTH_KEYS = frozenset(("listen",))

_ACC = re.compile(r"^\d{3}\Z")
_BASE_URL = re.compile(r"^https?://[A-Za-z0-9.-]{1,253}:\d{1,5}/u8co/?\Z")
_REDIS_URL = re.compile(r"^(?:redis|rediss|unix)://\S{1,500}\Z")
_LISTEN = re.compile(r"^[A-Za-z0-9.:-]{1,253}:\d{1,5}\Z")
_PREFIX = re.compile(r"^[A-Za-z0-9:_.-]{1,100}\Z")

# 环境变量只覆盖路径，不覆盖行为开关。
ENV_CONFIG = "U8CO_EVENTS_CONFIG"
ENV_STATE = "U8CO_EVENTS_STATE"
ENV_SECRET = "U8CO_EVENTS_SECRET_FILE"
ENV_REDIS_PASSWORD = "U8CO_EVENTS_REDIS_PASSWORD_FILE"

# 数据源。vouchers（单据，类型见 types）和 notes（应收应付票据）走单据轮询；其余各有自己的模块（见 source.py）。
SOURCE_NAMES = ("vouchers", "arap_process", "archives", "gl", "notes")
DEFAULT_SOURCES = ("vouchers",)
# 各数据源在状态与健康检查里的类型名（type_state 一行一种），不能与单据类型重名。
PROCESS_TYPES = ("ar_process", "ap_process")
GL_TYPES = ("gl_voucher",)
NOTE_TYPES = ("ar_note", "ap_note")
ARCHIVE_PREFIX = "archive:"
# 有时间戳（rowversion）的档案：按水位增量读，主键扫描找删除。缺省全开。
RV_ARCHIVES = (
    "customer", "vendor", "inventory", "department", "person", "warehouse",
    "customer_class", "vendor_class", "inventory_class",
    "account", "unit", "unit_group", "settle_style", "currency", "bank", "position", "rd_style",
    "purchase_type", "sale_type", "district_class", "trade_class", "aa_bank", "user_define",
    "customer_inventory", "exchange_rate", "reason", "customer_contact", "vendor_contact", "equipment",
)
# 没有时间戳的档案：按删除扫描的节奏整表比对。缺省不开，要在 archives 里写明。
# fa_card、operator、role 数据量大或只有账套主管能读，尤其要先确认再开。
PLAIN_ARCHIVES = (
    "voucher_sign", "project", "customer_address", "customer_bank", "vendor_bank", "fa_card", "operator", "role",
)


@dataclass(frozen=True)
class BridgeConf:
    base_url: str
    secret_file: str
    timeout_seconds: float


@dataclass(frozen=True)
class AccountConf:
    acc: str
    operator_file: str
    # 单据类型；sources 不含 vouchers 时为空。
    types: tuple[str, ...]
    sources: tuple[str, ...] = DEFAULT_SOURCES
    archives: tuple[str, ...] = RV_ARCHIVES

    def list_types(self) -> tuple[str, ...]:
        """走单据轮询（vouchers/list）的类型：单据类型，加上开了 notes 时的两种票据。"""
        notes = NOTE_TYPES if "notes" in self.sources else ()
        return self.types + notes

    def source_types(self, name: str) -> tuple[str, ...]:
        """附加数据源 name 在状态里的类型名；没开这个数据源时为空。"""
        if name not in self.sources:
            return ()
        if name == "arap_process":
            return PROCESS_TYPES
        if name == "gl":
            return GL_TYPES
        if name == "archives":
            return tuple(ARCHIVE_PREFIX + item for item in self.archives)
        return ()

    def all_types(self) -> tuple[str, ...]:
        """这个账套在状态和健康检查里的全部类型。"""
        extra = tuple(type_ for name in self.sources for type_ in self.source_types(name))
        return self.list_types() + extra


@dataclass(frozen=True)
class RedisConf:
    url: str
    password_file: str
    stream_prefix: str
    maxlen: int
    allow_nondurable_redis: bool


@dataclass(frozen=True)
class PublisherConf:
    kind: str
    batch: int
    idle_seconds: float


@dataclass(frozen=True)
class HealthConf:
    listen: str


@dataclass(frozen=True)
class Config:
    state_path: str
    bridge: BridgeConf
    accounts: tuple[AccountConf, ...]
    poll_interval_seconds: float
    page_limit: int
    delete_scan_minutes: float
    backfill_events: bool
    outbox_high_water: int
    redis: RedisConf
    publisher: PublisherConf
    health: HealthConf
    # 状态库是新的、但 Redis 里该账套的流已经存在时（多半是状态卷丢了），缺省拒绝启动。
    # 确认要从现在起重新回填（丢掉上次水位之后、以及发件箱里没发出的变化）才设 true。
    allow_reseed: bool = False
    # 主键扫描一张都没扫到、快照里却有单据时，逐张 vouchers/load 确认的上限；0 表示不确认，照旧报错。
    empty_scan_confirm_max: int = 50
    # 应收应付处理按自增号（Auto_ID）取增量时，每轮回看的最少条数：未提交的事务占了号、晚于后面的号提交时不漏。
    auto_id_lag: int = 500
    # 总账凭证摘要除未结账月份外，再看最近几个已结账月份（结账前一刻的改动）。
    gl_closed_periods: int = 1

    def pairs(self) -> frozenset[tuple[str, str]]:
        """配置里的全部（账套, 类型）。状态和健康检查只看这些，去掉的类型留下的旧行不算。"""
        return frozenset((account.acc, type_) for account in self.accounts for type_ in account.all_types())


def _known(sec: Mapping, keys: frozenset[str], where: str) -> None:
    extra = sorted(key for key in sec if key not in keys)
    if extra:
        raise ConfigError(f"{where} 里有不认识的键 {extra[0]!r}；可用的键：{', '.join(sorted(keys))}")


def _section(data: Mapping, name: str, keys: frozenset[str]) -> Mapping:
    value = data.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"配置项 {name} 必须是对象")
    _known(value, keys, "配置项 " + name)
    return value


def _text(sec: Mapping, key: str, default: str, where: str) -> str:
    value = sec.get(key)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigError(f"配置项 {where} 必须是字符串")
    return value.strip()


def _flag(sec: Mapping, key: str, default: bool, where: str) -> bool:
    value = sec.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise ConfigError(f"配置项 {where} 必须是 true 或 false")
    return value


def _number(sec: Mapping, key: str, default: float, bounds: tuple[float, float], where: str) -> float:
    value = sec.get(key)
    if value is None:
        return default
    # bool 是 int 的子类，要单独挡掉。
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"配置项 {where} 必须是数字")
    low, high = bounds
    if not low <= value <= high:
        raise ConfigError(f"配置项 {where} 必须在 {low:g} 到 {high:g} 之间")
    return float(value)


def _integer(sec: Mapping, key: str, default: int, bounds: tuple[int, int], where: str) -> int:
    value = sec.get(key)
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise ConfigError(f"配置项 {where} 必须是整数")
    return int(_number(sec, key, default, bounds, where))


def _path(value: str, where: str) -> str:
    if not value:
        raise ConfigError(f"缺少配置项 {where}")
    if "\0" in value:
        raise ConfigError(f"配置项 {where} 不是合法路径")
    return value


def _bridge(data: Mapping, env: Mapping[str, str]) -> BridgeConf:
    sec = _section(data, "bridge", _BRIDGE_KEYS)
    base_url = _text(sec, "base_url", "", "bridge.base_url")
    if _BASE_URL.fullmatch(base_url) is None:
        raise ConfigError("配置项 bridge.base_url 必须形如 http://主机:端口/u8co")
    secret = env.get(ENV_SECRET, "").strip() or _text(sec, "secret_file", "", "bridge.secret_file")
    return BridgeConf(
        base_url=base_url.rstrip("/"),
        secret_file=_path(secret, "bridge.secret_file"),
        timeout_seconds=_number(sec, "timeout_seconds", 60, (5, 600), "bridge.timeout_seconds"),
    )


def _types(value: object, where: str) -> tuple[str, ...]:
    if value is None:
        return KIND_NAMES
    if not isinstance(value, list) or not value:
        raise ConfigError(f"配置项 {where} 必须是非空数组，或者不写（表示全部类型）")
    seen: list[str] = []
    for item in value:
        if item not in KIND_NAMES:
            raise ConfigError(f"配置项 {where} 里有未知的单据类型 {item!r}")
        if item in seen:
            raise ConfigError(f"配置项 {where} 里 {item} 重复")
        seen.append(item)
    return tuple(seen)


def _names(value: object, allowed: tuple[str, ...], what: str, where: str) -> tuple[str, ...]:
    """非空、不重复、都在 allowed 里的名字数组。"""
    if not isinstance(value, list) or not value:
        raise ConfigError(f"配置项 {where} 必须是非空数组，或者不写（表示缺省）")
    seen: list[str] = []
    for item in value:
        if item not in allowed:
            raise ConfigError(f"配置项 {where} 里有未知的{what} {item!r}；可用的：{', '.join(allowed)}")
        if item in seen:
            raise ConfigError(f"配置项 {where} 里 {item} 重复")
        seen.append(item)
    return tuple(seen)


def _sources(item: Mapping, where: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """（sources, types, archives）。types 只在开了 vouchers 时有效，archives 只在开了 archives 时有效。"""
    raw = item.get("sources")
    sources = DEFAULT_SOURCES if raw is None else _names(raw, SOURCE_NAMES, "数据源", where + ".sources")
    if "vouchers" in sources:
        types = _types(item.get("types"), where + ".types")
    elif item.get("types") is not None:
        raise ConfigError(f"配置项 {where}.types 只在 sources 含 vouchers 时有效")
    else:
        types = ()
    raw_archives = item.get("archives")
    if raw_archives is None:
        archives = RV_ARCHIVES
    elif "archives" not in sources:
        raise ConfigError(f"配置项 {where}.archives 只在 sources 含 archives 时有效")
    else:
        archives = _names(raw_archives, RV_ARCHIVES + PLAIN_ARCHIVES, "档案", where + ".archives")
    return sources, types, archives


def _account(item: object, index: int) -> AccountConf:
    where = f"accounts[{index}]"
    if not isinstance(item, dict):
        raise ConfigError(f"配置项 {where} 必须是对象")
    _known(item, _ACCOUNT_KEYS, "配置项 " + where)
    acc = _text(item, "acc", "", where + ".acc")
    if _ACC.fullmatch(acc) is None:
        raise ConfigError(f"配置项 {where}.acc 必须是 3 位数字账套号")
    operator = _path(_text(item, "operator_file", "", where + ".operator_file"), where + ".operator_file")
    sources, types, archives = _sources(item, where)
    return AccountConf(acc=acc, operator_file=operator, types=types, sources=sources, archives=archives)


def _accounts(data: Mapping) -> tuple[AccountConf, ...]:
    value = data.get("accounts")
    if not isinstance(value, list) or not value:
        raise ConfigError("配置项 accounts 必须是非空数组")
    accounts = tuple(_account(item, index) for index, item in enumerate(value))
    codes = [account.acc for account in accounts]
    if len(set(codes)) != len(codes):
        raise ConfigError("配置项 accounts 里账套号重复")
    return accounts


def _redis_url(sec: Mapping) -> str:
    url = _text(sec, "url", "redis://redis:6379/0", "redis.url")
    if _REDIS_URL.fullmatch(url) is None:
        raise ConfigError("配置项 redis.url 必须以 redis://、rediss:// 或 unix:// 开头")
    # 口令只能放 0600 的 redis.password_file，不能写在地址里（配置文件不要求 0600）。
    try:
        parts = urllib.parse.urlsplit(url)
        inline = parts.password is not None
    except ValueError:
        raise ConfigError("配置项 redis.url 不是合法地址") from None
    query = {key.lower() for key in urllib.parse.parse_qs(parts.query, keep_blank_values=True)}
    if inline or "password" in query:
        raise ConfigError("配置项 redis.url 不能带口令；口令写进权限 600 的文件，用 redis.password_file 指定")
    return url


def _redis(data: Mapping, env: Mapping[str, str]) -> RedisConf:
    sec = _section(data, "redis", _REDIS_KEYS)
    url = _redis_url(sec)
    prefix = _text(sec, "stream_prefix", "u8co:events:", "redis.stream_prefix")
    if _PREFIX.fullmatch(prefix) is None:
        raise ConfigError("配置项 redis.stream_prefix 只能用字母、数字和 : _ . -")
    password = env.get(ENV_REDIS_PASSWORD, "").strip() or _text(sec, "password_file", "", "redis.password_file")
    return RedisConf(
        url=url,
        password_file=password,
        stream_prefix=prefix,
        maxlen=_integer(sec, "maxlen", 1_000_000, (1000, 100_000_000), "redis.maxlen"),
        allow_nondurable_redis=_flag(sec, "allow_nondurable_redis", False, "redis.allow_nondurable_redis"),
    )


def _publisher(data: Mapping) -> PublisherConf:
    sec = _section(data, "publisher", _PUBLISHER_KEYS)
    kind = _text(sec, "kind", "redis", "publisher.kind")
    if kind not in ("redis", "stdout"):
        raise ConfigError("配置项 publisher.kind 只能是 redis 或 stdout")
    return PublisherConf(
        kind=kind,
        batch=_integer(sec, "batch", 100, (1, 1000), "publisher.batch"),
        idle_seconds=_number(sec, "idle_seconds", 1.0, (0.1, 60), "publisher.idle_seconds"),
    )


def _health(data: Mapping) -> HealthConf:
    sec = _section(data, "health", _HEALTH_KEYS)
    listen = _text(sec, "listen", "127.0.0.1:8090", "health.listen")
    if listen and _LISTEN.fullmatch(listen) is None:
        raise ConfigError("配置项 health.listen 必须形如 127.0.0.1:8090，或者留空表示不开")
    return HealthConf(listen=listen)


def from_dict(data: object, env: Mapping[str, str] | None = None) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("配置文件必须是 JSON 对象")
    _known(data, _TOP_KEYS, "配置文件")
    env = os.environ if env is None else env
    state = env.get(ENV_STATE, "").strip() or _text(data, "state_path", DEFAULT_STATE, "state_path")
    return Config(
        state_path=_path(state, "state_path"),
        bridge=_bridge(data, env),
        accounts=_accounts(data),
        poll_interval_seconds=_number(data, "poll_interval_seconds", 30, (5, 3600), "poll_interval_seconds"),
        page_limit=_integer(data, "page_limit", 500, (1, 500), "page_limit"),
        delete_scan_minutes=_number(data, "delete_scan_minutes", 30, (0, 10080), "delete_scan_minutes"),
        backfill_events=_flag(data, "backfill_events", False, "backfill_events"),
        outbox_high_water=_integer(data, "outbox_high_water", 100_000, (1000, 10_000_000), "outbox_high_water"),
        redis=_redis(data, env),
        publisher=_publisher(data),
        health=_health(data),
        allow_reseed=_flag(data, "allow_reseed", False, "allow_reseed"),
        empty_scan_confirm_max=_integer(data, "empty_scan_confirm_max", 50, (0, 500), "empty_scan_confirm_max"),
        auto_id_lag=_integer(data, "auto_id_lag", 500, (50, 1_000_000), "auto_id_lag"),
        gl_closed_periods=_integer(data, "gl_closed_periods", 1, (0, 12), "gl_closed_periods"),
    )


def config_path() -> Path:
    return Path(os.environ.get(ENV_CONFIG, "").strip() or DEFAULT_CONFIG)


def load(path: Path | None = None) -> Config:
    target = path if path is not None else config_path()
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        raise ConfigError(f"读不了配置文件 {target}") from None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"配置文件 {target} 不是合法 JSON：第 {exc.lineno} 行") from None
    return from_dict(data)


def read_private(path: str, what: str) -> str:
    """读 0600 的小文件（密钥、凭证）。组和其他人有任何权限都拒绝。"""
    target = Path(path).expanduser()
    try:
        mode = target.stat().st_mode
    except OSError:
        raise ConfigError(f"找不到{what}文件 {target}") from None
    if mode & 0o077:
        raise ConfigError(f"{what}文件 {target} 权限过宽，请 chmod 600")
    try:
        return target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise ConfigError(f"读不了{what}文件 {target}") from None


def read_secret(path: str) -> str:
    return read_private(path, "密钥").strip()
