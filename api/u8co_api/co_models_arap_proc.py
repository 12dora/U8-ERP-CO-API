"""应收应付处理：应收冲应付 / 应付冲应收（arap/transfer）、并账（arap/merge）、红票对冲（arap/red_offset）的请求。
校验规则与桥的 ArapTransferReq、ArapMergeReq 和红票对冲的请求解析一致；出参见 co_models_arap_proc_out。"""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag

_FORBID = ConfigDict(extra="forbid")
_ID_MAX = 2147483647
_AMOUNT_MAX = 1000000000000
_LINES_MAX = 50
_PARTNER_MAX = 20
_DOC_MAX = 30
_TEXT_MAX = 120

# 各处理收的 U8 单据类型代码。
TRANSFER_TYPES = {"ar_lines": ("26", "27", "R0"), "ap_lines": ("01", "02", "P0")}
# 收付款单（48 / 49）不参与转账：桥同样 400。
_RECEIPTS = frozenset({"48", "49"})
TRANSFER_NO_RECEIPT = "转账只支持发票和应收单 / 应付单；收付款单请用核销"
SIDE_TYPES = {"AR": ("26", "27", "R0"), "AP": ("01", "02", "P0")}
_BILLS = frozenset({"R0", "P0"})
_SIDE_NAMES = {"AR": "26、27、R0（销售发票、应收单）", "AP": "01、02、P0（采购发票、应付单）"}

# 接口说明共用的 Markdown 条目：预演（只对测试账套开放的开关见 co_doctext.TEST_ONLY_GATE）。
DRY_ROLLBACK_NOTE = "- dry_run 为 true：在事务里执行后回滚（rollback 模式），不写入，返回 DryRunOut\n"
_RELATED = "**相关**\n- 撤销：arap/process/cancel\n- 制单：arap/process/voucher"
_CHECK_REJECTED = "- 409 u8_rejected：核对不符，已回滚\n"
_CONTROLLED = "- 409 workflow_enabled：单据受审批流控制\n"

TRANSFER_SUMMARY = "应收冲应付 / 应付冲应收"
TRANSFER_HELP = (
    "用同一往来对象的应收余额抵应付余额，对应 U8「转账」里的应收冲应付、应付冲应收。\n\n"
    "**用法**\n\n"
    "| flag | 处理 | 处理方式 | 处理号 |\n"
    "| --- | --- | --- | --- |\n"
    "| AR | 应收冲应付 | 9I | YCFAP… |\n"
    "| AP | 应付冲应收 | 9J | FCYAR… |\n\n"
    "- ar_lines 收 26 / 27 销售发票、R0 应收单；ap_lines 收 01 / 02 采购发票、P0 应付单\n"
    "- 收付款单不参与转账，请用核销\n" + DRY_ROLLBACK_NOTE + "\n"
    "**规则**\n"
    "- 各单据须同币种\n"
    "- 同一单据行不能重复，同一单据不能既按行又按整单\n"
    "- 处理日期就是登录日期 date（缺省今天），须在应收、应付都未结账的期间内\n"
    "- 两边各写一组处理行（往来明细两张表），发票累计核销经 U8 的回写组件\n\n"
    "**权限**\n"
    "- 功能权限：AR050502 / AP050502（上级 AR0505 / AP0505）\n"
    "- 数据权限：按两侧单据的往来单位、部门、业务员\n\n"
    "**错误**\n"
    "- 404 not_found：单据不存在\n" + _CONTROLLED + "- 409 state_mismatch：单据未审核、往来单位或币种不符、余额不足\n"
    "- 409 state_mismatch：日期早于单据日期或系统启用日期、期间已结账\n"
    "- 409 state_mismatch：外币两侧折合本币不等\n" + _CHECK_REJECTED + "\n" + _RELATED
)
MERGE_SUMMARY = "应收 / 应付并账"
MERGE_HELP = (
    "把若干单据在往来单位 from 名下的余额并到 to 名下，对应 U8「转账」里的并账（处理方式 BZ，处理号 BZAR… / BZAP…）。\n\n"
    "**用法**\n"
    "- flag AR：lines 收 26 / 27 销售发票、R0 应收单\n"
    "- flag AP：lines 收 01 / 02 采购发票、P0 应付单\n" + DRY_ROLLBACK_NOTE + "\n"
    "**规则**\n"
    "- 每张单据（行）写一对 ± 处理行，单据本身不改\n"
    "- 并账日期就是登录日期 date，须在本系统未结账的期间内\n\n"
    "**权限**\n"
    "- 功能权限：AR050504 / AP050504（上级 AR0505 / AP0505）\n"
    "- 数据权限：按各单据和 from、to 两个往来单位\n\n"
    "**错误**\n"
    "- 404 not_found：单据不存在\n" + _CONTROLLED + "- 409 state_mismatch：单据未审核、不属于 from、余额不足\n"
    "- 409 state_mismatch：日期早于单据日期或系统启用日期、期间已结账、一次超过 500 行\n"
    + _CHECK_REJECTED
    + "\n"
    + _RELATED
)
RED_OFFSET_SUMMARY = "应收 / 应付红票对冲"
RED_OFFSET_HELP = (
    "用同一往来单位的红字单据冲抵蓝字单据，对应 U8「转账」里的红票对冲（处理方式 9N，处理号 HRAR… / HPAP…）。\n\n"
    "**用法**\n"
    "- flag AR：red、blue 收 26 / 27 销售发票、R0 应收单\n"
    "- flag AP：red、blue 收 01 / 02 采购发票、P0 应付单\n" + DRY_ROLLBACK_NOTE + "\n"
    "**规则**\n"
    "- 同一单据不能同时出现在两侧；各单据须同币种\n"
    "- 对冲日期就是登录日期 date，须在本系统未结账的期间内\n"
    "- 在桥的事务里调用 U8 对冲组件 U8ApCancel.cLsCancel.AP_JZ_Red\n"
    "- 发票累计核销由 U8 一并回写\n\n"
    "**权限**\n"
    "- 功能权限：AR050503 / AP050503（上级 AR0505 / AP0505）\n"
    "- 数据权限：按往来单位、部门、业务员\n\n"
    "**错误**\n"
    "- 404 not_found：单据不存在\n"
    + _CONTROLLED
    + "- 409 state_mismatch：单据未审核、红蓝方向不对、往来单位或币种不符\n"
    "- 409 state_mismatch：余额不足、期间已结账\n"
    "- 409 u8_rejected：U8 拒绝（带回 U8 原文），或核对不符，均已回滚\n\n" + _RELATED
)


