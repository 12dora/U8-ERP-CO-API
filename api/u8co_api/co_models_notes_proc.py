"""票据处理（notes/process：托收 / 结算 9A、贴现 9D、背书冲应付 9E、退回 9C）的请求和响应。
校验规则与桥的 NotesProcReq 一致；应付票据（flag AP）只收结算、退回，只对测试账套开放。"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, StrictInt, StrictStr, field_validator, model_validator

from u8co_api.co_models import _ID_MAX, CoAuth
from u8co_api.co_models_arap_proc import ProcLineIn, _cents, _check_lines, _total, has_control
from u8co_api.co_models_arap_proc_out import ProcRowOut
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_gl import PASS, Scalar

_AMOUNT_MAX = 1000000000000
_SERIAL_MAX = 999999999999999
_LINES_MAX = 50
_AP_TYPES = ("01", "02", "P0")
# 各 op 专用的字段（其他 op 给了就 400，同桥的 Absent）。
_OP_FIELDS = (
    (("settle", "discount"), "结算、贴现", ("bank_code", "bank_name")),
    (("discount",), "贴现", ("expense", "interest", "rate")),
    (("endorse",), "背书", ("vendor", "ap_lines")),
)

# 应付票据只开放的 op（本接口不提供应付票据的贴现、背书）。
_AP_OPS = ("settle", "return")
AP_OPS_REFUSED = "应付票据只支持结算（settle）、退回（return），贴现、背书请在 U8 客户端处理"

PROC_SUMMARY = "票据处理"
PROC_HELP = (
    "U8 应收（应付）款管理「票据管理」里的票据处理（flag AR 应收票据、AP 应付票据，也是登录子系统）："
    "op 为 settle 托收 / 结算（处理方式 9A，处理号 PJJAR…）、discount 贴现（9D，PJTAR…）、"
    "endorse 背书冲应付（9E，PJBAR…，同时冲被背书供应商的应付单据）、"
    "return 退回（9C，CLAR…：把票据或分包票据的全部可用子票区间退还客户，经 U8 应收单组件另生成一张应收单 R0 "
    "重新挂应收（主键、单号由 U8 分配），往来单位是票据的客户，科目是登记收款单的应收控制科目，保存后补写处理标记、"
    "余额 0 和审核人（没有审核行，不能经 vouchers/verify 审核、弃审）；缺省摘要「退回{客户名称}电子承兑」；登记时的收款单不动）。"
    "U8 的处理窗体没有可调用的组件，桥按该窗体执行的 SQL 写（实测核对），在一个事务里做："
    "票据处理行 AP_Note_Sub、票据余额、往来明细、分包票据的可用子票区间（退回另有应收单），提交前后核对。"
    "note 是票据号（字符串）或票据主键 AP_Note.Auto_ID（整数）。"
    "amount 省略时背书取 ap_lines 合计，其余取票据余额（分包票据取第一段可用区间）；可小于余额（部分处理）。"
    "sub_start / sub_end 是分包票据的子票区间（成对给出，每个号 0.01 元，同时给 amount 时须一致）。"
    "结算、贴现须给 bank_code（末级银行科目），bank_name 省略取科目上级名称；退回不收 bank_*、贴现、背书的字段；"
    "贴现另收 expense（贴现息、手续费）、interest（票据利息）、rate（贴现率 %），"
    "净额 = 金额 + interest - expense，须大于 0。"
    "背书须给 vendor（被背书的供应商）和 ap_lines（1 到 50 项 {type, id, line_id?, amount}，"
    "01 / 02 采购发票、P0 应付单，不收付款单 49），合计等于背书金额。"
    "处理日期就是登录日期 date，须在应收（背书另查应付）未结账的期间内，不早于签发、收票日期。"
    "响应带处理号 cancel_no、style、note（id、code、partner、partner_name）、amount、"
    "remaining（处理后的票据余额），贴现另带 net、expense、interest、rate，背书另带 vendor、ap_rows，"
    "退回另带 r0_id、r0_code（生成的应收单）。"
    "应付票据（flag AP）只收 settle（PJJAP…，往来明细记借方，银行在贷方）和 return（CLAP…，退还供应商，"
    "另生成一张应付单 P0，科目是登记付款单的应付控制科目，响应带 p0_id、p0_code），discount、endorse 400；"
    "应付票据写入是第二级写入，只在测试账套上验证过；"
    "默认关闭（桥未开 enableReplicatedWrites 时 403 feature_disabled），打开后只对 CO 桥配置为测试账套（testAccounts）的账套开放，其他账套 403 test_account_only，不登录 U8。"
    "功能权限：结算 AP240203 / AP240201，退回「票据转出」AP240202（都另收票据录入 AP0504），数据权限按供应商。"
    "404 not_found：票据、科目或供应商不存在。409 state_mismatch：外币票据、已换票、已退回、余额为 0 或不足、"
    "不是分包票据却给了子票区间、区间不可用、登记生成的收款单未审核（先 vouchers/verify）、"
    "退回日期早于票据已有的处理、分包票据没有退回全部可用区间、取不到应收控制科目或应收单模板、"
    "科目不是末级银行科目、期间已结账、票据在 U8 客户端被占用等；核对不符回滚，409 u8_rejected；"
    "提交后回读不符 504 outcome_unknown（先按处理号核对，不要直接重试）。"
    "撤销用 arap/process/cancel（处理号；退回的应收单已被核销等引用时 409），"
    "制单用 arap/process/voucher（贴现可给 expense_code）。"
    "功能权限按操作：结算 AR240203 / AR240201、贴现 AR240205、背书 AR240206、退回「票据退票」AR240207 或「票据转出」AR240202，都另收票据录入 AR0504；数据权限按票据的客户、部门、业务员，背书另按每张应付单据。"
    "dry_run 为 true 时在事务里执行后回滚（rollback 模式），什么都不写入，返回 DryRunOut"
    "（退回时 U8 取过的应收单号不退，真做时会跳号）。"
)


def _text(value: str | None, name: str) -> str | None:
    if value is not None and (not value.strip() or has_control(value)):
        raise ValueError(f"{name} 无效")
    return value


def _range_amount(start: int, end: int) -> Decimal:
    return Decimal(end - start + 1) / 100


class CoNoteProcIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(
        ..., description="AR 应收票据、AP 应付票据（只收 settle、return，第二级写入，默认关闭，打开后只对测试账套开放），也是登录子系统"
    )
    op: Literal["settle", "discount", "endorse", "return"] = Field(
        ...,
        description="settle 托收 / 结算（9A）、discount 贴现（9D）、endorse 背书冲应付（9E）、"
        "return 退回（9C，生成应收单；应付票据生成应付单）",
    )
    note: StrictInt | StrictStr = Field(..., description="票据号（字符串，最多 120 字）或票据主键 Auto_ID（整数）")
    amount: float | None = Field(
        None,
        gt=0,
        le=_AMOUNT_MAX,
        allow_inf_nan=False,
        strict=True,
        description="处理金额（本位币），最多两位小数；省略时背书取 ap_lines 合计，其余取票据余额",
    )
    sub_start: StrictInt | None = Field(
        None, ge=1, le=_SERIAL_MAX, description="分包票据的子票区间起号，与 sub_end 成对"
    )
    sub_end: StrictInt | None = Field(None, ge=1, le=_SERIAL_MAX, description="子票区间止号，不小于 sub_start")
    bank_code: str | None = Field(
        None, min_length=1, max_length=40, description="结算银行科目（末级银行科目）：结算、贴现必填"
    )
    bank_name: str | None = Field(None, min_length=1, max_length=100, description="银行名称；省略取科目上级的名称")
    expense: float | None = Field(
        None, ge=0, le=_AMOUNT_MAX, allow_inf_nan=False, strict=True, description="贴现：贴现息、手续费，缺省 0"
    )
    interest: float | None = Field(
        None, ge=0, le=_AMOUNT_MAX, allow_inf_nan=False, strict=True, description="贴现：票据利息，缺省 0"
    )
    rate: float | None = Field(
        None, ge=0, le=100, allow_inf_nan=False, strict=True, description="贴现：贴现率（%），最多六位小数"
    )
    vendor: str | None = Field(None, min_length=1, max_length=20, description="背书：被背书的供应商编码，必填")
    ap_lines: list[ProcLineIn] | None = Field(
        None,
        min_length=1,
        max_length=_LINES_MAX,
        description="背书：要冲的应付单据 1 到 50 项（01 / 02 / P0，写法同 arap/transfer），合计等于背书金额",
    )
    digest: str | None = Field(None, max_length=120, description="摘要")
    dry_run: DryRunFlag = False

    @field_validator("note")
    @classmethod
    def _note(cls, value: int | str) -> int | str:
        if isinstance(value, int):
            if not 1 <= value <= _ID_MAX:
                raise ValueError("note 作为票据主键必须是 1 到 2147483647 的整数")
            return value
        if not value.strip() or len(value.strip()) > 120 or has_control(value):
            raise ValueError("note 无效")
        return value

    @field_validator("amount")
    @classmethod
    def _amount(cls, value: float | None) -> float | None:
        return _cents(value)

    @field_validator("expense", "interest")
    @classmethod
    def _fee(cls, value: float | None, info) -> float | None:
        if value is not None and Decimal(str(value)).as_tuple().exponent < -2:
            raise ValueError(f"{info.field_name} 必须不小于 0、不超过 1000000000000、最多两位小数")
        return value

    @field_validator("rate")
    @classmethod
    def _rate(cls, value: float | None) -> float | None:
        if value is not None and Decimal(str(value)).as_tuple().exponent < -6:
            raise ValueError("rate 必须在 0 到 100 之间、最多六位小数")
        return value

    @field_validator("bank_code", "bank_name", "vendor")
    @classmethod
    def _codes(cls, value: str | None, info) -> str | None:
        return _text(value, info.field_name)

    @field_validator("digest")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        if value is not None and (has_control(value) or len(value.strip()) > 120):
            raise ValueError("digest 必须是不含控制字符、最多 120 字的字符串")
        return value

    @model_validator(mode="after")
    def _shape(self) -> CoNoteProcIn:
        if self.flag == "AP" and self.op not in _AP_OPS:
            raise ValueError(AP_OPS_REFUSED)
        for ops, title, names in _OP_FIELDS:
            if self.op not in ops:
                for name in names:
                    if getattr(self, name) is not None:
                        raise ValueError(f"{name} 只用于{title}")
        self._range()
        if self.op in ("settle", "discount") and self.bank_code is None:
            raise ValueError("缺少 bank_code（结算银行科目）")
        if self.op == "endorse":
            self._endorse()
        return self

    def _range(self) -> None:
        if self.sub_start is None and self.sub_end is None:
            return
        if self.sub_start is None or self.sub_end is None:
            raise ValueError("sub_start 与 sub_end 要同时给出")
        if self.sub_start > self.sub_end:
            raise ValueError("sub_start 不能大于 sub_end")
        if self.amount is not None and _range_amount(self.sub_start, self.sub_end) != Decimal(str(self.amount)):
            raise ValueError("amount 与子票区间的金额不一致（区间每个号 0.01 元）")

    def _endorse(self) -> None:
        if self.vendor is None:
            raise ValueError("缺少 vendor（供应商编码）")
        if self.ap_lines is None:
            raise ValueError("ap_lines 必须是 1 到 50 项")
        if any(line.type == "49" for line in self.ap_lines):
            raise ValueError("背书不支持冲付款单（49），请在 U8 客户端处理")
        _check_lines(self.ap_lines, "ap_lines", _AP_TYPES, "背书", fold=True)
        total = _total(self.ap_lines)
        if self.amount is not None and Decimal(str(self.amount)) != total:
            raise ValueError("ap_lines 的金额合计必须等于背书金额 amount")
        if self.sub_start is not None and _range_amount(self.sub_start, self.sub_end) != total:
            raise ValueError("ap_lines 的金额合计必须等于子票区间的金额")

    def audit_ref(self) -> str:
        return f"{self.op}:{self.note}"


class NoteRefOut(BaseModel):
    model_config = PASS
    id: Scalar = Field(None, description="票据主键 AP_Note.Auto_ID")
    code: Scalar = Field(None, description="票据号")
    partner: Scalar = Field(None, description="票据的往来单位编码")
    partner_name: Scalar = Field(None, description="往来单位名称")


class CoNoteProcOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    acc: Scalar = Field(None, description="账套号")
    flag: Scalar = Field(None, description="AR 或 AP")
    op: Scalar = Field(None, description="settle、discount、endorse 或 return")
    style: Scalar = Field(None, description="处理方式：9A 结算、9D 贴现、9E 背书、9C 退回")
    cancel_no: Scalar = Field(
        None, description="处理号（PJJAR… / PJTAR… / PJBAR… / CLAR…，应付票据 PJJAP… / CLAP…），取消、制单要用"
    )
    date: Scalar = Field(None, description="处理日期")
    note: NoteRefOut | None = Field(None, description="处理的票据")
    amount: Scalar = Field(None, description="处理金额（本位币）")
    remaining: Scalar = Field(None, description="处理后的票据余额")
    sub_start: Scalar = Field(None, description="子票区间起号；不是分包票据为 null")
    sub_end: Scalar = Field(None, description="子票区间止号")
    digest: Scalar = Field(None, description="摘要")
    bank_code: Scalar = Field(None, description="结算、贴现：银行科目")
    bank_name: Scalar = Field(None, description="结算、贴现：银行名称")
    net: Scalar = Field(None, description="贴现：净额 = 金额 + interest - expense")
    expense: Scalar = Field(None, description="贴现：贴现息、手续费")
    interest: Scalar = Field(None, description="贴现：票据利息")
    rate: Scalar = Field(None, description="贴现：贴现率（%）")
    vendor: Scalar = Field(None, description="背书：被背书的供应商")
    ap_rows: list[ProcRowOut] | None = Field(None, description="背书：冲掉的应付单据各行（amount、remaining）")
    r0_id: Scalar = Field(None, description="退回：生成的应收单主键 Ap_Vouch.Auto_ID")
    r0_code: Scalar = Field(None, description="退回：生成的应收单号（类型 ar_bill）")
    p0_id: Scalar = Field(None, description="应付票据退回：生成的应付单主键 Ap_Vouch.Auto_ID")
    p0_code: Scalar = Field(None, description="应付票据退回：生成的应付单号（类型 ap_bill）")
