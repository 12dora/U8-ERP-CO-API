"""用例的纯判断（不连服务器、不引用 steps）：字段标签的结构、meta 可写字段展开、搜索与列表的对照、批量读取的逐项核对。

标签文字来自账套模板，这里只核对结构（name / label / type / required / enum），不写死任何标签文字。
"""

from __future__ import annotations

import re

from co.e2e.casespec.dry_judge import first_diff

FIELD_TYPES = ("bool", "string", "int", "decimal", "date", "enum", "array")
_REVISION = re.compile(r"^[0-9a-f]{64}\Z")
# 响应里可能放字段行的键：单据 head / lines，档案 fields（或 head），总账凭证 head / lines（或 line）/ cash_flow。
ROW_KEYS = ("head", "lines", "line", "fields", "cash_flow")


def expand_side(side: object) -> set[str]:
    """meta kinds[].writable 的一侧 {exact, spans} → 小写字段名集合；null（该操作不收这一侧）→ 空集合。"""
    if not isinstance(side, dict):
        return set()
    names = {str(name).lower() for name in side.get("exact") or []}
    for span in side.get("spans") or []:
        prefix, low, high = span.get("prefix"), span.get("from"), span.get("to")
        if isinstance(prefix, str) and isinstance(low, int) and isinstance(high, int):
            names.update(f"{prefix.lower()}{index}" for index in range(low, high + 1))
    return names


def _named(rows: object, name: str) -> dict:
    for row in rows if isinstance(rows, list) else []:
        if isinstance(row, dict) and row.get("name") == name:
            return row
    return {}


def kind_writable(meta: dict, kind: str, op: str = "create") -> dict:
    """meta 里该类型该操作的 writable 规格（create / update）；找不到或不支持时返回空字典。"""
    spec = _named(meta.get("kinds"), kind).get("writable")
    found = spec.get(op) if isinstance(spec, dict) else None
    return found if isinstance(found, dict) else {}


def unsupported_op(meta: dict) -> tuple[str, str]:
    """挑一个 writable.update 为 null 的类型（该类型不支持修改），用来测 op 不支持时 400 field=op。"""
    for row in meta.get("kinds") or []:
        spec = row.get("writable") if isinstance(row, dict) else None
        if isinstance(spec, dict) and spec.get("create") is not None and spec.get("update") is None:
            return str(row.get("name") or ""), "update"
    return "", ""


def archive_writable(meta: dict, archive: str) -> set[str]:
    return {str(tag).lower() for tag in _named(meta.get("archives"), archive).get("writable") or []}


def gl_names(meta: dict) -> set[str]:
    gl = meta.get("gl") if isinstance(meta.get("gl"), dict) else {}
    return {str(name).lower() for key in ("head", "line", "cash_flow") for name in gl.get(key) or []}


def gl_required(meta: dict) -> set[str]:
    gl = meta.get("gl") if isinstance(meta.get("gl"), dict) else {}
    return {str(name).lower() for key in ("required_head", "required_line") for name in gl.get(key) or []}


def rows_of(payload: dict, keys: tuple[str, ...] = ROW_KEYS) -> list:
    rows: list = []
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend(value)
    return rows


def names_of(rows: list) -> set[str]:
    return {str(row.get("name") or "").lower() for row in rows if isinstance(row, dict)}


def _enum_problem(row: dict) -> str:
    items = row.get("enum")
    if row.get("type") != "enum":
        return "" if items is None else f"{row.get('name')} 不是 enum 却带 enum"
    if not isinstance(items, list) or not items:
        return f"{row.get('name')} 是 enum 但没有取值"
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("code"), str) or not isinstance(item.get("name"), str):
            return f"{row.get('name')} 的 enum 项要有 code、name"
    return ""


