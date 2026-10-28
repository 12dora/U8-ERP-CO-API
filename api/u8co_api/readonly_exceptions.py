"""只读例外（U8CO_READONLY_EXCEPTIONS）：只读账套上允许穿过闸门的精确写入。

JSON 数组，每项 accounts / operators / type / op，archives 的 create、delete 另有 archives。
type 只能是 archives 或 vouchers。
type=archives：op 只能是 customer_person、customer_fields、customer_address、customer_address_add、
customer_address_delete（客户主数据的受控 SQL 写入）、create 或 delete；请求路径必须恰好是 archives/<op>。
op=create、delete 必须写 archives：非空的档案种类列表，每项都在代码里该 op 的可开放集合内
（create：district_class、customer；delete：只有 district_class），放行时请求体的 archive 须在列表里；
其它 op 不能写 archives。地区分类的 delete 与 create 成对开放，用来回滚刚新增的档案；客户只开放新增
（EAI 新增是 INSERT，不经清银行账号的 diffedit），不开放删除，新增错的客户用 customer_fields 写停用日期；
customer_address_delete 与 customer_address_add 同理成对。
type=vouchers：op 只能是 head_fix（历史发货单表头修正），请求路径必须恰好是 vouchers/head_fix，也不能写 archives。
账套必须是 U8CO_READONLY_ACCOUNTS 的子集。未知键、其它 type/op、不合规的操作员编码一律让进程起不来。
操作员比较不区分大小写，不去首尾空白。archives/update（含客户）、其它档案的 create / delete、客户的 delete、
vouchers/update 等其余写路由在只读账套上仍一律 403。
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass

ENV = "U8CO_READONLY_EXCEPTIONS"
_KEYS = frozenset(("accounts", "operators", "type", "op", "archives"))
_REQUIRED = ("accounts", "operators", "type", "op")
_ARCHIVES = "archives"
_VOUCHERS = "vouchers"
_CREATE = "create"
_DELETE = "delete"
# 必须带 archives 的 op：新增与删除成对开放。
_ARCHIVE_OPS = (_CREATE, _DELETE)
_OPS = {
    _ARCHIVES: (
        "customer_person",
        "customer_fields",
        "customer_address",
        "customer_address_add",
        "customer_address_delete",
        *_ARCHIVE_OPS,
    ),
    _VOUCHERS: ("head_fix",),
}
# type → 请求动作前缀：路径必须恰好是 <前缀><op>。
_PREFIX = {_ARCHIVES: "co:archives/", _VOUCHERS: "co:vouchers/"}
# op 为 create、delete 时 archives 可以列出的档案种类（代码里的封闭集合，配置只能从中选）。与桥
# ConfigRulesReadOnly.EligibleArchives / EligibleDelete 一致。客户不能删、update 不能开放。
CREATE_ARCHIVES = frozenset(("district_class", "customer"))
DELETE_ARCHIVES = frozenset(("district_class",))
_ELIGIBLE = {_CREATE: CREATE_ARCHIVES, _DELETE: DELETE_ARCHIVES}
_SIZE = 65536
_ACCOUNT = re.compile(r"^[0-9]{3}\Z")


@dataclass(frozen=True)
class ReadOnlyException:
    """一条只读例外。operators 保留配置原文，匹配时操作员不区分大小写。"""

    accounts: tuple[str, ...]
    operators: tuple[str, ...]
    type: str
    op: str
    # 只有 op 为 create、delete 时有：允许写入的档案种类。
    archives: tuple[str, ...] | None = None

    def matches(self, acc: str, operator: str) -> bool:
        if acc not in self.accounts:
            return False
        who = operator.casefold()
        return who in {item.casefold() for item in self.operators}

    def covers(self, payload: dict) -> bool:
        """op 须是该 type 可开放的 op（直接构造的 op=update 等一律不放行）；op 为 create、delete 时请求体的 archive
        须在 archives 里；其它 op 没有这一层限制。"""
        if self.op not in _OPS.get(self.type, ()):
            return False
        if self.type != _ARCHIVES or self.op not in _ARCHIVE_OPS:
            return True
        # 解析器保证这两个 op 一定有 archives 且都可开放；对象被直接构造成没有、或列了不可开放的种类时，一律不放行。
        archive = _text(payload.get("archive"))
        return bool(self.archives) and archive in self.archives and archive in _ELIGIBLE[self.op]

    def summary(self) -> dict:
        found = {
            "accounts": list(self.accounts),
            "operators": list(self.operators),
            "type": self.type,
            "op": self.op,
        }
        if self.archives is not None:
            found["archives"] = list(self.archives)
        return found


def parse_exceptions(raw: str, read_only: tuple[str, ...]) -> tuple[ReadOnlyException, ...]:
    """解析环境变量。空串为未配置；无效内容让进程起不来。"""
    if not raw:
        return ()
    data = _json(raw)
    if not isinstance(data, list):
        raise SystemExit(f"{ENV} 必须是 JSON 数组")
    return tuple(_one(f"{ENV}[{index}]", item, read_only) for index, item in enumerate(data))


def allows(exceptions: tuple[ReadOnlyException, ...], action: str, payload: dict) -> bool:
    """路由必须恰好是 archives/<op> 或 vouchers/head_fix，且任一同 type、op 例外的账套、操作员命中才放行。
    archives 的 create、delete 另看请求体 archive。"""
    if not exceptions:
        return False
    acc = _text(payload.get("acc"))
    operator = _text(payload.get("operator"))
    return any(
        action == _PREFIX.get(item.type, "") + item.op and item.matches(acc, operator) and item.covers(payload)
        for item in exceptions
    )


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
        archives=_archives(where, type_, op, raw),
    )


def _kind(where: str, type_: object, op: object) -> tuple[str, str]:
    if not isinstance(type_, str) or type_ not in _OPS:
        raise SystemExit(f"{where}.type 只能是 {'、'.join(_OPS)}")
    if op not in _OPS[type_]:
        raise SystemExit(f"{where}.op 在 type={type_} 时只能是 {'、'.join(_OPS[type_])}")
    return type_, str(op)


def _archives(where: str, type_: str, op: str, item: dict) -> tuple[str, ...] | None:
    # type=archives 且 op 为 create、delete 时必须列出档案种类，且都在代码里的可开放集合内；
    # 其它 type、op 不能写这个键（null 也不行）。
    if type_ != _ARCHIVES or op not in _ARCHIVE_OPS:
        if "archives" in item:
            raise SystemExit(f"{where}.archives 只能用于 type=archives 的 op=create 或 delete")
        return None
    if "archives" not in item:
        raise SystemExit(f"{where}.archives 在 op={op} 时必须给出")
    found = _strings(where, item["archives"], "archives", False)
    eligible = _ELIGIBLE[op]
    for kind in found:
        if kind not in eligible:
            allowed = "、".join(sorted(eligible))
            raise SystemExit(f"{where}.archives 里的 {kind!r} 不能开放 op={op}（目前只有 {allowed}）")
    return found


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
