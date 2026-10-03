"""字段标签（meta/fields）、单据搜索（vouchers/search）、批量读取（vouchers/load_many、archives/get_many）。混入 U8CoClient。

都是读路由，要登录（账套、口令）。字段标签按账套的单据模板取，客户改过的自定义项名称、必填也会反映出来。
load_many 一次 1 到 20 张；逐张用 COM 读取的类型桥只收 5 张（400 field=ids），这一点由桥判断，客户端不另列清单。
单张失败（not_found、no_permission 等）不影响整批：items 里该项是 {"id": …, "error": {code, message}}。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from co.client.u8co_kinds import _ALL_KINDS
from co.client.u8co_gl_arc import READ_ARCHIVES, _arc_code, _common, _int, _put_optional, _text, _ymd

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

FIELD_OPS = ("create", "update", "generate")
SEARCH_TEXT = ("partner", "dept", "person", "warehouse", "maker", "inventory")
_BATCH_MAX = 20
_SEARCH_MAX = 200
_CODE_LIKE_MAX = 40
_TEXT_MAX = 60
_INT32_MAX = 2147483647
# 表头文本自定义项（桥 VoucherSearchDefines）：cDefine4–7、15–16 是日期或数字列，不能按文本搜索。
DEFINE_KEYS = tuple(f"define{n}" for n in (1, 2, 3, 8, 9, 10, 11, 12, 13, 14))
DEFINE_OPS = ("eq", "like", "prefix")
_DEFINES_MAX = 4
_DEFINE_LEN = 120


@dataclass(frozen=True)
class FieldsTarget:
    """meta/fields 的对象：kind（单据类型）、archive（档案）、gl=True（总账凭证）三选一。
    op 只给单据：create（缺省）、update、generate；generate 必须带 source（来源单据类型）。"""

    kind: str = ""
    archive: str = ""
    gl: bool = False
    op: str = ""
    source: str = ""


@dataclass(frozen=True)
class SearchQuery:
    """vouchers/search。过滤条件都可省略；partner 是该类型的客户 / 供应商 / 往来单位编码，没有这一列的类型桥 400。"""

    kind: str
    code_like: str = ""
    partner: str = ""
    dept: str = ""
    person: str = ""
    warehouse: str = ""
    maker: str = ""
    inventory: str = ""
    date_from: str = ""
    date_to: str = ""
    verified: bool | None = None
    closed: bool | None = None
    after: int | None = None
    limit: int | None = None
    # 表头自定义项条件：{"define1": "HT001"} 或 {"define1": {"like" | "prefix" | "eq": "HT"}}，1 到 4 个键。
    defines: dict[str, Any] | None = None


def _kind(kind: object) -> str:
    if kind not in _ALL_KINDS:
        raise ValueError("未知的单据类型")
    return str(kind)


def _bool(value: object, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} 必须是布尔")
    return value


def fields_body(call: U8Call, target: FieldsTarget) -> dict[str, Any]:
    chosen = [bool(target.kind), bool(target.archive), target.gl is True]
    if chosen.count(True) != 1:
        raise ValueError("kind、archive、gl 必须恰好给一个")
    fields = _common(call)
    if target.archive:
        if target.op or target.source:
            raise ValueError("档案不带 op、source")
        if target.archive not in READ_ARCHIVES:
            raise ValueError("未知的档案类型")
        fields["archive"] = target.archive
        return fields
    if target.gl:
        if target.op or target.source:
            raise ValueError("总账凭证不带 op、source")
        fields["gl"] = True
        return fields
    fields["type"] = _kind(target.kind)
    return _op_fields(fields, target)


def _op_fields(fields: dict[str, Any], target: FieldsTarget) -> dict[str, Any]:
    if target.op and target.op not in FIELD_OPS:
        raise ValueError("op 只能是 " + "、".join(FIELD_OPS))
    if (target.op == "generate") != bool(target.source):
        raise ValueError("op=generate 时必须带 source，其他操作不带")
    _put_optional(fields, "op", target.op)
    if target.source:
        fields["source"] = _kind(target.source)
    return fields


def search_body(call: U8Call, query: SearchQuery) -> dict[str, Any]:
    fields = _common(call)
    fields["type"] = _kind(query.kind)
    _put_optional(fields, "code_like", query.code_like and _text(query.code_like, "code_like", _CODE_LIKE_MAX))
    for name in SEARCH_TEXT:
        value = getattr(query, name)
        _put_optional(fields, name, value and _text(value, name, _TEXT_MAX))
    _put_optional(fields, "date_from", query.date_from and _ymd(query.date_from, "date_from"))
    _put_optional(fields, "date_to", query.date_to and _ymd(query.date_to, "date_to"))
    if query.date_from and query.date_to and query.date_from > query.date_to:
        raise ValueError("date_from 不能晚于 date_to")
    for name in ("verified", "closed"):
        value = getattr(query, name)
        if value is not None:
            fields[name] = _bool(value, name)
    _put_optional(fields, "after", None if query.after is None else _int(query.after, "after", 0, _INT32_MAX))
    _put_optional(fields, "limit", None if query.limit is None else _int(query.limit, "limit", 1, _SEARCH_MAX))
    if query.defines is not None:
        fields["defines"] = _defines(query.defines)
    return fields


def _defines(defines: object) -> dict[str, Any]:
    if not isinstance(defines, dict) or not 1 <= len(defines) <= _DEFINES_MAX:
        raise ValueError(f"defines 必须是 1 到 {_DEFINES_MAX} 个键的字典")
    for key, spec in defines.items():
        if key not in DEFINE_KEYS:
            raise ValueError("defines 的键只能是 " + "、".join(DEFINE_KEYS))
        label = f"defines.{key}"
        if isinstance(spec, dict):
            if len(spec) != 1 or next(iter(spec)) not in DEFINE_OPS:
                raise ValueError(f"{label} 必须是字符串或 {{eq | like | prefix: 字符串}}")
            spec = next(iter(spec.values()))
        # 规整同桥：全角空格、不换行空格当半角空格，去两端半角空格。
        normal = spec.replace("\u3000", " ").replace("\u00a0", " ").strip(" ") if isinstance(spec, str) else spec
        _text(normal, label, _DEFINE_LEN)
    return dict(defines)


def _batch(values: object, label: str) -> list[Any]:
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError(f"{label} 必须是数组")
    if not 1 <= len(values) <= _BATCH_MAX:
        raise ValueError(f"{label} 必须是 1 到 {_BATCH_MAX} 项")
    return list(values)


def _unique(values: list[Any], label: str) -> list[Any]:
    # 每项先校验成 int / str 再查重，免得不可哈希的值在 set() 里抛 TypeError。
    if len(set(values)) != len(values):
        raise ValueError(f"{label} 不能重复")
    return values


def load_many_body(call: U8Call, kind: str, ids: object) -> dict[str, Any]:
    fields = _common(call)
    fields["type"] = _kind(kind)
    fields["ids"] = _unique([_int(item, "ids 的每一项", 1, _INT32_MAX) for item in _batch(ids, "ids")], "ids")
    return fields


def get_many_body(call: U8Call, archive: str, codes: object) -> dict[str, Any]:
    if archive not in READ_ARCHIVES:
        raise ValueError("未知的档案类型")
    fields = _common(call)
    fields["archive"] = archive
    fields["codes"] = _unique([_arc_code(archive, item, "codes 的每一项") for item in _batch(codes, "codes")], "codes")
    return fields


class U8CoLookupMixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def meta_fields(self, call: U8Call, target: FieldsTarget) -> dict[str, Any]:
        """字段标签：head / lines（档案、总账凭证见桥的说明）每项 name、label、type、required，枚举另有 enum。"""
        return self.call("/v1/meta/fields", fields_body(call, target))

    def search(self, call: U8Call, query: SearchQuery) -> dict[str, Any]:
        """按条件找单据：items 同 vouchers/list 的行，另有 partner_name；还有下一页时带 next（传给 after）。"""
        return self.call("/v1/vouchers/search", search_body(call, query))

    def load_many(self, call: U8Call, kind: str, ids: object) -> dict[str, Any]:
        """一次读几张单据，一次登录。items 按请求顺序：vouchers/load 的正文，或 {id, error}。"""
        return self.call("/v1/vouchers/load_many", load_many_body(call, kind, ids))

    def get_many(self, call: U8Call, archive: str, codes: object) -> dict[str, Any]:
        """一次读几条档案。items 按请求顺序：archives/get 的正文，或 {code, error}。"""
        return self.call("/v1/archives/get_many", get_many_body(call, archive, codes))
