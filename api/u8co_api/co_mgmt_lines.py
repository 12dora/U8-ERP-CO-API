"""经营管理利润表的行定义（U8CO_MGMT_LINES_FILE，可选）：每一行由哪些损益科目前缀组成、按收入还是费用取数，
以及由行加减得到的派生行（毛利、营业利润、利润总额、净利润）。

没配置文件时用内置的通用定义：按 2007 新会计准则科目表的一级科目。现场的明细科目对照（例如把某个明细科目单列为
一行）只写在现场的配置文件里，不进代码库。文件有任何问题进程都起不来（同 U8CO_IC_MAP_FILE）。

文件格式：{"version": 1, "lines": [...], "derived": [...]}，不认识的键一律无效。
- lines[]：{"id", "name", "codes": {"*": ["6001"], "801": ["600111"]}, "sign": "income" | "expense"}。
  codes 的键是 "*"（缺省）或账套号；账套有自己的列表时整个替换缺省列表。科目按前缀匹配。
  income 取贷方减借方，expense 取借方减贷方。一行可以是另一行的「其中」（科目重叠），派生行只引用需要的行。
- derived[]：{"id", "name", "plus": [行 id], "minus": [行 id]}，按顺序计算，可以引用前面的派生行。
必须有 id 为 revenue（营业收入）和 cogs（营业成本）的行：合并时内部收入、成本从这两行抵销。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

INCOME = "income"
EXPENSE = "expense"
REVENUE = "revenue"
COGS = "cogs"
DEFAULT_KEY = "*"
_LIMIT = 65536
_MAX_LINES = 60
_ID = re.compile(r"^[a-z][a-z0-9_]{0,39}\Z")
_ACC = re.compile(r"^[0-9]{3}\Z")
_SUBJECT = re.compile(r"^[0-9]{1,20}\Z")
_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,40}\Z")
_TOP_KEYS = frozenset(("version", "lines", "derived"))
_LINE_KEYS = frozenset(("id", "name", "codes", "sign"))
_DERIVED_REQUIRED = frozenset(("id", "name", "plus"))
_DERIVED_KEYS = _DERIVED_REQUIRED | {"minus"}
_REQUIRED_LINES = (REVENUE, COGS)


@dataclass(frozen=True, eq=False)
class MgmtLine:
    id: str
    name: str
    codes: dict[str, tuple[str, ...]]
    sign: str

    def codes_for(self, acc: str) -> tuple[str, ...]:
        """该账套用的科目前缀：账套自己的列表，没有时用缺省（"*"）列表。"""
        return self.codes.get(acc, self.codes.get(DEFAULT_KEY, ()))


@dataclass(frozen=True, eq=False)
class MgmtDerived:
    id: str
    name: str
    plus: tuple[str, ...]
    minus: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False)
class MgmtLines:
    lines: tuple[MgmtLine, ...]
    derived: tuple[MgmtDerived, ...]
    # default 内置通用定义，file 现场配置文件。
    source: str = "default"

    def needs_leaf(self, acc: str) -> bool:
        """该账套有超过 4 位的科目前缀时要按末级科目取数（只按一级科目汇总匹配不到明细前缀）。"""
        return any(len(code) > 4 for line in self.lines for code in line.codes_for(acc))

    def ids(self) -> list[str]:
        return [line.id for line in self.lines] + [item.id for item in self.derived]

    def name_of(self, line_id: str) -> str:
        for item in (*self.lines, *self.derived):
            if item.id == line_id:
                return item.name
        return line_id


def _line(line_id: str, name: str, codes: tuple[str, ...], sign: str) -> MgmtLine:
    return MgmtLine(line_id, name, {DEFAULT_KEY: codes}, sign)


DEFAULT_LINES = MgmtLines(
    lines=(
        _line(REVENUE, "营业收入", ("6001", "6051"), INCOME),
        _line(COGS, "营业成本", ("6401", "6402"), EXPENSE),
        _line("tax", "税金及附加", ("6403",), EXPENSE),
        _line("selling", "销售费用", ("6601",), EXPENSE),
        _line("admin", "管理费用", ("6602",), EXPENSE),
        _line("finance", "财务费用", ("6603",), EXPENSE),
        _line("impairment", "资产减值损失", ("6701", "6702"), EXPENSE),
        _line("fair_value", "公允价值变动收益", ("6101",), INCOME),
        _line("invest", "投资收益", ("6111",), INCOME),
        _line("other_gain", "其他收益", ("6117", "6115"), INCOME),
        _line("nonop_in", "营业外收入", ("6301",), INCOME),
        _line("nonop_out", "营业外支出", ("6711",), EXPENSE),
        _line("income_tax", "所得税费用", ("6801",), EXPENSE),
    ),
    derived=(
        MgmtDerived("gross_profit", "毛利", (REVENUE,), (COGS,)),
        MgmtDerived(
            "operating_profit",
            "营业利润",
            ("gross_profit", "fair_value", "invest", "other_gain"),
            ("tax", "selling", "admin", "finance", "impairment"),
        ),
        MgmtDerived("total_profit", "利润总额", ("operating_profit", "nonop_in"), ("nonop_out",)),
        MgmtDerived("net_profit", "净利润", ("total_profit",), ("income_tax",)),
    ),
)


def _fail(message: str) -> SystemExit:
    return SystemExit(f"U8CO_MGMT_LINES_FILE: {message}")


def load_mgmt_lines(path: str) -> MgmtLines:
    """没设路径时用内置定义；设了就严格校验，任何问题都让进程起不来。"""
    if not path:
        return DEFAULT_LINES
    return parse_mgmt_lines(_json(path))


def _json(path: str) -> Any:
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read(_LIMIT + 1)
    except (OSError, UnicodeDecodeError) as exc:
        raise _fail("文件读不到") from exc
    if len(text) > _LIMIT:
        raise _fail("文件超过 64 KiB")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise _fail(f"不是合法的 JSON（第 {exc.lineno} 行）") from exc


def parse_mgmt_lines(data: object) -> MgmtLines:
    """校验已解析的 JSON（load_mgmt_lines 和测试共用）。"""
    top = _keys("顶层", data, _TOP_KEYS, _TOP_KEYS)
    if top["version"] != 1 or isinstance(top["version"], bool):
        raise _fail("version 只能是 1")
    raw_lines = top["lines"]
    if not isinstance(raw_lines, list) or not raw_lines or len(raw_lines) > _MAX_LINES:
        raise _fail(f"lines 必须是 1 到 {_MAX_LINES} 项的列表")
    lines = tuple(_parse_line(f"lines[{index}]", item) for index, item in enumerate(raw_lines))
    seen = [line.id for line in lines]
    if len(set(seen)) != len(seen):
        raise _fail("lines 的 id 有重复")
    for required in _REQUIRED_LINES:
        if required not in seen:
            raise _fail(f"lines 必须有 id 为 {required} 的行")
    raw_derived = top["derived"]
    if not isinstance(raw_derived, list) or len(raw_derived) > _MAX_LINES:
        raise _fail(f"derived 必须是最多 {_MAX_LINES} 项的列表")
    derived: list[MgmtDerived] = []
    for index, item in enumerate(raw_derived):
        found = _parse_derived(f"derived[{index}]", item, seen)
        seen.append(found.id)
        derived.append(found)
    return MgmtLines(lines, tuple(derived), "file")


def _keys(where: str, raw: object, required: frozenset[str], allowed: frozenset[str]) -> dict:
    if not isinstance(raw, dict):
        raise _fail(f"{where} 必须是对象")
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _fail(f"{where} 有不认识的键: {', '.join(unknown)}")
    absent = sorted(required - set(raw))
    if absent:
        raise _fail(f"{where} 缺少: {', '.join(absent)}")
    return raw


def _text(where: str, raw: object, pattern: re.Pattern[str], what: str) -> str:
    if not isinstance(raw, str) or pattern.fullmatch(raw) is None:
        raise _fail(f"{where} {what}")
    return raw


def _parse_line(where: str, raw: object) -> MgmtLine:
    data = _keys(where, raw, _LINE_KEYS, _LINE_KEYS)
    line_id = _text(f"{where}.id", data["id"], _ID, "必须是小写字母开头的 1 到 40 个小写字母、数字或下划线")
    name = _text(f"{where}.name", data["name"], _NAME, "必须是 1 到 40 个字符、不含控制字符")
    if data["sign"] not in (INCOME, EXPENSE):
        raise _fail(f"{where}.sign 只能是 income 或 expense")
    return MgmtLine(line_id, name, _codes(f"{where}.codes", data["codes"]), data["sign"])


def _codes(where: str, raw: object) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, dict) or not raw:
        raise _fail(f"{where} 必须是非空对象（\"*\" 或账套号 → 科目前缀列表）")
    found: dict[str, tuple[str, ...]] = {}
    for key, codes in raw.items():
        if key != DEFAULT_KEY and _ACC.fullmatch(key) is None:
            raise _fail(f"{where} 的键只能是 \"*\" 或三位数字的账套号: {key!r}")
        if not isinstance(codes, list) or not codes:
            raise _fail(f"{where}.{key} 必须是非空的科目前缀列表")
        items = tuple(_text(f"{where}.{key}", code, _SUBJECT, "里的科目前缀必须是 1 到 20 位数字") for code in codes)
        if len(set(items)) != len(items):
            raise _fail(f"{where}.{key} 有重复的科目前缀")
        found[key] = items
    return found


def _parse_derived(where: str, raw: object, known: list[str]) -> MgmtDerived:
    data = _keys(where, raw, _DERIVED_REQUIRED, _DERIVED_KEYS)
    item_id = _text(f"{where}.id", data["id"], _ID, "必须是小写字母开头的 1 到 40 个小写字母、数字或下划线")
    if item_id in known:
        raise _fail(f"{where}.id {item_id} 与前面的行重复")
    name = _text(f"{where}.name", data["name"], _NAME, "必须是 1 到 40 个字符、不含控制字符")
    plus = _refs(f"{where}.plus", data["plus"], known, allow_empty=False)
    minus = _refs(f"{where}.minus", data.get("minus", []), known, allow_empty=True)
    return MgmtDerived(item_id, name, plus, minus)


def _refs(where: str, raw: object, known: list[str], *, allow_empty: bool) -> tuple[str, ...]:
    if not isinstance(raw, list) or (not raw and not allow_empty):
        raise _fail(f"{where} 必须是{'' if allow_empty else '非空'}的行 id 列表")
    for item in raw:
        if not isinstance(item, str) or item not in known:
            raise _fail(f"{where} 引用的 {item!r} 不是前面定义过的行")
    return tuple(raw)
