"""基础档案 /v1/co/archives/* 的请求和响应。fields 用 EAI 标签名，桥按 RsXml 校验。"""

from __future__ import annotations

import datetime as dt
import re
from typing import Annotated, Any, ClassVar

from pydantic import BaseModel, Field, StrictBool, StrictInt, field_validator, model_validator

from u8co_api.co_doctext import field_doc
from u8co_api.co_fa_card_write import check_fa_write
from u8co_api.co_fa_write import refuse_archive_op
from u8co_api.co_models import CoAuth
from u8co_api.co_models_arc_fa import FA_CARD, FA_CODE_MAX, FA_RO, FaListFilters, check_fa_filters
from u8co_api.co_models_arc_names import (
    ARCHIVE_DESC,
    ARCHIVE_RO_DESC,
    ARCHIVE_UPDATE_DESC,
    ARCHIVE_WRITE_DESC,
    DeleteArchiveName,
    ReadArchiveName,
    UpdateArchiveName,
    WriteArchiveName,
)
from u8co_api.co_models_arc_partner import (
    PARTNER_CODE_MAX,
    PARTNER_NO_UFTS,
    PARTNER_OPEN,
    PARTNER_PAIR,
    check_partner_write,
)
from u8co_api.co_models_arc_rw import check_code_tag, check_exch_write, check_gl_write, is_gl_archive
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

_TAG = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
_FIELDS_MAX = 300
_PROJECT_TAGS = frozenset({"name", "bclose", "citemccode"})
# 没有 rowversion 的只读档案，列表不支持 changed_since。
_NO_UFTS = frozenset({"voucher_sign", "project", "customer_address", FA_CARD, "operator", "role"}) | PARTNER_NO_UFTS
_PROJECT = "project"
# 以桥为准（ArcRoutes.CheckCode / ArcKindRo / ArcProject）：编码不含控制字符（\x00–\x1f、\x7f、\uFFFE、\uFFFF），
# 首尾不能有空白，中间可以有空格。可写档案最长 30；只读档案 account 40、unit / unit_group 35、project 63，
# 按表列长度（position 20、rd_style 5、purchase_type / sale_type 2、district_class / trade_class 12、aa_bank 5），
# 结算方式 3、币种（名称）8 也按表列长度，其余 30。
_CTRL = "\\x00-\\x1f\\x7f\\uFFFE\\uFFFF"
# 首尾不是空白（pydantic 的 Rust 正则不支持环视，长度另用 max_length 限制）。
_TRIMMED = rf"[^\s{_CTRL}](?:[^{_CTRL}]*[^\s{_CTRL}])?"
_PROJECT_CODE = re.compile(rf"[0-9A-Za-z]{{1,2}}:(?=.{{1,60}}$){_TRIMMED}")
_CODE_MAX = {
    "account": 40,
    "unit": 35,
    "unit_group": 35,
    "settle_style": 3,
    "currency": 8,
    "project": 63,
    "bank": 3,
    "position": 20,
    "rd_style": 5,
    "purchase_type": 2,
    "sale_type": 2,
    "district_class": 12,
    "trade_class": 12,
    "aa_bank": 5,
    "customer_address": 51,
    "user_define": 411,
    "customer_inventory": 81,
    "exchange_rate": 27,
    # 原因码（桥 ArcReason.CodeMax，EAI 模板 Reason.xml 的编码 10）。
    "reason": 10,
    FA_CARD: FA_CODE_MAX,
    "equipment": 30,  # 设备台账（EQ_EQData.cEQCode）
    # U8 操作员、角色（桥 ArcUa.CodeMax，UA_User.cUser_Id nvarchar(20)）。
    "operator": 20,
    "role": 20,
    **PARTNER_CODE_MAX,
}
# 两列主键的档案（桥 ArcPair）：编码 "<第一段>:<第二段>"，按第一个冒号拆开，两段各自的最大长度和写法。
_PAIR = {
    "customer_address": (20, 30, "<客户编码>:<地址编码>"),
    "user_define": (10, 400, "<自定义项号>:<档案值>"),
    "customer_inventory": (20, 60, "<客户编码>:<存货编码>"),
    **PARTNER_PAIR,
}
# 汇率（桥 ArcExch / ExchKey）：币种 1 到 8 个字符，年度四位，期间 1 到 12，日（浮动汇率的 exch.cdate 原文）最长 10。
_EXCH = "exchange_rate"
_EXCH_FORM = "<币种>:<年度>:<期间>[:<日>] 或 <币种>:<yyyy-mm-dd>"
_EXCH_KEY = re.compile(r"(?:19|[2-9]\d)\d{2}:(?:[1-9]|0[1-9]|1[0-2])(?::(?!\s)[^:]{1,10}(?<!\s))?")
_EXCH_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_READ_CODE_MAX = 411
_CODE_DEFAULT = 30
Code = Annotated[
    str,
    Field(
        pattern=rf"^{_TRIMMED}$",
        max_length=30,
        description="档案编码，1 到 30 个字符，首尾不能有空白，不含控制字符",
    ),
]
FieldValue = (
    Annotated[str, Field(max_length=2000)]
    | Annotated[int, Field(strict=True)]
    | Annotated[float, Field(strict=True, allow_inf_nan=False)]
    | bool
    | None
)
Ufts = Annotated[str, Field(pattern=r"^\d{1,19}$", description="rowversion 的十进制字符串")]


