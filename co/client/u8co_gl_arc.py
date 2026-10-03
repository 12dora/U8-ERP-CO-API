"""客户端方法：总账凭证、基础档案、单据列表、现存量。混入 U8CoClient。"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from co.client.u8co_fa_write import (  # 固定资产卡片、设备台账
    EQ_ARCHIVE,
    EQ_CODE_LIMIT,
    FA_DELETE_ARCHIVES,
    FA_WRITE_ARCHIVES,
    NO_TEMPLATE_ARCHIVES,
    NO_UPDATE_TEXT,
)
from co.client.u8co_idem import with_key  # 幂等键
from co.client.u8co_kinds import _LIST_KINDS  # 单据类型加票据
from co.client.u8co_partner import PARTNER_ARCHIVES, PARTNER_CODE_LIMIT, PARTNER_WRITE_ARCHIVES  # 客户、供应商子档案

if TYPE_CHECKING:
    from co.client.u8co_client import U8Call

GL_OPS = ("void", "unvoid", "verify", "unverify", "sign", "unsign", "delete")
GL_STATES = ("all", "unaudited", "audited", "posted", "void")
ARCHIVES = (
    "customer", "vendor", "inventory", "department", "person", "warehouse",
    "customer_class", "vendor_class", "inventory_class",
)
# 只读档案：只有 arc-get、arc-list。project 的编码写成 <项目大类>:<项目编码>。
# 只读档案；两列主键的（PAIR_ARCHIVES）编码写成 <第一段>:<第二段>。
RO6_ARCHIVES = (
    "position", "rd_style", "purchase_type", "sale_type", "district_class", "trade_class", "aa_bank",
    "customer_address", "user_define", "customer_inventory",
)
PAIR_ARCHIVES = ("customer_address", "user_define", "customer_inventory", *PARTNER_ARCHIVES)
# 汇率：编码 <币种>:<年度>:<期间>[:<日>]，arc-get 也收 <币种>:<yyyy-mm-dd>；arc-list 可按 currency、fiscal_year 过滤。
# 可写：fields 收 rate（记账汇率或浮动汇率）、adjust_rate（调整汇率），细的校验在 API 和桥。
EXCH_ARCHIVE = "exchange_rate"
# 固定资产卡片：编码是卡片编号（最长 20）；arc-list 可按 type_code（含下级类别）、dept_code、include_disposed 过滤。
FA_ARCHIVE = "fa_card"
# U8 操作员、角色：编码最长 20，只列本账套有授权的，只有账套主管能读。
UA_ARCHIVES = ("operator", "role")
# 原因码：走 EAI，可新增、修改、删除，get、list 按 EAI 标签返回；fields 收 name、Reasontype（新增必填）、ReasonMemo。
REASON_ARCHIVE = "reason"
READ_ARCHIVES = (
    *ARCHIVES,
    REASON_ARCHIVE,
    "account", "unit", "unit_group", "settle_style", "voucher_sign", "currency", "bank", "project",
    EXCH_ARCHIVE,
    FA_ARCHIVE,
    *UA_ARCHIVES,
    *RO6_ARCHIVES, *PARTNER_ARCHIVES,
    EQ_ARCHIVE,
)
# 编码（含 after、code_prefix）的最大长度，其余 60。细的校验（每类长度、两段各自长度）在 API 和桥。
_CODE_LIMIT = {
    "project": 63, "customer_address": 51, "user_define": 411, "customer_inventory": 81, EXCH_ARCHIVE: 27, FA_ARCHIVE: 20,
    "operator": 20, "role": 20, **PARTNER_CODE_LIMIT, EQ_ARCHIVE: EQ_CODE_LIMIT,
}
# 开户银行可新增、修改、删除；项目走受控 SQL，可新增、修改，没有被引用的项目可以删除。
# 货位、计量单位、自定义项档案、客户存货对照走 EAI，可新增、删除（两列主键的不收 template）；
# 自定义项档案、客户存货对照 U8 不提供修改，arc-update 只收 UPDATE_ARCHIVES。
EAI_ITEM_ARCHIVES = ("position", "unit", "user_define", "customer_inventory")
# 之后又走 EAI、可新增、修改、删除的七类。
EAI_CLASS_ARCHIVES = ("unit_group", "settle_style", "rd_style", "purchase_type", "sale_type", "district_class", "aa_bank")
# 币种（fields 的 code 是币种符号）、凭证类别（新增要 type_name，修改只收 type_name）。会计科目仍只读。
GL_ARCHIVES = ("currency", "voucher_sign")
# 新增走 EAI、修改删除由桥直接改表的几类：币种、凭证类别，子档案，汇率。
_SQL_RW = (*GL_ARCHIVES, *PARTNER_WRITE_ARCHIVES, EXCH_ARCHIVE)
WRITE_ARCHIVES = (*ARCHIVES, "bank", "project", *EAI_ITEM_ARCHIVES, *EAI_CLASS_ARCHIVES, *_SQL_RW, REASON_ARCHIVE, *FA_WRITE_ARCHIVES)
UPDATE_ARCHIVES = (*ARCHIVES, "bank", "project", "position", "unit", *EAI_CLASS_ARCHIVES, *_SQL_RW, REASON_ARCHIVE)
DELETE_ARCHIVES = (*ARCHIVES, "bank", "project", *EAI_ITEM_ARCHIVES, *EAI_CLASS_ARCHIVES, *_SQL_RW, REASON_ARCHIVE, *FA_DELETE_ARCHIVES)
_PROJECT_CLASS = re.compile(r"^[0-9A-Za-z]{1,2}\Z")
# 单据列表里取布尔值的过滤键，其余都是字符串。
BOOL_FILTERS = ("verified", "closed", "red")

_INT32_MAX = 2147483647
_YMD = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
_UFTS = re.compile(r"^\d{1,19}\Z")
_SCALAR = (str, int, float, bool)


@dataclass(frozen=True)
class GlKey:
    """已有凭证：会计期间、凭证类别字、凭证号。"""

    period: int
    sign: str
    no: int


@dataclass(frozen=True)
class GlDraft:
    """新建或整张替换凭证。head 是对象，lines 是 2 到 200 条分录。"""

    head: Any
    lines: Any


@dataclass(frozen=True)
class GlQuery:
    period_from: int
    period_to: int
    sign: str = ""
    date_from: str = ""
    date_to: str = ""
    maker: str = ""
    state: str = ""
    after: str = ""
    limit: int | None = None


@dataclass(frozen=True)
class ArcRecord:
    """档案写入。fields 的键是 EAI 标签名，值为 None 的不发给 U8。"""

    archive: str
    code: str
    fields: Any = field(default_factory=dict)
    template: str = ""


@dataclass(frozen=True)
class ArcQuery:
    archive: str
    code_prefix: str = ""
    name_like: str = ""
    changed_since: str = ""
    after: str = ""
    limit: int | None = None
    project_class: str = ""
    currency: str = ""
    fiscal_year: int | None = None
    type_code: str = ""
    dept_code: str = ""
    include_disposed: bool | None = None
    # 每项只返回 code、ufts（删除扫描）。
    keys_only: bool = False


@dataclass(frozen=True)
class VoucherQuery:
    """单据列表。filter 的键见桥的列表设计，after 是上一页的 next（整数主键）。"""

    kind: str
    filter: Any = None
    keys_only: bool = False
    changed_since: str = ""
    after: int | None = None
    limit: int | None = None


@dataclass(frozen=True)
class StockQuery:
    wh: str = ""
    inv: str = ""
    batch: str = ""
    after: int | None = None
    limit: int | None = None


class U8CoP4Mixin:
    def call(self, route: str, fields: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def gl_load(self, call: U8Call, key: GlKey) -> dict[str, Any]:
        return self.call("/v1/gl/vouchers/load", _gl_keyed(call, key))

    def gl_list(self, call: U8Call, query: GlQuery) -> dict[str, Any]:
        return self.call("/v1/gl/vouchers/list", _gl_list_fields(call, query))

    def gl_create(self, call: U8Call, draft: GlDraft, idempotency_key: str | None = None) -> dict[str, Any]:
        fields = _common(call)
        fields["head"] = _gl_head(draft.head)
        fields["lines"] = _gl_lines(draft.lines)
        return self.call("/v1/gl/vouchers/create", with_key(fields, idempotency_key))

    def gl_update(self, call: U8Call, key: GlKey, draft: GlDraft) -> dict[str, Any]:
        fields = _gl_keyed(call, key)
        fields["head"] = _gl_head(draft.head)
        fields["lines"] = _gl_lines(draft.lines)
        return self.call("/v1/gl/vouchers/update", fields)

    def gl_op(self, call: U8Call, op: str, key: GlKey) -> dict[str, Any]:
        # 作废、审核、出纳签字、删除都只带凭证键。
        if op not in GL_OPS:
            raise ValueError("不支持的凭证操作")
        return self.call("/v1/gl/vouchers/" + op, _gl_keyed(call, key))

    def arc_get(self, call: U8Call, archive: str, code: str) -> dict[str, Any]:
        return self.call("/v1/archives/get", _arc_keyed(call, archive, code, READ_ARCHIVES))

    def arc_list(self, call: U8Call, query: ArcQuery) -> dict[str, Any]:
        return self.call("/v1/archives/list", _arc_list_fields(call, query))

    def arc_create(self, call: U8Call, record: ArcRecord, idempotency_key: str | None = None) -> dict[str, Any]:
        fields = _arc_keyed(call, record.archive, record.code, WRITE_ARCHIVES)
        fields["fields"] = _arc_fields(record.fields, False)
        if record.template:
            if record.archive in (*PAIR_ARCHIVES, *GL_ARCHIVES, EXCH_ARCHIVE, *NO_TEMPLATE_ARCHIVES):
                raise ValueError("该档案不支持 template")
            fields["template"] = _text(record.template, "template", 60)
        return self.call("/v1/archives/create", with_key(fields, idempotency_key))

    def arc_update(self, call: U8Call, record: ArcRecord) -> dict[str, Any]:
        if record.template:
            raise ValueError("修改档案不能带 template")
        if record.archive in WRITE_ARCHIVES and record.archive not in UPDATE_ARCHIVES:
            raise ValueError(NO_UPDATE_TEXT.get(record.archive, "该档案不支持修改（U8 不提供修改），请删除后重新新增"))
        fields = _arc_keyed(call, record.archive, record.code, UPDATE_ARCHIVES)
        fields["fields"] = _arc_fields(record.fields, True)
        return self.call("/v1/archives/update", fields)

    def arc_delete(self, call: U8Call, archive: str, code: str) -> dict[str, Any]:
        return self.call("/v1/archives/delete", _arc_keyed(call, archive, code, DELETE_ARCHIVES))

    def list_vouchers(self, call: U8Call, query: VoucherQuery) -> dict[str, Any]:
        return self.call("/v1/vouchers/list", _list_fields(call, query))

    def stock_current(self, call: U8Call, query: StockQuery) -> dict[str, Any]:
        return self.call("/v1/stock/current", _stock_fields(call, query))


def _common(call: U8Call) -> dict[str, Any]:
    # 与单据路由相同的公共字段和顺序。password 由 call() 换成 password_enc。
    return {
        "acc": call.acc,
        "year": call.year,
        "operator": call.operator,
        "password": call.password,
        "date": call.date,
    }


def _text(value: object, label: str, max_len: int) -> str:
    if type(value) is not str or not 1 <= len(value) <= max_len:
        raise ValueError(f"{label} 必须是 1 到 {max_len} 个字符")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ValueError(f"{label} 不能含控制字符")
    return value


def _int(value: object, label: str, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} 必须是 {low} 到 {high} 的整数")
    return value


def _ymd(value: str, label: str) -> str:
    if type(value) is not str or _YMD.fullmatch(value) is None:
        raise ValueError(f"{label} 必须是 yyyy-MM-dd")
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} 必须是 yyyy-MM-dd") from None
    return value


def _ufts(value: str) -> str:
    if type(value) is not str or _UFTS.fullmatch(value) is None:
        raise ValueError("changed_since 必须是十进制时间戳")
    return value


def _after(value: object) -> int:
    # 单据列表和现存量按整数主键翻页。
    return _int(value, "after", 0, _INT32_MAX)


def _put_optional(fields: dict[str, Any], key: str, value: object) -> None:
    if value is None or value == "":
        return
    fields[key] = value


def _gl_sign(value: object) -> str:
    return _text(value, "凭证类别字 sign", 2)


def _gl_keyed(call: U8Call, key: GlKey) -> dict[str, Any]:
    fields = _common(call)
    fields["period"] = _int(key.period, "会计期间 period", 1, 12)
    fields["sign"] = _gl_sign(key.sign)
    fields["no"] = _int(key.no, "凭证号 no", 1, _INT32_MAX)
    return fields


def _gl_list_fields(call: U8Call, query: GlQuery) -> dict[str, Any]:
    fields = _common(call)
    fields["period_from"] = _int(query.period_from, "period_from", 1, 12)
    fields["period_to"] = _int(query.period_to, "period_to", 1, 12)
    if query.period_from > query.period_to:
        raise ValueError("period_from 不能大于 period_to")
    if query.state and query.state not in GL_STATES:
        raise ValueError("state 必须是 " + "、".join(GL_STATES))
    _put_optional(fields, "sign", query.sign and _gl_sign(query.sign))
    _put_optional(fields, "date_from", query.date_from and _ymd(query.date_from, "date_from"))
    _put_optional(fields, "date_to", query.date_to and _ymd(query.date_to, "date_to"))
    _put_optional(fields, "maker", query.maker and _text(query.maker, "maker", 40))
    _put_optional(fields, "state", query.state)
    _put_optional(fields, "after", query.after and _text(query.after, "after", 200))
    _put_optional(fields, "limit", _limit(query.limit, 200))
    return fields


def _limit(value: int | None, top: int) -> int | None:
    if value is None:
        return None
    return _int(value, "limit", 1, top)


def _scalar_row(row: object, label: str) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError(f"{label} 必须是对象")
    for key, value in row.items():
        if type(key) is not str or key == "":
            raise ValueError("字段名必须是非空字符串")
        if type(value) not in _SCALAR:
            raise ValueError(f"{label} 的字段值只能是字符串、数字或布尔")
    return dict(row)


def _gl_head(head: object) -> dict[str, Any]:
    # 新建和整张替换都要 head.sign。
    clean = _scalar_row(head, "head")
    clean["sign"] = _gl_sign(clean.get("sign"))
    return clean


def _cash_flow(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)) or len(value) > 50:
        raise ValueError("cash_flow 必须是不超过 50 项的数组")
    return [_scalar_row(item, "cash_flow") for item in value]


def _gl_line(row: object) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise ValueError("每条分录必须是对象")
    clean: dict[str, Any] = {}
    for key, value in row.items():
        if key == "cash_flow":
            clean[key] = _cash_flow(value)
            continue
        clean.update(_scalar_row({key: value}, "分录"))
    return clean


def _gl_lines(lines: object) -> list[dict[str, Any]]:
    if not isinstance(lines, (list, tuple)) or not 2 <= len(lines) <= 200:
        raise ValueError("凭证分录必须是 2 到 200 条")
    return [_gl_line(item) for item in lines]


def _archive(value: object, known: tuple[str, ...] = ARCHIVES) -> str:
    if value not in known:
        if value in WRITE_ARCHIVES:
            raise ValueError("该档案不支持删除")
        if value in READ_ARCHIVES:
            raise ValueError("该档案只读")
        raise ValueError("未知的档案类型")
    return str(value)


def _arc_keyed(
    call: U8Call, archive: str, code: str, known: tuple[str, ...] = ARCHIVES
) -> dict[str, Any]:
    fields = _common(call)
    fields["archive"] = _archive(archive, known)
    fields["code"] = _arc_code(archive, code, "档案编码 code")
    return fields


def _arc_code(archive: str, code: object, label: str) -> str:
    text = _text(code, label, _CODE_LIMIT.get(archive, 60))
    if archive in PAIR_ARCHIVES and ":" not in text:
        raise ValueError(f"{label} 必须写成 <第一段>:<第二段>")
    if archive == EXCH_ARCHIVE and ":" not in text:
        raise ValueError(f"{label} 必须写成 <币种>:<年度>:<期间>[:<日>] 或 <币种>:<yyyy-mm-dd>")
    return text


def _arc_fields(values: object, updating: bool) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise ValueError("fields 必须是对象")
    for key, value in values.items():
        if type(key) is not str or key == "":
            raise ValueError("字段名必须是非空字符串")
        if value is not None and type(value) not in _SCALAR:
            raise ValueError("fields 的值只能是字符串、数字、布尔或 null")
    if updating and "code" in values:
        raise ValueError("不能修改档案编码")
    if updating and not values:
        raise ValueError("没有要修改的内容")
    return dict(values)


def _arc_list_fields(call: U8Call, query: ArcQuery) -> dict[str, Any]:
    fields = _common(call)
    fields["archive"] = _archive(query.archive, READ_ARCHIVES)
    limit = _CODE_LIMIT.get(query.archive, 60)
    _put_optional(fields, "code_prefix", query.code_prefix and _text(query.code_prefix, "code_prefix", limit))
    _put_optional(fields, "name_like", query.name_like and _text(query.name_like, "name_like", 60))
    _put_optional(fields, "changed_since", query.changed_since and _ufts(query.changed_since))
    _put_optional(fields, "after", query.after and _arc_code(query.archive, query.after, "after"))
    _put_optional(fields, "limit", _limit(query.limit, 500))
    _put_optional(fields, "project_class", query.project_class and _project_class(query))
    _exch_filters(fields, query)
    _fa_filters(fields, query)
    if query.keys_only:
        fields["keys_only"] = True
    return fields


def _exch_filters(fields: dict[str, Any], query: ArcQuery) -> None:
    """currency、fiscal_year 只给 exchange_rate；fiscal_year 省略时桥取登录年份。"""
    if not query.currency and query.fiscal_year is None:
        return
    if query.archive != EXCH_ARCHIVE:
        raise ValueError("只有 exchange_rate 支持 currency、fiscal_year")
    _put_optional(fields, "currency", query.currency and _text(query.currency, "currency", 8))
    if query.fiscal_year is not None:
        fields["fiscal_year"] = _int(query.fiscal_year, "fiscal_year", 1900, 9999)


def _fa_filters(fields: dict[str, Any], query: ArcQuery) -> None:
    """type_code、dept_code、include_disposed 只给 fa_card；include_disposed 省略时桥只列在役卡片。"""
    if not query.type_code and not query.dept_code and query.include_disposed is None:
        return
    if query.archive != FA_ARCHIVE:
        raise ValueError("只有 fa_card 支持 type_code、dept_code、include_disposed")
    _put_optional(fields, "type_code", query.type_code and _text(query.type_code, "type_code", 20))
    _put_optional(fields, "dept_code", query.dept_code and _text(query.dept_code, "dept_code", 12))
    if query.include_disposed is not None:
        if type(query.include_disposed) is not bool:
            raise ValueError("include_disposed 必须是布尔")
        fields["include_disposed"] = query.include_disposed


def _project_class(query: ArcQuery) -> str:
    if query.archive != "project":
        raise ValueError("只有 project 支持 project_class")
    if _PROJECT_CLASS.match(query.project_class) is None:
        raise ValueError("project_class 必须是 1 到 2 位字母或数字")
    return query.project_class


def _filter_value(key: str, value: object) -> object:
    if key in BOOL_FILTERS:
        if type(value) is not bool:
            raise ValueError(f"过滤条件 {key} 必须是布尔")
        return value
    return _text(value, f"过滤条件 {key}", 60)


def _voucher_filter(values: object) -> dict[str, Any] | None:
    if values is None:
        return None
    if not isinstance(values, dict):
        raise ValueError("filter 必须是对象")
    clean: dict[str, Any] = {}
    for key, value in values.items():
        if type(key) is not str or key == "":
            raise ValueError("过滤条件名必须是非空字符串")
        clean[key] = _filter_value(key, value)
    return clean or None


def _list_fields(call: U8Call, query: VoucherQuery) -> dict[str, Any]:
    if query.kind not in _LIST_KINDS:
        raise ValueError("未知的单据类型")
    fields = _common(call)
    fields["type"] = query.kind
    _put_optional(fields, "filter", _voucher_filter(query.filter))
    if query.keys_only:
        fields["keys_only"] = True
    _put_optional(fields, "changed_since", query.changed_since and _ufts(query.changed_since))
    _put_optional(fields, "after", None if query.after is None else _after(query.after))
    _put_optional(fields, "limit", _limit(query.limit, 500))
    return fields


def _stock_fields(call: U8Call, query: StockQuery) -> dict[str, Any]:
    fields = _common(call)
    _put_optional(fields, "wh", query.wh and _text(query.wh, "wh", 20))
    _put_optional(fields, "inv", query.inv and _text(query.inv, "inv", 60))
    _put_optional(fields, "batch", query.batch and _text(query.batch, "batch", 60))
    _put_optional(fields, "after", None if query.after is None else _after(query.after))
    _put_optional(fields, "limit", _limit(query.limit, 500))
    return fields
