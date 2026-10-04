"""只读路由的请求、响应模型：字段标签（meta/fields）、单据查询（vouchers/search）、
批量读取（vouchers/load_many、archives/get_many）。

响应只给最小的信封，条目是自由的 dict：查询参数 fields / compact 能作用在顶层的 head、lines、items 上
（items[].head、items[].lines、items[].fields 不裁剪，只裁剪 items 每一项的顶层键）。
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, Field, StrictBool, ValidationInfo, field_validator, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_gen_source import SourceType
from u8co_api.co_models import CoAuth, VoucherType
from u8co_api.co_models_arc_names import ARCHIVE_DESC, ARCHIVE_RO_DESC, ReadArchiveName
from u8co_api.co_models_arc import ReadCode, check_exch_code, check_read_code
from u8co_api.co_models_gl import DATE, PASS, Scalar

_ID_MAX = 2147483647
_BATCH_MAX = 20
_CTRL = r"^[^\x00-\x1f\x7f]{1,60}$"
Word = Annotated[str, Field(pattern=_CTRL, description="1 到 60 个字符，不含控制字符")]
DocId = Annotated[int, Field(gt=0, le=_ID_MAX)]

_DEFINE_OPS = ("eq", "like", "prefix")
_DEFINE_MAX = 120
DEFINE_KEYS = tuple(f"define{n}" for n in (1, 2, 3, 8, 9, 10, 11, 12, 13, 14))


def define_normalize(text: str) -> str:
    """与桥（VoucherSearchDefines.Normalize）相同：全角空格、不换行空格当半角空格，再去两端半角空格。"""
    return text.replace("\u3000", " ").replace("\u00a0", " ").strip(" ")


def _define_text(value: object) -> str:
    text = define_normalize(value) if isinstance(value, str) else ""
    if not 1 <= len(text) <= _DEFINE_MAX or any(ord(c) < 0x20 or ord(c) == 0x7F for c in text):
        raise ValueError(f"自定义项的值必须是 1 到 {_DEFINE_MAX} 个字符的字符串（规整后：全角空格、不换行空格当半角空格，去两端空格）")
    return text


def _define_spec(value: str | dict[str, Any]) -> str | dict[str, Any]:
    # 字符串等同 {"eq": …}；对象恰好一个键 eq / like / prefix。值原样转给桥（桥规整空格、转义 LIKE）。
    if isinstance(value, dict):
        if len(value) != 1 or next(iter(value)) not in _DEFINE_OPS:
            raise ValueError("必须是字符串或 {eq | like | prefix: 字符串}")
        _define_text(next(iter(value.values())))
        return value
    _define_text(value)
    return value


# 对象的值写成 Any：值不是字符串时也由 _define_spec 报错，field 停在 defines.<键>（不带联合类型的成员标签）。
DefineSpec = Annotated[str | dict[str, Any], AfterValidator(_define_spec)]

FIELDS_SUMMARY = "字段标签"
FIELDS_HELP = (
    "返回单据、档案或总账凭证的可写字段及其标签，要登录（每个账套的单据模板不同）。\n\n"
    "**用法**\n"
    "- type（单据类型）、archive（档案）、gl（总账凭证，true）三选一。\n"
    "- fields_revision 是返回的字段列表的 SHA-256，可用来判断模板有没有变。\n\n"
    "| 对象 | 字段位置 | 说明 |\n"
    "|---|---|---|\n"
    "| 单据 | head、lines | 字段名恰好是桥对该类型、该操作接受的可写字段，与 /v1/co/meta 的 kinds[].writable 相同，自定义项、自由项已展开 |\n"
    "| 档案 | fields | 字段名是 meta archives[].writable 的标签；label 取自 U8 的列字典，取不到为 null；type 为 null，没有 enum |\n"
    "| 总账凭证 | head、lines、cash_flow | 标签是固定的 |\n\n"
    "**规则**\n"
    "- 单据的 op 为 create（缺省）、update 或 generate；generate 必须给 source（来源单据类型）。\n"
    "- 单据的 label、type、required、max_length、enum 取自本账套该类型的单据模板。\n"
    "- vt_source：fixed 桥写死的模板，user_default 操作员的缺省模板，card_default 系统缺省模板，none 没有模板。\n"
    "- 模板里没有的字段 label、type 为 null。\n"
    "- type 为 bool、string、int、decimal、date 或 enum（enum 另有 code、name 列表）。\n"
    "- required 为模板设为必输或桥自己要求。\n\n"
    "**错误**\n"
    "- 类型或档案不认识：400（field 为 type / archive）。\n"
    "- 该类型不支持这个操作：400（field 为 op）。"
)

SEARCH_SUMMARY = "单据查询"
SEARCH_HELP = (
    "按条件查某类单据的表头。\n\n"
    "**用法**\n"
    "- 与 vouchers/list 同一套取数：相同的条目、记录级数据权限、按主键续读。\n"
    "- code_like：单据编号包含。\n"
    "- partner：客户或供应商（收付款单、应收应付单是往来单位 cDwCode）；有往来单位列的类型每条另有 partner_name。\n"
    "- warehouse：表头仓库。\n"
    "- inventory：明细含该存货（表头有存货列的类型按表头）。\n"
    "- defines：按表头自定义项找单据，如表头自定义项 1 里的纸质合同号。\n"
    "- next 原样放进 after 读下一页。\n\n"
    "**规则**\n"
    "- defines 的键是 define1–3、define8–14（表头的文本自定义项），最多 4 个键，条件之间是 AND。\n"
    "- defines 的值是字符串（规整后相等）或 `{eq | like | prefix: 字符串}`（相等、包含、开头是）。\n"
    "- 规整：全角空格、不换行空格当半角空格，去两端空格。\n"
    "- 给了 defines 时每条另有 defines：请求里各键的表头值（规整后，空为 null）。\n\n"
    "**错误**\n"
    "- 该类型没有往来单位列、仓库列、存货列或关闭状态时，对应的 partner、warehouse、inventory、closed 返回 400。\n"
    "- defines 的其他键 400（field 为 `defines.<键>`）；日期、数字自定义项 define4–7、define15–16 也不收。\n"
    "- 生产订单、物料清单没有表头自定义项：400（field 为 defines）。"
)

LOAD_MANY_SUMMARY = "批量读取单据"
LOAD_MANY_HELP = (
    "一次读同一类型的 1 到 20 张单据，整个请求只登录一次。\n\n"
    "**用法**\n"
    "- 主键不能重复。\n"
    "- items 按请求顺序：成功的项与 vouchers/load 的响应体相同。\n"
    "- 失败的项是 `{id, error: {code, message}}`（not_found、no_permission 等），单张失败不影响其它。\n"
    "- fields / compact 只裁剪 items 每一项的顶层键（id、code、ok、error、masked_fields 总是保留）。\n"
    "- fields / compact 不裁剪 items[].head、items[].lines。\n"
    "- 字段权限：无权查看的字段值为 null，该项另带 masked_fields，信封上是各项的并集。\n\n"
    "**限制**\n"
    "- U8 组件读取的类型（销售、采购、库存、应收应付的单据）一次最多 5 张。\n"
    "- 按 SQL 读取的类型（采购发票、生产订单、物料清单、报检单、检验单、不良品处理单）最多 20 张。\n\n"
    "**错误**\n"
    "- U8 组件读取的类型超过 5 张：400（field 为 ids）。\n"
    "- 登录失败、许可满、繁忙等请求级错误照常返回错误。"
)

GET_MANY_SUMMARY = "批量读取档案"
GET_MANY_HELP = op_doc(
    "按编码批量读取同一档案的 1 到 20 条。",
    (
        "用法",
        (
            "编码写法与 archives/get 相同；不区分大小写，不能重复",
            "fields / compact 只裁剪 items 每一项的顶层键（id、code、ok、error 总是保留），不裁剪 items[].fields",
        ),
    ),
    (
        "规则",
        (
            "items 按请求顺序；成功的项与 archives/get 的响应体相同",
            "失败的项是 {code, error: {code, message}}（not_found、no_permission 等）",
            "单条失败不影响其它",
        ),
    ),
)

FieldsOp = Literal["create", "update", "generate"]


def _unique(values: list, what: str) -> None:
    if len(set(values)) != len(values):
        raise ValueError(f"{what} 不能重复")


def _real_date(value: str | None) -> str | None:
    if value is not None:
        try:
            dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("日期无效") from exc
    return value


class MetaFieldsIn(CoAuth):
    type: VoucherType | None = Field(None, description="单据类型（与 vouchers/load 相同）。与 archive、gl 三选一")
    archive: ReadArchiveName | None = Field(None, description="档案类型。与 type、gl 三选一")
    gl: Literal[True] | None = Field(None, description="true 表示总账凭证。与 type、archive 三选一")
    op: FieldsOp | None = Field(None, description="单据的操作：create（缺省）、update 或 generate。只能和 type 一起给")
    source: SourceType | None = Field(None, description="来源单据类型。op 为 generate 时必填，其它情况不能给")

    @model_validator(mode="after")
    def _one_target(self) -> MetaFieldsIn:
        chosen = [name for name in ("type", "archive", "gl") if getattr(self, name) is not None]
        if len(chosen) != 1:
            raise ValueError("type、archive、gl 必须且只能给一个")
        if self.type is None and (self.op is not None or self.source is not None):
            raise ValueError("op、source 只能和 type 一起给")
        if (self.op == "generate") != (self.source is not None):
            raise ValueError("op 为 generate 时必须给 source，其它操作不能给 source")
        return self

    def audit_ref(self) -> str:
        if self.type is not None:
            op = self.op or "create"
            return f"{self.type}:{op}" + (f":{self.source}" if self.source else "")
        return f"archive:{self.archive}" if self.archive is not None else "gl"


class MetaFieldsOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到字段")
    type: Scalar = Field(None, description="单据类型（按单据查时）")
    archive: Scalar = Field(None, description="档案类型（按档案查时）")
    gl: bool | None = Field(None, description="总账凭证时为 true")
    op: Scalar = Field(None, description="单据的操作")
    source: Scalar = Field(None, description="来源单据类型（generate）")
    vt_id: int | None = Field(None, description="单据：取标签用的单据模板号；没有模板时省略")
    card: Scalar = Field(None, description="单据：单据模板的卡片号")
    vt_source: Literal["fixed", "user_default", "card_default", "none"] | None = Field(
        None,
        description="单据：模板从哪里来。fixed 桥写死的模板，user_default 操作员的缺省模板，"
        "card_default 该卡片的系统缺省模板（vouchers.DEF_ID），none 没有模板（label、type 都为 null）",
    )
    head: list[dict[str, Any]] | None = Field(
        None,
        description="单据、总账凭证的表头字段：name、label、type、required，另可有 max_length、enum（code、name）",
    )
    lines: list[dict[str, Any]] | None = Field(None, description="单据、总账凭证的明细字段，键同 head")
    cash_flow: list[dict[str, Any]] | None = Field(None, description="总账凭证的现金流量字段，键同 head")
    fields: list[dict[str, Any]] | None = Field(
        None, description="档案的字段：name、label、type（总是 null）、required"
    )
    fields_revision: Scalar = Field(None, description="返回的字段列表的 SHA-256（十六进制）")


class VoucherSearchIn(CoAuth):
    type: VoucherType = Field(..., description="单据类型，与 vouchers/list 相同")
    code_like: str | None = Field(
        None, pattern=r"^[^\x00-\x1f\x7f]{1,40}$", description="单据编号包含这段文字，1 到 40 个字符"
    )
    partner: Word | None = Field(None, description="客户或供应商编码（收付款单、应收应付单是往来单位）")
    dept: Word | None = Field(None, description="部门编码")
    person: Word | None = Field(None, description="业务员编码")
    warehouse: Word | None = Field(None, description="表头仓库编码")
    maker: Word | None = Field(None, description="制单人")
    inventory: Word | None = Field(None, description="存货编码：明细里有这个存货的单据")
    date_from: str | None = Field(None, pattern=DATE, description="单据日期下限，yyyy-MM-dd，含当天")
    date_to: str | None = Field(None, pattern=DATE, description="单据日期上限，yyyy-MM-dd，含当天")
    verified: StrictBool | None = Field(None, description="true 只要已审核，false 只要未审核")
    closed: StrictBool | None = Field(None, description="true 只要已关闭，false 只要未关闭")
    after: int | None = Field(None, ge=0, le=_ID_MAX, description="上一页响应的 next（最后一个主键），按主键续读")
    limit: int | None = Field(None, ge=1, le=200, description="每页条数，1 到 200，缺省 50")
    defines: dict[str, DefineSpec] | None = Field(
        None,
        min_length=1,
        max_length=4,
        description="表头自定义项条件，1 到 4 个键。\n\n"
        "- 键：" + "、".join(DEFINE_KEYS) + "\n"
        "- 值：字符串（规整后相等）或 {eq | like | prefix: 字符串}，1 到 120 个字符\n"
        "- 键由桥核对，不认识的键 400（field 为 defines.<键>）",
        examples=[{"define1": "HT202609039"}, {"define1": {"prefix": "HT2026"}, "define10": {"like": "框架"}}],
    )

    @field_validator("date_from", "date_to")
    @classmethod
    def _date(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _range(self) -> VoucherSearchIn:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to")
        return self

    def audit_ref(self) -> str:
        return self.type


class VoucherSearchOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否查到列表")
    type: Scalar = Field(None, description="单据类型")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按主键排序的单据表头，与 vouchers/list 的条目相同；有往来单位列的类型另有 partner_name；"
        "请求给了 defines 时另有 defines（请求里各键的表头值，规整后，空为 null）",
    )
    next: Scalar = Field(None, description="下一页的 after。最后一页省略")


class LoadManyIn(CoAuth):
    type: VoucherType = Field(..., description="单据类型，与 vouchers/load 相同")
    ids: list[DocId] = Field(
        ...,
        min_length=1,
        max_length=_BATCH_MAX,
        description="单据主键，1 到 20 个，不能重复（U8 组件读取的类型最多 5 个）",
    )

    @field_validator("ids")
    @classmethod
    def _distinct(cls, value: list[int]) -> list[int]:
        _unique(value, "ids")
        return value

    def audit_ref(self) -> str:
        return f"{self.type}:" + ",".join(str(item) for item in self.ids)


class LoadManyOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="请求是否完成（单张失败不影响）")
    type: Scalar = Field(None, description="单据类型")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按请求顺序：vouchers/load 的响应体，或 {id, error: {code, message}}",
    )
    masked_fields: list[str] | None = Field(
        None, description="各项 masked_fields 的并集（字段权限）。没有被遮蔽的字段时省略"
    )


class GetManyIn(CoAuth):
    archive: ReadArchiveName = Field(..., description=ARCHIVE_DESC + ARCHIVE_RO_DESC)
    codes: list[ReadCode] = Field(
        ...,
        min_length=1,
        max_length=_BATCH_MAX,
        description="档案编码，1 到 20 个，不区分大小写不能重复，写法同 archives/get 的 code",
    )

    @field_validator("codes")
    @classmethod
    def _codes(cls, value: list[str], info: ValidationInfo) -> list[str]:
        # U8 的编码比较不区分大小写，桥也按不区分大小写拒绝重复。
        _unique([code.upper() for code in value], "codes")
        archive = info.data.get("archive")
        for index, code in enumerate(value):
            label = f"codes.{index}"
            if archive == "exchange_rate":
                # 与 archives/get 相同：汇率也可以写 <币种>:<yyyy-mm-dd>。
                check_exch_code(code, label, True)
            elif archive is not None:
                check_read_code(archive, code, label)
        return value

    def audit_ref(self) -> str:
        return f"{self.archive}:{len(self.codes)}"


class GetManyOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="请求是否完成（单条失败不影响）")
    archive: Scalar = Field(None, description="档案")
    items: list[dict[str, Any]] | None = Field(
        None,
        description="按请求顺序：archives/get 的响应体，或 {code, error: {code, message}}",
    )