def check_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """校验标签名，并去掉值为 null 的标签（null 表示不发送）。"""
    if len(fields) > _FIELDS_MAX:
        raise ValueError("fields 最多 300 项")
    for tag in fields:
        if _TAG.fullmatch(tag) is None:
            raise ValueError("字段名无效 " + tag)
    return {tag: value for tag, value in fields.items() if value is not None}


ReadCode = Annotated[
    str,
    Field(
        pattern=rf"^{_TRIMMED}$",
        max_length=_READ_CODE_MAX,
        description=field_doc(
            "档案编码，首尾不能有空白，不含控制字符。",
            (
                "用法",
                (
                    "project：`<项目大类>:<项目编码>`，大类 1 到 2 位字母数字，项目编码 1 到 60 个字符",
                    "customer_address：`<客户编码>:<地址编码>`，两段最长 20、30",
                    "user_define：`<自定义项号>:<档案值>`，两段最长 10、400",
                    "customer_inventory：`<客户编码>:<存货编码>`，两段最长 20、60",
                    "exchange_rate：`<币种>:<年度>:<期间>[:<日>]`；get 也可以写 `<币种>:<yyyy-mm-dd>`",
                ),
            ),
            (
                "限制",
                (
                    "一般最长 30 个字符",
                    "account 40；unit、unit_group 35；position、operator、role 20",
                    "rd_style、aa_bank 5；purchase_type、sale_type 2；district_class、trade_class 12",
                    "settle_style 3，currency 8，reason 10",
                ),
            ),
        ),
    ),
]


class ArcKeyBase(CoAuth):
    archive: DeleteArchiveName = Field(..., description=ARCHIVE_DESC + ARCHIVE_WRITE_DESC)
    code: ReadCode

    def audit_ref(self) -> str:
        return f"{self.archive}:{self.code}"

    @model_validator(mode="after")
    def _code_len(self) -> ArcKeyBase:
        check_read_code(self.archive, self.code, "code")
        return self


class ArcKeyIn(ArcKeyBase):
    """删除（新增、修改继承它）：多一个 dry_run。读取 ArcGetIn 直接继承 ArcKeyBase，不收。"""

    dry_run: DryRunFlag = False
    _op: ClassVar[str] = "delete"

    @model_validator(mode="before")
    @classmethod
    def _refused(cls, data: object) -> object:
        return refuse_archive_op(data, cls._op)  # 固定资产卡片、设备台账不支持的操作，与桥同文


def _check_pair(archive: str, code: str, label: str) -> None:
    """两列主键：按第一个冒号拆成两段，每段 1 到该列长度个字符，首尾没有空白（桥 ArcPair.Split）。"""
    first_max, second_max, form = _PAIR[archive]
    first, sep, second = code.partition(":")
    # 客户联系人新增的第二段可以为空（U8 自动编号，桥 ArcPair.OpenSecond）；其他操作由 check_partner_write 和桥再拒绝。
    low = 0 if archive in PARTNER_OPEN else 1
    for part, limit, least in ((first, first_max, 1), (second, second_max, low)):
        if sep != ":" or not least <= len(part) <= limit or part.strip() != part:
            raise ValueError(f"{label} 必须写成 {form}，两段长度分别不超过 {first_max} 和 {second_max}，前后不能有空格")


