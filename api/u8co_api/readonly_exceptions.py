"""只读例外（U8CO_READONLY_EXCEPTIONS）：只读账套上允许穿过闸门的精确写入。

JSON 数组，每项 accounts / operators / type / op。当前只开放 type=archives、
op=customer_person，且路由必须是 archives/customer_person。账套必须是
U8CO_READONLY_ACCOUNTS 的子集。未知键（含已取消的 archive / fields）、其它 type/op、
不合规的操作员编码一律让进程起不来。操作员比较不区分大小写，不去首尾空白。
archives/update 在只读账套上仍一律 403。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

ENV = "U8CO_READONLY_EXCEPTIONS"
_KEYS = frozenset(("accounts", "operators", "type", "op"))
_REQUIRED = ("accounts", "operators", "type", "op")
_TYPE = "archives"
_OP = "customer_person"
_ACTION = "co:archives/customer_person"
_SIZE = 65536
_ACCOUNT = re.compile(r"^[0-9]{3}\Z")


@dataclass(frozen=True)
class ReadOnlyException:
    """一条只读例外。operators 保留配置原文，匹配时操作员不区分大小写。"""

    accounts: tuple[str, ...]
    operators: tuple[str, ...]
    type: str
    op: str

    def matches(self, acc: str, operator: str) -> bool:
        if acc not in self.accounts:
            return False
        who = operator.casefold()
        return who in {item.casefold() for item in self.operators}

    def summary(self) -> dict:
        return {
            "accounts": list(self.accounts),
            "operators": list(self.operators),
            "type": self.type,
            "op": self.op,
        }


def parse_exceptions(raw: str, read_only: tuple[str, ...]) -> tuple[ReadOnlyException, ...]:
    """解析环境变量。空串为未配置；无效内容让进程起不来。"""
    if not raw:
        return ()
    data = _json(raw)
    if not isinstance(data, list):
        raise SystemExit(f"{ENV} 必须是 JSON 数组")
    return tuple(_one(f"{ENV}[{index}]", item, read_only) for index, item in enumerate(data))


def allows(exceptions: tuple[ReadOnlyException, ...], action: str, payload: dict) -> bool:
    """路由必须是 archives/customer_person，且任一例外的账套、操作员命中才放行。"""
    if not exceptions or action != _ACTION:
        return False
    acc = _text(payload.get("acc"))
    operator = _text(payload.get("operator"))
    return any(item.matches(acc, operator) for item in exceptions)


def health_of(exceptions: tuple[ReadOnlyException, ...]) -> list[dict] | None:
    """健康检查用的非机密摘要；未配置时省略。"""
    if not exceptions:
        return None
    return [item.summary() for item in exceptions]


def _json(raw: str) -> object:
    if len(raw) > _SIZE:
        raise SystemExit(f"{ENV} 超过 64 KiB")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{ENV} 不是合法的 JSON（第 {exc.lineno} 行）") from exc


def _one(where: str, raw: object, read_only: tuple[str, ...]) -> ReadOnlyException:
    if not isinstance(raw, dict):
        raise SystemExit(f"{where} 必须是对象")
    unknown = sorted(set(raw) - _KEYS)
    if unknown:
        raise SystemExit(f"{where} 有不认识的键: {', '.join(unknown)}")
    absent = [key for key in _REQUIRED if key not in raw]
    if absent:
        raise SystemExit(f"{where} 缺少: {', '.join(absent)}")
    type_, op = _kind(where, raw["type"], raw["op"])
    return ReadOnlyException(
        accounts=_accounts(where, raw["accounts"], read_only),
        operators=_operators(where, raw["operators"]),
        type=type_,
        op=op,
    )


def _kind(where: str, type_: object, op: object) -> tuple[str, str]:
    if type_ != _TYPE:
        raise SystemExit(f"{where}.type 只能是 archives（目前只开放客户专管业务员）")
    if op != _OP:
        raise SystemExit(f"{where}.op 只能是 customer_person（目前只开放客户专管业务员）")
    return _TYPE, _OP


def _accounts(where: str, raw: object, read_only: tuple[str, ...]) -> tuple[str, ...]:
    found = _strings(where, raw, "accounts", False)
    for acc in found:
        if _ACCOUNT.fullmatch(acc) is None:
            raise SystemExit(f"{where}.accounts 里的账套号必须是三位数字: {acc!r}")
        if acc not in read_only:
            raise SystemExit(f"{where}.accounts 里的账套 {acc} 不在 U8CO_READONLY_ACCOUNTS 里")
    return found


def _operators(where: str, raw: object) -> tuple[str, ...]:
    found = _strings(where, raw, "operators", True)
    for who in found:
        if not _operator_code(who):
            raise SystemExit(f"{where}.operators 里的项必须是有效的 U8 操作员编码: {who}")
    return found


def _strings(where: str, raw: object, key: str, ignore_case: bool) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise SystemExit(f"{where}.{key} 必须是非空数组")
    found: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str) or not item:
            raise SystemExit(f"{where}.{key} 里的项必须是非空字符串")
        token = item.casefold() if ignore_case else item
        if token in seen:
            raise SystemExit(f"{where}.{key} 有重复的项: {item}")
        seen.add(token)
        found.append(item)
    return tuple(found)


def _operator_code(code: str) -> bool:
    # 与桥 ConfigRules.OperatorCode + JsonSafe 相同：1 到 20 个字符，不含空白、控制字符、单引号、分号、双引号、反斜杠。
    if not code or len(code) > 20:
        return False
    return all(ch > " " and ch not in "';\"\\" and unicodedata.category(ch) != "Cc" for ch in code)


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""