def row_problem(row: object) -> str:
    """一行字段：name 非空、label 为字符串或 null、type 在词表里或 null、required 是布尔，enum 只在 type=enum 时出现。"""
    if not isinstance(row, dict) or not isinstance(row.get("name"), str) or not row["name"]:
        return "字段行缺 name"
    name = row["name"]
    if not (row.get("label") is None or isinstance(row.get("label"), str)):
        return f"{name} 的 label 不是字符串"
    if not (row.get("type") is None or row.get("type") in FIELD_TYPES):
        return f"{name} 的 type={row.get('type')!r} 不在词表里"
    if not isinstance(row.get("required"), bool):
        return f"{name} 的 required 不是布尔"
    length = row.get("max_length")
    if length is not None and (not isinstance(length, int) or isinstance(length, bool) or length <= 0):
        return f"{name} 的 max_length 不是正整数"
    return _enum_problem(row)


def fields_problem(payload: dict, want: set[str], required: set[str], keys: tuple[str, ...] = ROW_KEYS) -> str:
    """meta/fields 的响应：ok、fields_revision、每行结构、名字集合恰好是 want、required 里的都标 required。"""
    if payload.get("ok") is not True:
        return "ok 不是 true"
    if _REVISION.fullmatch(str(payload.get("fields_revision") or "")) is None:
        return "fields_revision 不是 64 位十六进制"
    rows = rows_of(payload, keys)
    for row in rows:
        problem = row_problem(row)
        if problem:
            return problem
    got = names_of(rows)
    if got != want:
        extra, lost = sorted(got - want)[:5], sorted(want - got)[:5]
        return f"字段名与 meta 可写字段不同：多 {extra} 少 {lost}"
    loose = sorted(name for name in required if not _row_required(rows, name))[:5]
    return f"meta 必填的字段没有标 required：{loose}" if loose else ""


def _row_required(rows: list, name: str) -> bool:
    return any(isinstance(row, dict) and str(row.get("name")).lower() == name and row.get("required") is True for row in rows)


def labelled(rows: list) -> int:
    return sum(1 for row in rows if isinstance(row, dict) and isinstance(row.get("label"), str) and row["label"])


def item_ids(payload: dict) -> list[int]:
    items = payload.get("items")
    return [row.get("id") for row in items if isinstance(row, dict)] if isinstance(items, list) else []


def page_problem(search: dict, listed: dict, label: str) -> str:
    """同一过滤条件、同一 limit：搜索和列表的第一页主键相同，是否还有下一页也相同。"""
    got, want = item_ids(search), item_ids(listed)
    if got != want:
        return f"{label}：搜索 {got[:6]} ≠ 列表 {want[:6]}"
    if (search.get("next") is None) != (listed.get("next") is None):
        return f"{label}：搜索 next={search.get('next')}，列表 next={listed.get('next')}"
    return ""


def same_as_list(search_item: dict, list_item: dict) -> str:
    """搜索的行 = 列表的行（同一 id 的各键值相同）+ partner_name。"""
    for key, value in list_item.items():
        if key not in search_item:
            return f"搜索的行缺 {key}"
        if search_item[key] != value:
            return f"搜索的行 {key}={search_item[key]!r}，列表是 {value!r}"
    return "" if "partner_name" in search_item else "搜索的行缺 partner_name"


def strip_ok(body: object) -> object:
    return {key: value for key, value in body.items() if key != "ok"} if isinstance(body, dict) else body


def batch_problem(items: object, singles: list[tuple[object, object]], id_key: str, missing: str = "not_found") -> str:
    """load_many / get_many 的 items 与逐个请求对照。singles 按请求顺序，每项（id 或编码, 单个读取的正文；None 表示该缺，
    items 里应是 {id_key: …, error: {code: missing}}）。"""
    if not isinstance(items, list) or len(items) != len(singles):
        return f"items 应有 {len(singles)} 项"
    for index, (item, (want_key, single)) in enumerate(zip(items, singles)):
        if not isinstance(item, dict):
            return f"items.{index} 不是对象"
        if single is None:
            error = item.get("error")
            if item.get(id_key) != want_key or not isinstance(error, dict):
                return f"items.{index} 应是 {id_key}={want_key} 的错误项"
            if error.get("code") != missing:
                return f"items.{index} error.code={error.get('code')}，期望 {missing}"
            continue
        diff = first_diff(strip_ok(single), strip_ok(item))
        if diff:
            return f"items.{index}（{id_key}={want_key}）与单个读取不同：{diff}"
    return ""