def _exch_date(text: str) -> bool:
    if _EXCH_DATE.fullmatch(text) is None:
        return False
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def check_exch_code(code: str, label: str, allow_date: bool) -> None:
    """汇率编码（桥 ExchKey.Parse）：<币种>:<年度>:<期间>[:<日>]；allow_date 时也收 <币种>:<yyyy-mm-dd>。"""
    currency, sep, rest = code.partition(":")
    ok = sep == ":" and 1 <= len(currency) <= 8 and currency.strip() == currency
    if ok and not (allow_date and _exch_date(rest)):
        ok = _EXCH_KEY.fullmatch(rest) is not None
    if not ok:
        raise ValueError(f"{label} 必须写成 {_EXCH_FORM if allow_date else '<币种>:<年度>:<期间>[:<日>]'}")


def check_read_code(archive: str, code: str | None, label: str) -> None:
    """project 的编码必须是 <大类>:<编码>，两列主键的档案按两段校验，其他档案按桥的每类最大长度。"""
    if code is None:
        return
    if archive == _PROJECT:
        if _PROJECT_CODE.fullmatch(code) is None:
            raise ValueError(f"{label} 必须写成 <项目大类>:<项目编码>")
        return
    if archive in _PAIR:
        _check_pair(archive, code, label)
        return
    if archive == _EXCH:
        check_exch_code(code, label, label == "code")
        return
    limit = _CODE_MAX.get(archive, _CODE_DEFAULT)
    if len(code) > limit:
        raise ValueError(f"{label} 最长 {limit} 个字符")


def check_prefix(archive: str, prefix: str | None) -> None:
    """code_prefix 最长与该档案的编码相同（桥 ArcReq.ParseList）。"""
    limit = _CODE_MAX.get(archive, _CODE_DEFAULT)
    if prefix is not None and len(prefix) > limit:
        raise ValueError(f"code_prefix 最长 {limit} 个字符")


class ArcGetIn(ArcKeyBase):
    archive: ReadArchiveName = Field(..., description=ARCHIVE_DESC + ARCHIVE_RO_DESC)
    code: ReadCode

    @model_validator(mode="after")
    def _code(self) -> ArcGetIn:
        check_read_code(self.archive, self.code, "code")
        return self


def check_project_write(archive: str, fields: dict[str, Any], template: str | None, creating: bool) -> None:
    """project 只收 name、bclose、citemccode，新增必须有 citemccode，不收 template（桥 ArcProjectWrite.Check）。"""
    if archive != _PROJECT:
        return
    unknown = sorted(set(fields) - _PROJECT_TAGS)
    if unknown:
        raise ValueError("project 只能写 name、bclose、citemccode，未知字段 " + ", ".join(unknown))
    if template is not None:
        raise ValueError("项目档案不支持 template")
    if creating and "citemccode" not in fields:
        raise ValueError("新增项目必须给 citemccode（所属末级分类）")
    close = fields.get("bclose")
    if close is not None and close not in (True, False, 0, 1, "0", "1"):
        raise ValueError("bclose 必须是布尔或 0 / 1")


class ArcCreateIn(ArcKeyIn):
    archive: WriteArchiveName = Field(..., description=ARCHIVE_DESC + ARCHIVE_WRITE_DESC)
    code: ReadCode
    fields: dict[str, FieldValue] = Field(
        ...,
        description="EAI 标签名 → 值（字符串、数字或布尔）。null 表示不发送。标签名以桥的 RsXml 为准，未知标签返回 400",
    )
    template: Code | None = Field(
        None,
        description="模板档案编码。存货必填：桥先按模板读出全部表头，再用 code、name、start_date 和 fields 覆盖",
    )
    _op: ClassVar[str] = "create"

    @field_validator("fields")
    @classmethod
    def _fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        return check_fields(value)

    @model_validator(mode="after")
    def _write(self) -> ArcCreateIn:
        check_code_tag(self.archive, self.fields, True)
        check_gl_write(self.archive, self.code, self.fields, True)
        check_project_write(self.archive, self.fields, self.template, True)
        check_partner_write(self.archive, self.code, self.fields, True)
        check_exch_write(self.archive, self.code, self.fields, True)
        check_fa_write(self.archive, self.fields, self.template)  # 固定资产卡片、设备台账
        no_template = self.archive in _PAIR or self.archive == _EXCH or is_gl_archive(self.archive)
        if self.template is not None and no_template:
            raise ValueError(f"档案 {self.archive} 不支持 template")
        return self