def has_control(value: str) -> bool:
    """含控制字符（Unicode Cc，同桥的 char.IsControl）。"""
    return any(unicodedata.category(ch) == "Cc" for ch in value)


def _no_control(value: str, name: str) -> str:
    if not value.strip() or has_control(value):
        raise ValueError(f"{name} 无效")
    return value


def _cents(value: float | None) -> float | None:
    if value is not None and Decimal(str(value)).as_tuple().exponent < -2:
        raise ValueError("amount 必须大于 0、不超过 1000000000000、最多两位小数")
    return value


class ProcLineIn(BaseModel):
    """一张单据（或它的一行）及本次金额。"""

    model_config = _FORBID
    type: str = Field(..., min_length=2, max_length=2, description="U8 单据类型代码（26 / 27 / R0、01 / 02 / P0）")
    id: str = Field(..., min_length=1, max_length=_DOC_MAX, description="单据号")
    line_id: int | None = Field(
        None,
        gt=0,
        le=_ID_MAX,
        description="发票表体行（= 往来明细 iBVid）；省略时按行主键从小到大依次分摊。应收单、应付单按整单，不能带",
    )
    amount: float = Field(
        ..., gt=0, le=_AMOUNT_MAX, allow_inf_nan=False, strict=True, description="本次金额（原币），最多两位小数"
    )

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: float | None) -> float | None:
        return _cents(value)

    @field_validator("id")
    @classmethod
    def _doc(cls, value: str) -> str:
        return _no_control(value, "id")


class MergeLineIn(ProcLineIn):
    amount: float | None = Field(
        None,
        gt=0,
        le=_AMOUNT_MAX,
        allow_inf_nan=False,
        strict=True,
        description="本次并入的金额（原币）；省略并入该单据（行）在 from 名下的全部余额",
    )


def _check_lines(lines: list[ProcLineIn], name: str, types: tuple[str, ...], verb: str, fold: bool = False) -> None:
    """类型、整单不带 line_id、重复；同一单据不能既按行又按整单。fold 为真时单号不区分大小写（并账、红票对冲）。"""
    seen: set[tuple[str, str, int]] = set()
    for line in lines:
        if line.type not in types:
            raise ValueError(f"{name}[].type 只能是 " + "、".join(types))
        if line.line_id is not None and line.type in _BILLS:
            raise ValueError(f"应收单、应付单按整单{verb}，不能带 line_id")
        doc = (line.type, _doc_key(line.id, fold))
        key = (*doc, line.line_id or 0)
        if key in seen:
            raise ValueError(f"{name} 里有重复的单据行")
        if any(old[:2] == doc and (old[2] == 0 or key[2] == 0) for old in seen):
            raise ValueError(f"同一张单据不能既整单又按行{verb}")
        seen.add(key)


def _doc_key(code: str, fold: bool) -> str:
    code = code.strip()
    return code.upper() if fold else code


def _total(lines: list[ProcLineIn]) -> Decimal:
    return sum((Decimal(str(line.amount)) for line in lines), Decimal(0))


class CoArapTransferIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="AR 应收冲应付（9I），AP 应付冲应收（9J）；也是登录子系统")
    customer: str = Field(..., min_length=1, max_length=_PARTNER_MAX, description="客户编码")
    vendor: str = Field(..., min_length=1, max_length=_PARTNER_MAX, description="供应商编码")
    currency: str | None = Field(None, min_length=1, max_length=20, description="币种名称；省略为本位币")
    ar_lines: list[ProcLineIn] = Field(
        ..., min_length=1, max_length=_LINES_MAX, description="应收一侧，1 到 50 项（26 / 27 / R0）"
    )
    ap_lines: list[ProcLineIn] = Field(
        ...,
        min_length=1,
        max_length=_LINES_MAX,
        description="应付一侧，1 到 50 项（01 / 02 / P0），合计须等于 ar_lines",
    )
    digest: str | None = Field(None, max_length=_TEXT_MAX, description="摘要；省略用 U8 的缺省摘要")
    dry_run: DryRunFlag = False

    @field_validator("customer", "vendor", "currency")
    @classmethod
    def _codes(cls, value: str | None, info) -> str | None:
        return value if value is None else _no_control(value, info.field_name)

    @model_validator(mode="after")
    def _sides(self) -> CoArapTransferIn:
        if self.digest is not None and has_control(self.digest):
            raise ValueError("digest 必须是不含控制字符的字符串")
        if any(line.type in _RECEIPTS for line in self.ar_lines + self.ap_lines):
            raise ValueError(TRANSFER_NO_RECEIPT)
        for name in ("ar_lines", "ap_lines"):
            _check_lines(getattr(self, name), name, TRANSFER_TYPES[name], "转账")
        if _total(self.ar_lines) != _total(self.ap_lines):
            raise ValueError("ar_lines 与 ap_lines 的金额合计必须相等")
        return self

    def audit_ref(self) -> str:
        return f"{self.customer}/{self.vendor}"


class CoArapMergeIn(CoAuth):
    # from 是 Python 关键字：字段名 from_，请求和转给桥的键都是 from。
    model_config = ConfigDict(serialize_by_alias=True)
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统")
    from_: str = Field(..., alias="from", min_length=1, max_length=_PARTNER_MAX, description="并出的客户（供应商）编码")
    to: str = Field(
        ..., min_length=1, max_length=_PARTNER_MAX, description="并入的客户（供应商）编码，不能与 from 相同"
    )
    lines: list[MergeLineIn] = Field(
        ...,
        min_length=1,
        max_length=_LINES_MAX,
        description="并账的单据，1 到 50 项：AR 收 26 / 27 / R0，AP 收 01 / 02 / P0；amount 可省略",
    )
    digest: str | None = Field(None, min_length=1, max_length=_TEXT_MAX, description="摘要；省略为「并账」")
    dry_run: DryRunFlag = False

    @field_validator("from_", "to")
    @classmethod
    def _codes(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("必须是 1 到 20 个字符的往来单位编码")
        return value

    @model_validator(mode="after")
    def _shape(self) -> CoArapMergeIn:
        if self.from_.strip().upper() == self.to.strip().upper():
            raise ValueError("from 与 to 不能是同一个" + ("供应商" if self.flag == "AP" else "客户"))
        if self.digest is not None and not self.digest.strip():
            raise ValueError("digest 必须是 1 到 120 个字符的文本")
        if any(line.type not in SIDE_TYPES[self.flag] for line in self.lines):
            raise ValueError("type 只能是 " + _SIDE_NAMES[self.flag])
        _check_lines(self.lines, "lines", SIDE_TYPES[self.flag], "并账", fold=True)
        return self

    def audit_ref(self) -> str:
        return f"{self.from_}>{self.to}"


class CoArapRedOffsetIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统")
    partner: str = Field(..., min_length=1, max_length=_PARTNER_MAX, description="客户（AR）或供应商（AP）编码")
    currency: str | None = Field(None, min_length=1, max_length=20, description="币种名称；省略为本位币")
    red: list[ProcLineIn] = Field(
        ...,
        min_length=1,
        max_length=_LINES_MAX,
        description="红字一侧，1 到 50 项（红字发票或负余额的应收单 / 应付单）",
    )
    blue: list[ProcLineIn] = Field(
        ..., min_length=1, max_length=_LINES_MAX, description="蓝字一侧，1 到 50 项，合计须等于 red"
    )
    digest: str | None = Field(None, max_length=_TEXT_MAX, description="摘要；U8 组件不收时忽略")
    dry_run: DryRunFlag = False

    @field_validator("partner", "currency")
    @classmethod
    def _codes(cls, value: str | None, info) -> str | None:
        return value if value is None else _no_control(value, info.field_name)

    @model_validator(mode="after")
    def _sides(self) -> CoArapRedOffsetIn:
        if self.digest is not None and has_control(self.digest):
            raise ValueError("digest 必须是不含控制字符的字符串")
        types = SIDE_TYPES[self.flag]
        _check_lines(self.red, "red", types, "对冲", fold=True)
        _check_lines(self.blue, "blue", types, "对冲", fold=True)
        red_docs = {(line.type, line.id.strip().upper()) for line in self.red}
        if any((line.type, line.id.strip().upper()) in red_docs for line in self.blue):
            raise ValueError("同一单据不能同时出现在 red 和 blue")
        if _total(self.red) != _total(self.blue):
            raise ValueError("red 与 blue 的金额合计必须相等")
        return self

    def audit_ref(self) -> str:
        return self.partner
