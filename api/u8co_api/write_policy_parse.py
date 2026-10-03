"""写入策略文件（U8CO_WRITE_POLICY_FILE）的解析与校验，与桥的 WritePolicyParse.cs 同一套规则（version 1）。

任何对象层级都可以写字符串 "comment"；其余未知键一律拒绝（整份无效）。报错是 PolicyError，消息写明键路径，
例如「写入策略 accounts.801.allow[0].ops 含未知操作 'sell'」。license 段只在桥上生效，这里只校验。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

# 操作词表（封闭），与桥 WritePolicySnapshot.OpNames 相同。规则里另可写 "*"。
OP_NAMES = (
    "create", "update", "delete", "verify", "unverify", "close", "open", "generate", "lock", "unlock",
    "workflow", "writeoff", "voucher", "post", "process", "other",
)  # fmt: skip

_TOP_KEYS = frozenset(
    (
        "comment", "version", "reloadSeconds", "freeze", "windows",
        "denyDates", "license", "defaults", "unlisted", "accounts",
    )  # fmt: skip
)
_FREEZE_KEYS = frozenset(("comment", "global", "accounts", "reason"))
_WINDOW_KEYS = frozenset(("comment", "days", "start", "end"))
_LICENSE_KEYS = frozenset(("comment", "maxConcurrentLogins", "holdWritesWhen"))
_QUOTA_KEYS = frozenset(("comment", "writesPerMinute", "writesPerDay", "maxLines", "maxAmount"))
_ACCOUNT_KEYS = frozenset(("comment", "operators", "quotas", "allow"))
_OPERATOR_KEYS = frozenset(("comment", "allow", "deny"))
_RULE_KEYS = frozenset(("comment", "type", "ops"))
_LICENSE_STATES = frozenset(("near", "full", "unknown"))
_DAYS = re.compile(r"^[1-7](?:-[1-7])?(?:,[1-7](?:-[1-7])?)*\Z")
_CLOCK = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])\Z")
_TYPE = re.compile(r"^(\*|[a-z][a-z0-9_]{0,63})\Z")
_ACC = re.compile(r"^[0-9]{3}\Z")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_AMOUNT_MAX = Decimal(1000000000000)


class PolicyError(ValueError):
    """策略文件无效。消息以「写入策略」开头。"""

    def __init__(self, message: str) -> None:
        super().__init__("写入策略 " + message)


@dataclass(frozen=True)
class Limits:
    """限额，0 表示不限。账套 quotas 按键覆盖 defaults。"""

    per_minute: int = 0
    per_day: int = 0
    max_lines: int = 0
    max_amount: Decimal = Decimal(0)


@dataclass(frozen=True)
class Rule:
    type: str
    ops: frozenset[str]

    def matches(self, type_: str, op: str) -> bool:
        if self.type != "*" and self.type != type_:
            return False
        return "*" in self.ops or op in self.ops


@dataclass(frozen=True)
class Operators:
    """操作员名单：deny 优先；allow 为空表示任何操作员。去首尾空白、不区分大小写比较。"""

    allow: frozenset[str] = frozenset()
    deny: frozenset[str] = frozenset()

    def permits(self, operator: str | None) -> bool:
        who = (operator or "").strip().casefold()
        if who and who in self.deny:
            return False
        return not self.allow or (bool(who) and who in self.allow)


@dataclass(frozen=True)
class Account:
    operators: Operators
    limits: Limits
    rules: tuple[Rule, ...]

    def allows(self, type_: str, op: str) -> bool:
        return any(rule.matches(type_, op) for rule in self.rules)


@dataclass(frozen=True)
class Window:
    """写入时段：ISO 星期（1 是星期一），含 start、不含 end（秒），不跨午夜。"""

    days: frozenset[int]
    start: int
    end: int

    def contains(self, local: datetime) -> bool:
        clock = local.hour * 3600 + local.minute * 60 + local.second
        return local.isoweekday() in self.days and self.start <= clock < self.end


@dataclass(frozen=True)
class Snapshot:
    """一份解析后的策略。不可变；重载时整份替换。"""

    version: int
    reload_seconds: int
    loaded_at: datetime
    freeze_global: bool = False
    freeze_accounts: tuple[str, ...] = ()
    freeze_reason: str | None = None
    windows: tuple[Window, ...] | None = None
    deny_dates: frozenset[str] = frozenset()
    defaults: Limits = Limits()
    unlisted_allow: bool = False
    accounts: dict[str, Account] = field(default_factory=dict)

    def account(self, acc: str) -> Account | None:
        return self.accounts.get(acc)

    def limits(self, acc: str) -> Limits:
        found = self.account(acc)
        return self.defaults if found is None else found.limits

    def is_frozen(self, acc: str) -> bool:
        return self.freeze_global or acc in self.freeze_accounts

    def window_open(self, local: datetime) -> bool:
        if local.strftime("%Y-%m-%d") in self.deny_dates:
            return False
        if self.windows is None:
            return True
        return any(window.contains(local) for window in self.windows)


def parse(text: str, loaded_at: datetime) -> Snapshot:
    data = _json(text)
    _keys(data, _TOP_KEYS, "")
    version = data.get("version")
    if not _is_int(version) or version != 1:
        raise PolicyError("version 必须是 1")
    freeze = _section(data, "freeze", "freeze")
    _keys(freeze, _FREEZE_KEYS, "freeze")
    _license(data)
    defaults = _limits(_section(data, "defaults", "defaults"), "defaults", Limits())
    return Snapshot(
        version=1,
        reload_seconds=_int(data, "reloadSeconds", "reloadSeconds", (2, 1, 3600)),
        loaded_at=loaded_at,
        freeze_global=_bool(freeze, "global", "freeze.global"),
        freeze_accounts=_accounts(freeze, "accounts", "freeze.accounts"),
        freeze_reason=_text(freeze, "reason", "freeze.reason"),
        windows=_windows(data),
        deny_dates=_dates(data),
        defaults=defaults,
        unlisted_allow=_unlisted(data),
        accounts=_account_map(data, defaults),
    )


def _json(text: str) -> dict:
    try:
        data = json.loads(text, parse_constant=_no_constant)
    except (ValueError, RecursionError) as exc:
        raise PolicyError(f"不是有效的 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise PolicyError("必须是 JSON 对象")
    return data


def _no_constant(name: str) -> None:
    raise ValueError(f"不支持 {name}")


def _windows(data: dict) -> tuple[Window, ...] | None:
    if "windows" not in data:
        return None
    items = _list(data["windows"], "windows")
    if not items:
        raise PolicyError("windows 不能是空数组；不限时段时删掉这个键")
    found: list[Window] = []
    for index, raw in enumerate(items):
        at = f"windows[{index}]"
        sec = _object(raw, at)
        _keys(sec, _WINDOW_KEYS, at)
        start = _clock(_need(sec, "start", at + ".start"), at + ".start")
        end = _clock(_need(sec, "end", at + ".end"), at + ".end")
        if end <= start:
            raise PolicyError(at + ".end 必须晚于 start（不支持跨午夜）")
        found.append(Window(_days(_need(sec, "days", at + ".days"), at + ".days"), start, end))
    return tuple(found)


def _dates(data: dict) -> frozenset[str]:
    dates = _strings(data, "denyDates", "denyDates")
    for item in dates:
        if not _DATE.fullmatch(item) or not _real_date(item):
            raise PolicyError(f"denyDates 的每一项必须是 yyyy-MM-dd：'{item}'")
    return frozenset(dates)


def _real_date(text: str) -> bool:
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _license(data: dict) -> None:
    # 只在桥上生效：同时在线的接口登录上限、按许可状态暂停写入。这里只校验，保证两边对同一份文件的判断一致。
    sec = _section(data, "license", "license")
    _keys(sec, _LICENSE_KEYS, "license")
    _int(sec, "maxConcurrentLogins", "license.maxConcurrentLogins", (0, 0, 64))
    for state in _strings(sec, "holdWritesWhen", "license.holdWritesWhen"):
        if state not in _LICENSE_STATES:
            raise PolicyError(f"license.holdWritesWhen 只能是 near、full、unknown：'{state}'")


def _limits(sec: dict, at: str, fallback: Limits) -> Limits:
    _keys(sec, _QUOTA_KEYS, at)
    return Limits(
        per_minute=_int(sec, "writesPerMinute", at + ".writesPerMinute", (fallback.per_minute, 0, 100000)),
        per_day=_int(sec, "writesPerDay", at + ".writesPerDay", (fallback.per_day, 0, 10000000)),
        max_lines=_int(sec, "maxLines", at + ".maxLines", (fallback.max_lines, 0, 100000)),
        max_amount=_amount(sec, "maxAmount", at + ".maxAmount", fallback.max_amount),
    )


def _unlisted(data: dict) -> bool:
    text = _text(data, "unlisted", "unlisted")
    if text is None or text == "deny":
        return False
    if text == "allow":
        return True
    raise PolicyError('unlisted 只能是 "deny" 或 "allow"')


def _account_map(data: dict, defaults: Limits) -> dict[str, Account]:
    found: dict[str, Account] = {}
    for key, raw in _section(data, "accounts", "accounts").items():
        if key == "comment":
            continue
        if not _ACC.fullmatch(key):
            raise PolicyError(f"accounts 的键必须是 3 位数字的账套号：'{key}'")
        found[key] = _account(raw, "accounts." + key, defaults)
    return found


def _account(raw: object, at: str, defaults: Limits) -> Account:
    sec = _object(raw, at)
    _keys(sec, _ACCOUNT_KEYS, at)
    ops = _section(sec, "operators", at + ".operators")
    _keys(ops, _OPERATOR_KEYS, at + ".operators")
    operators = Operators(
        allow=frozenset(item.casefold() for item in _strings(ops, "allow", at + ".operators.allow")),
        deny=frozenset(item.casefold() for item in _strings(ops, "deny", at + ".operators.deny")),
    )
    limits = _limits(_section(sec, "quotas", at + ".quotas"), at + ".quotas", defaults)
    return Account(operators=operators, limits=limits, rules=_rules(sec, at + ".allow"))


def _rules(sec: dict, at: str) -> tuple[Rule, ...]:
    if "allow" not in sec:
        return ()
    found: list[Rule] = []
    for index, raw in enumerate(_list(sec["allow"], at)):
        here = f"{at}[{index}]"
        rule = _object(raw, here)
        _keys(rule, _RULE_KEYS, here)
        type_ = _need(rule, "type", here + ".type")
        if not _TYPE.fullmatch(type_):
            raise PolicyError(f"{here}.type 必须是 \"*\" 或小写的类型名：'{type_}'")
        found.append(Rule(type_, _ops(rule, here + ".ops")))
    return tuple(found)


def _ops(rule: dict, at: str) -> frozenset[str]:
    if "ops" not in rule:
        raise PolicyError("缺少 " + at)
    ops = _strings(rule, "ops", at)
    if not ops:
        raise PolicyError(at + " 不能是空数组")
    for op in ops:
        if op != "*" and op not in OP_NAMES:
            raise PolicyError(f"{at} 含未知操作 '{op}'；可用：* 或 " + "、".join(OP_NAMES))
    return frozenset(ops)


def _clock(text: str, at: str) -> int:
    match = _CLOCK.fullmatch(text)
    if match is None:
        raise PolicyError(f"{at} 必须是 HH:MM：'{text}'")
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60


def _days(text: str, at: str) -> frozenset[int]:
    if not _DAYS.fullmatch(text):
        raise PolicyError(f"{at} 不合法：'{text}'（写成 1-5 或 1,3,5，1 是星期一）")
    found: set[int] = set()
    for part in text.split(","):
        first = int(part[0])
        last = int(part[2]) if len(part) > 1 else first
        if last < first:
            raise PolicyError(f"{at} 的范围 '{part}' 反了")
        found.update(range(first, last + 1))
    return frozenset(found)


# ---- 字段读取：类型与取值范围不对就报错，消息带键路径 ----


def _keys(sec: dict, known: frozenset[str], at: str) -> None:
    prefix = at + "." if at else ""
    for key, value in sec.items():
        if key not in known:
            raise PolicyError(f"含未知字段 {prefix}{key}；可用：" + ", ".join(sorted(known)))
        if key == "comment" and not isinstance(value, str):
            raise PolicyError(prefix + "comment 必须是字符串")


def _section(data: dict, key: str, at: str) -> dict:
    # 可选对象：缺少时当作空对象。
    if key not in data:
        return {}
    return _object(data[key], at)


def _object(raw: object, at: str) -> dict:
    if not isinstance(raw, dict):
        raise PolicyError(at + " 必须是对象")
    return raw


def _list(raw: object, at: str) -> list:
    if not isinstance(raw, list):
        raise PolicyError(at + " 必须是数组")
    return raw


def _strings(data: dict, key: str, at: str) -> tuple[str, ...]:
    # 可选字符串数组：缺少时为空；元素必须是非空字符串（去首尾空白）。
    if key not in data:
        return ()
    found: list[str] = []
    for item in _list(data[key], at):
        if not isinstance(item, str) or not item.strip():
            raise PolicyError(at + " 只能含非空字符串")
        found.append(item.strip())
    return tuple(found)


def _accounts(data: dict, key: str, at: str) -> tuple[str, ...]:
    values = _strings(data, key, at)
    for item in values:
        if not _ACC.fullmatch(item):
            raise PolicyError(f"{at} 的每一项必须是 3 位数字的账套号：'{item}'")
    return values


def _bool(data: dict, key: str, at: str) -> bool:
    raw = data.get(key, False)
    if not isinstance(raw, bool):
        raise PolicyError(at + " 必须是 true 或 false")
    return raw


def _text(data: dict, key: str, at: str) -> str | None:
    # 可选字符串：缺少为 None；写了就必须是字符串（去首尾空白，空白当作没写）。
    if key not in data:
        return None
    raw = data[key]
    if not isinstance(raw, str):
        raise PolicyError(at + " 必须是字符串")
    return raw.strip() or None


def _need(data: dict, key: str, at: str) -> str:
    text = _text(data, key, at)
    if text is None:
        raise PolicyError("缺少 " + at)
    return text


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int(data: dict, key: str, at: str, spec: tuple[int, int, int]) -> int:
    # spec = (缺省值, 最小值, 最大值)。
    fallback, low, high = spec
    if key not in data:
        return fallback
    raw = data[key]
    if not _is_int(raw) or not low <= raw <= high:
        raise PolicyError(f"{at} 必须是 {low} 到 {high} 的整数")
    return raw


def _amount(data: dict, key: str, at: str, fallback: Decimal) -> Decimal:
    # 金额：非负数，0 表示不限。
    if key not in data:
        return fallback
    raw = data[key]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise PolicyError(at + " 必须是数字")
    try:
        value = Decimal(str(raw))
    except InvalidOperation as exc:
        raise PolicyError(at + " 必须是数字") from exc
    if not value.is_finite() or not Decimal(0) <= value <= _AMOUNT_MAX:
        raise PolicyError(at + " 必须在 0 到 1000000000000 之间（0 表示不限）")
    return value