class ArcUpdateIn(ArcKeyIn):
    archive: UpdateArchiveName = Field(..., description=ARCHIVE_DESC + ARCHIVE_UPDATE_DESC)
    code: ReadCode
    fields: dict[str, FieldValue] = Field(
        ...,
        description="只改列出的标签，至少一个非 null。桥会补发该档案必需的当前值（例如部门的 name）。不能改 code",
    )
    _op: ClassVar[str] = "update"

    @field_validator("fields")
    @classmethod
    def _fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        kept = check_fields(value)
        if not kept:
            raise ValueError("没有要修改的内容")
        return kept

    @model_validator(mode="after")
    def _write(self) -> ArcUpdateIn:
        check_code_tag(self.archive, self.fields, False)
        check_gl_write(self.archive, self.code, self.fields, False)
        check_project_write(self.archive, self.fields, None, False)
        check_partner_write(self.archive, self.code, self.fields, False)
        check_exch_write(self.archive, self.code, self.fields, False)
        return self


class ArcListIn(CoAuth, FaListFilters):
    archive: ReadArchiveName = Field(..., description=ARCHIVE_DESC + ARCHIVE_RO_DESC)
    code_prefix: str | None = Field(
        None,
        pattern=rf"^[^{_CTRL}]{{1,{_READ_CODE_MAX}}}$",
        description="编码前缀，不含控制字符，最长与该档案的编码相同（一般 30，project 63）。"
        "project 和两列主键的档案按整串 <第一段>:<第二段> 匹配，例如 C001:",
    )
    name_like: str | None = Field(
        None,
        pattern=rf"^[^{_CTRL}]{{1,60}}$",
        description="名称包含的文字，1 到 60 个字符，不含控制字符",
    )
    changed_since: Ufts | None = Field(None, description="只返回 ufts 大于该值的档案。用上一轮第一页的 watermark")
    after: ReadCode | None = Field(None, description="上一页响应的 next（最后一个编码），按编码续读")
    limit: StrictInt | None = Field(None, ge=1, le=500, description="每页条数，1 到 500，缺省 100")
    keys_only: StrictBool | None = Field(None, description="为 true 时每项只返回 code 和 ufts，便于比对删除")
    project_class: str | None = Field(
        None,
        pattern=r"^[0-9A-Za-z]{1,2}$",
        description="只给 project：只列这个项目大类。省略时列出全部项目大类",
    )
    currency: str | None = Field(
        None,
        pattern=rf"^{_TRIMMED}$",
        max_length=8,
        description="只给 exchange_rate：只列这个币种（币种名称，如 美元）",
    )
    fiscal_year: StrictInt | None = Field(
        None, ge=1900, le=9999, description="只给 exchange_rate：汇率的年度，省略时取登录日期的年份"
    )

    @model_validator(mode="after")
    def _read_only(self) -> ArcListIn:
        check_read_code(self.archive, self.after, "after")
        check_prefix(self.archive, self.code_prefix)
        if self.project_class is not None and self.archive != _PROJECT:
            raise ValueError("只有 project 支持 project_class")
        if (self.currency is not None or self.fiscal_year is not None) and self.archive != _EXCH:
            raise ValueError("只有 exchange_rate 支持 currency、fiscal_year")
        check_fa_filters(self.archive, (self.type_code, self.dept_code, self.include_disposed))
        if self.changed_since is not None and self.archive in _NO_UFTS:
            raise ValueError(f"档案 {self.archive} 没有 ufts，不支持 changed_since")
        return self


class ArcGetOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到档案")
    archive: Scalar = Field(None, description="档案")
    code: Scalar = Field(None, description="档案编码")
    fields: dict[str, Any] | None = Field(
        None,
        description="EAI 标签名 → 当前值。返回全部有值的标签（含银行账号、联系方式），只去掉口令类列。"
        "只读档案按表列名返回全部非空列；exchange_rate 返回 code、currency、year、period、day、rate、adjust_rate、mode；"
        "fa_card 返回与列表项相同的卡片（原值、累计折旧、净值、使用部门 depts 等）；"
        "operator、role 返回与列表项相同的对象",
    )
    ufts: Scalar = Field(None, description="只读档案：rowversion 的十进制字符串。没有 rowversion 的档案省略")
    fiscal_year: int | None = Field(None, description="account：读的是这一会计年度（登录日期的年份）的科目")
    project_class: Scalar = Field(None, description="project：项目大类")
    rate_mode: Scalar = Field(
        None, description="exchange_rate 按日期读取时：账套的汇率方式，fixed 固定汇率（按月）或 floating 浮动汇率（按日）"
    )


