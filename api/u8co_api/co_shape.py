"""响应裁剪：查询参数 fields（投影）和 compact（去空值），只作用在自由格式的容器上。

容器：head（dict）、lines（dict 列表）、items（dict 列表）、fields（档案 get 的 dict）、
docs[].head、docs[].lines（预演）。信封里的其它键（ok、type、id、code、next……）一概不动。

fields 规则：
- 逗号分隔，最多 100 项，每项是 [A-Za-z0-9_]+，可带前缀 head. lines. items. fields. docs.head. docs.lines.
- 不带前缀的名字作用于所有容器；带前缀的只作用于那个容器（head. 只管顶层 head，docs.head. 只管 docs[].head）。
- 大小写不敏感。
- 某个容器没有任何适用的名字（没有裸名字，也没有它的前缀）时原样保留；
  有适用的名字但一个都不存在时变成空容器（不删除）。
- items 的每一行投影时总是保留 id、code、error、ok、masked_fields（有的话）。
compact=true：在这些容器里删掉值为 null、""（去空白后）、[]、{} 的键；0 和 false 保留。
纯函数：不改动传入的 dict。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from u8co_api.errors import bad_request

_MAX_ENTRIES = 100
_NAME = re.compile(r"[A-Za-z0-9_]+")
# 前缀按长度从长到短匹配，docs.head. 先于 head.。
_PREFIXES = ("docs.head", "docs.lines", "head", "lines", "items", "fields")
_TOP = ("head", "lines", "items", "fields")
# 批量读（load_many / get_many）的 items 行可能是错误条目 {id|code, error}：投影永远保留这几个键，
# 免得失败被裁掉；compact 仍按空值规则处理。批量读的每项另带 masked_fields（字段权限遮蔽），同样保留。
_ITEM_KEEP = frozenset({"id", "code", "error", "ok", "masked_fields"})


@dataclass(frozen=True)
class FieldSpec:
    """解析后的 fields：bare 作用于所有容器，by 按容器名（head、docs.head……）。名字都已转小写。"""

    bare: frozenset[str] = frozenset()
    by: dict[str, frozenset[str]] = field(default_factory=dict)

    def names_for(self, container: str) -> frozenset[str] | None:
        """容器适用的名字集合；None 表示这个容器不投影。"""
        own = self.by.get(container, frozenset())
        if not self.bare and not own:
            return None
        return self.bare | own


def _bad(message: str):
    return bad_request(message, field="fields")


def _split(entry: str) -> tuple[str | None, str]:
    for prefix in _PREFIXES:
        head = prefix + "."
        if entry.lower().startswith(head):
            return prefix, entry[len(head) :]
    return None, entry


def parse_fields(text: str | None) -> FieldSpec | None:
    """解析查询参数 fields；None 表示不投影。语法错误抛 400 bad_request（field=fields）。"""
    if text is None:
        return None
    entries = [part.strip() for part in text.split(",")]
    if not text.strip() or any(not part for part in entries):
        raise _bad("fields 不能有空项")
    if len(entries) > _MAX_ENTRIES:
        raise _bad(f"fields 最多 {_MAX_ENTRIES} 项")
    bare: set[str] = set()
    by: dict[str, set[str]] = {}
    for entry in entries:
        container, name = _split(entry)
        if not _NAME.fullmatch(name):
            raise _bad(f"fields 项无效：{entry[:40]}")
        if container is None:
            bare.add(name.lower())
        else:
            by.setdefault(container, set()).add(name.lower())
    return FieldSpec(frozenset(bare), {key: frozenset(value) for key, value in by.items()})


def _empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return isinstance(value, (list, dict)) and not value


def _row(row: dict, names: frozenset[str] | None, compact: bool) -> dict:
    return {
        key: value
        for key, value in row.items()
        if (names is None or str(key).lower() in names) and not (compact and _empty(value))
    }


def _container(value: object, names: frozenset[str] | None, compact: bool) -> object:
    # dict 容器整体裁剪；dict 列表逐行裁剪；其它形状（非 dict 的行、标量）原样保留。
    if isinstance(value, dict):
        return _row(value, names, compact)
    if isinstance(value, list):
        return [_row(row, names, compact) if isinstance(row, dict) else row for row in value]
    return value


def _doc(doc: object, spec: FieldSpec | None, compact: bool) -> object:
    if not isinstance(doc, dict):
        return doc
    out = dict(doc)
    for key in ("head", "lines"):
        if key in out:
            names = spec.names_for("docs." + key) if spec else None
            out[key] = _container(out[key], names, compact)
    return out


def shape(result: dict, fields: str | None, compact: bool) -> dict:
    """按 fields / compact 裁剪桥的成功响应。两者都没给时原样返回同一个对象。"""
    spec = parse_fields(fields)
    if spec is None and not compact:
        return result
    out = dict(result)
    for key in _TOP:
        if key in out:
            names = spec.names_for(key) if spec else None
            if key == "items" and names is not None:
                names = names | _ITEM_KEEP
            out[key] = _container(out[key], names, compact)
    docs = out.get("docs")
    if isinstance(docs, list):
        out["docs"] = [_doc(doc, spec, compact) for doc in docs]
    return out