class ArcListItem(BaseModel):
    model_config = PASS
    code: Scalar = Field(None, description="编码")
    name: Scalar = Field(None, description="名称")
    class_code: Scalar = Field(
        None,
        description="分类编码（unit 是计量单位组，project 是项目分类，position 是仓库，"
        "customer_address、customer_inventory 是客户编码，user_define 是自定义项号）。没有分类的档案省略",
    )
    ufts: Scalar = Field(
        None, description="rowversion 的十进制字符串。voucher_sign、project、customer_address 为 null"
    )
    project_class: Scalar = Field(None, description="project：项目大类")
    closed: bool | None = Field(None, description="project：项目已关闭")
    currency: Scalar = Field(None, description="exchange_rate：币种名称")
    year: int | None = Field(None, description="exchange_rate：年度")
    period: int | None = Field(None, description="exchange_rate：期间（月份）")
    day: Scalar = Field(None, description="exchange_rate：浮动汇率的日期（U8 原文）。固定汇率为 null")
    rate: float | None = Field(None, description="exchange_rate：记账汇率（固定汇率）或当日的浮动汇率")
    adjust_rate: float | None = Field(None, description="exchange_rate：该期间的调整汇率（期末调汇用）。未录入为 null")
    mode: Scalar = Field(None, description="exchange_rate：fixed 按月的固定汇率，floating 按日的浮动汇率")
    dept: Scalar = Field(None, description="operator：U8 操作员档案上的所属部门（文本原样）")
    state: int | None = Field(None, description="operator：U8 的 nState 原值，0 正常")
    end_date: Scalar = Field(
        None,
        description="customer、vendor、warehouse、department、person、inventory：停用日期（人员是失效日期，"
        "yyyy-mm-dd）。未填停用日期或 keys_only 时省略",
    )
    disabled: bool | None = Field(
        None,
        description="operator：nState 不是 0（已停用或注销）；customer、vendor、warehouse、department、person、"
        "inventory：已填停用日期（不比较当天日期）",
    )
    bin_managed: bool | None = Field(None, description="warehouse：启用货位管理（bWhPos）。keys_only 时省略")
    leaf: bool | None = Field(None, description="position：末级货位（bPosEnd），只有末级能出入库。keys_only 时省略")
    batch_managed: bool | None = Field(None, description="inventory：批次管理（bInvBatch）。keys_only 时省略")
    shelf_life_managed: bool | None = Field(
        None, description="inventory：保质期管理（bInvQuality）。keys_only 时省略"
    )
    roles: list[str] | None = Field(None, description="operator：所属角色编码（只含本账套有授权的角色）")
    person_code: Scalar = Field(None, description="operator：关联的人员编码（UserHrPersonContro），没有关联为 null")
    person_name: Scalar = Field(None, description="operator：关联人员的姓名")
    members: list[str] | None = Field(None, description="role：成员操作员编码")


class ArcListOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到列表")
    items: list[ArcListItem] | None = Field(None, description="按编码排序的档案")
    next: Scalar = Field(None, description="下一页的 after。最后一页省略")
    watermark: Scalar = Field(
        None,
        description="读这一页之前的 MIN_ACTIVE_ROWVERSION()-1。"
        "整轮读完后，把第一页的 watermark 作为下一轮的 changed_since。"
        "voucher_sign、project、customer_address、fa_card、operator、role 为 null",
    )
    fiscal_year: int | None = Field(
        None,
        description="account、exchange_rate、fa_card：读的是这一年度（account、fa_card 取登录日期的年份，"
        "exchange_rate 取请求的 fiscal_year 或登录年份）",
    )
    rate_mode: Scalar = Field(None, description="exchange_rate：账套的汇率方式，fixed 或 floating")


class ArcWriteOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    archive: Scalar = Field(None, description="档案")
    code: Scalar = Field(None, description="档案编码")
    deleted: bool | None = Field(None, description="删除成功时为 true")
    asset_num: Scalar = Field(None, description="fa_card 新增：资产编号（即请求的 code；响应的 code 是卡片编号）")
    card_id: int | None = Field(None, description="fa_card 新增：U8 卡片内部编号 sCardID")
