"""应收应付处理的批次操作：取消处理（arap/process/cancel）、处理制单（arap/process/voucher）、
汇兑损益与取消（arap/exchange_gain、arap/exchange_gain/cancel，只对测试账套开放）的请求。
校验规则与桥的 ArapProcCancelReq、ArapProcVoucherReq、ArapExGainReq 一致；出参见 co_models_arap_proc_out。"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Literal

from pydantic import Field, StrictBool, field_validator, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_arap_proc import has_control
from u8co_api.co_models_arap_voucher import CashItems
from u8co_api.co_models_dry import DryRunFlag

_VOUCHER_MAX = 50
_LIST_MAX = 200
_TEXT_MAX = 120
_TEST_ONLY = "默认关闭（桥未开 enableReplicatedWrites 时 403 feature_disabled），打开后只对 CO 桥配置为测试账套（testAccounts）的账套开放，其他账套 403 test_account_only，不登录 U8。"
_DRY_ROLLBACK = "dry_run 为 true 时在事务里执行后回滚（rollback 模式），什么都不写入，返回 DryRunOut。"

# 处理号前缀 → (处理方式, flag)。前缀由 U8 的 Ap_Proc_CancelNo 编出（cType + cFlag）。
PROC_PREFIXES = {
    "YCFAP": ("9I", "AR"),
    "FCYAR": ("9J", "AP"),
    "BZAR": ("BZ", "AR"),
    "BZAP": ("BZ", "AP"),
    "HRAR": ("9N", "AR"),
    "HPAP": ("9N", "AP"),
    "SYRAR": ("9M", "AR"),
    "SYPAP": ("9M", "AP"),
    # 票据处理：PJJAR 结算 9A、PJTAR 贴现 9D、PJBAR 背书 9E、CLAR 退回 9C。应付票据只开放结算 PJJAP、退回 CLAP
    # （只对测试账套开放，桥在登录前查），贴现、背书的 PJTAP / PJBAP 桥同样 400。
    "PJJAR": ("9A", "AR"),
    "PJTAR": ("9D", "AR"),
    "PJBAR": ("9E", "AR"),
    "CLAR": ("9C", "AR"),
    "PJJAP": ("9A", "AP"),
    "CLAP": ("9C", "AP"),
    # 坏账：发生 9G、收回 9H、计提 9F 共用 HZAR（只有应收），具体处理方式由桥按往来明细或坏账准备参数行判断。
    "HZAR": ("HZ", "AR"),
}
_AP_NOTE = re.compile(r"^(PJTAP|PJBAP)[0-9]{1,20}\Z")
_AP_NOTE_REFUSED = "应付票据只支持结算、退回（处理号 PJJAP、CLAP），贴现、背书（PJTAP、PJBAP）请在 U8 客户端处理"
_AP_NOTE_TIER2 = (
    "应付票据（PJJAP… 结算、CLAP… 退回，flag AP）是第二级写入，只在测试账套上验证过；" + _TEST_ONLY
)
_STYLE_TITLES = {
    "9I": "应收冲应付",
    "9J": "应付冲应收",
    "BZ": "并账",
    "9N": "红票对冲",
    "9M": "汇兑损益",
    "9A": "票据结算",
    "9D": "票据贴现",
    "9E": "票据背书",
    "9C": "票据退回",
    "HZ": "坏账处理",
}
_NO = re.compile(r"^([A-Z]{4,5})[0-9]{1,20}\Z")
_CANCEL_NO = r"^(YCFAP|FCYAR|BZAR|BZAP|HRAR|HPAP|PJJAR|PJTAR|PJBAR|CLAR|PJJAP|CLAP|HZAR)[0-9]{1,20}$"
_FLAG_NOTE = "YCFAP、BZAR、HRAR、坏账的 HZAR 和票据的 PJJAR 等用 AR；FCYAR、BZAP、HPAP 和应付票据的 PJJAP、CLAP 用 AP"
_EXGAIN_NO = re.compile(r"^SY(RAR|PAP)[0-9]{1,20}\Z")

CANCEL_SUMMARY = "取消应收应付处理"
CANCEL_HELP = (
    "按处理号整批取消一次应收冲应付（YCFAP…，flag AR）、应付冲应收（FCYAR…，flag AP）、并账（BZAR… / BZAP…，flag 跟号）"
    "或红票对冲（HRAR… / HPAP…），以及应收票据处理（PJJAR… 结算、PJTAR… 贴现、PJBAR… 背书、CLAR… 退回，flag AR；"
    "加回票据余额和分包票据的可用子票区间，删票据处理行，背书另加回被背书供应商的应付单据余额，退回另删它生成的"
    "应收单），相当于 U8 应收（应付）款管理「其他处理 → 取消操作」。" + _AP_NOTE_TIER2 + "退回另删生成的应付单。"
    "U8 这一步没有可调用的组件，"
    "桥按 U8 界面执行的 SQL 写（实测核对），在一个事务里做：加回收付款单行、应收应付单和发票累计核销的余额（并账成对 ± 行净额为 0，只删行），"
    "删掉该处理号在涉及的往来明细里的行，提交前核对每个余额都精确回到处理前。cancel_no 前缀须与 flag 一致，否则 400。"
    "响应带 style、kind、两边删掉的行数 ar_rows / ap_rows，items 是删掉的处理行按单据行汇总的借贷原币，restored 是加"
    "回了"
    "余额的单据行（amount 加回、remaining 取消后的余额）。"
    "404 not_found：处理号不存在。409 state_mismatch：已制单（「已制单，请先取消制单」，先用 arap/voucher/delete 删凭"
    "证）；"
    "处理所在期间已结账（应收冲应付、应付冲应收两边都查）；单据在这次处理之后还有核销、汇兑损益等处理（先取消后面的）；"
    "应收一侧有未审核的收款单；记录不完整或涉及合同、代理进口、本接口不支持的单据类型（请在 U8 客户端取消）。"
    "核对不符回滚，409 u8_rejected。功能权限：取消操作 AR0807 / AP0807；"
    "数据权限按处理行的往来单位、部门、业务员。"
    "坏账处理号 HZAR…（flag AR）："
    + _TEST_ONLY
    + "坏账发生（9G）加回单据余额并把坏账准备余额加回；坏账收回（9H）把收款单恢复为未审核、余额复原，坏账准备余额减回；"
    "计提（9F）把该年度坏账准备参数行的余额减去累计计提并清空计提日期，只能取消该年度最后一次计提（同一年度计提多次时"
    "累计数合在一起，前面的不能单独取消）。" + _DRY_ROLLBACK
)
VOUCHER_SUMMARY = "应收应付处理制单"
VOUCHER_HELP = (
    "U8「制单处理」里给处理批次生成总账凭证：同一类处理的 1 到 50 个处理号合成一张转账凭证。应收冲应付 YCFAP… / "
    "应付冲应收 FCYAR…（凭证来源 coutsign ZZ）、并账 BZAR… / BZAP…（BZ）、红票对冲 HRAR… / HPAP…、汇兑损益 SYRAR… / "
    "SYPAP…（SY）、应收票据处理 PJJAR… 结算 / PJTAR… 贴现 / PJBAR… 背书 / CLAR… 退回（PJ；"
    + _AP_NOTE_TIER2
    + "应付结算借应付票据、贷银行，缺省凭证类别「付」；退回实质是借应付票据、贷应付账款，同应收退回换方向取负）、"
    "坏账 HZAR…（JT：计提借坏账准备参数里的对方科目、贷坏账准备科目；发生借坏账准备、贷单据的应收科目带客户；"
    "收回借收款单的结算科目、贷坏账准备；一次只能是同一种坏账处理，"
    + _TEST_ONLY
    + "科目须是登录年度的末级科目，否则 409）；"
    "前缀须与 flag 一致（YCFAP、BZAR、HRAR、SYRAR、PJJAR 等用 AR，其余用 AP），一次只能给同一类。"
    "票据贴现（PJTAR…）可给 expense_code（贴现费用科目，省略取基本科目设置），其他处理不收。"
    "票据处理和坏账收回的凭证按「现金流量项目数据来源」给对方分录挂现金流量项目，推不出时 409，可用 cash_items"
    "（{科目编码: 项目编码}）指定；其他处理的凭证不挂项目，给了 cash_items 400。"
    "分录由桥照 U8 的规则按处理明细拼（往来科目取明细上的科目，带客户 / 供应商辅助项），经凭证导入 U8PzInsert 保存"
    "（它自己提交），再按 U8 界面的回写（实测核对）：两张往来明细里这些批次的全部行写 cPZid / cGLSign / iGLno_id / dPZDate。"
    "sign 是凭证类别，省略为「转」；voucher_date 是制单日期，省略取各批次处理日期中最晚的，不能早于处理日期、须在登录"
    "年度内"
    "且总账未结账；digest 省略取往来明细上的摘要。汇兑损益另须 pl_code（汇兑损益科目编码，U8 在制单界面选，桥不猜），"
    "其他处理不收 pl_code；汇兑损益制单" + _TEST_ONLY + "只做本币批次。"
    "响应同 arap/voucher：pz_id（外部业务号）、voucher、lines，另带 proc_style、out_sign、cancel_nos、rows。"
    "409 state_mismatch：批次已制单、处理类型不符、外币批次、科目缺失、借贷不平、期间已结账、凭证类别不存在；"
    "409 u8_rejected：U8 拒绝保存凭证（原文带回）；回写失败时桥删掉刚生成的凭证并 409，删不掉 504。"
    "收到 504 outcome_unknown 时先在总账里按外部业务号核对，不要直接重试。取消制单用 arap/voucher/delete。"
    "功能权限同 arap/voucher（AR0508 / AP0508）。"
    "dry_run 为 true 时（validate 模式）算好凭证、在保存之前停下，计划凭证在 detail.voucher，什么都不写入，返回 DryRu"
    "nOut。"
)
EXGAIN_SUMMARY = "应收 / 应付汇兑损益"
EXGAIN_HELP = (
    "U8 应收（应付）款管理的汇兑损益（处理方式 9M）：按外币余额和调整汇率计算本币差额，每个（往来单位、单据）一个处理号"
    "（SYRAR… / SYPAP…），写往来明细，发票累计核销经 U8 的回写组件。"
    + _TEST_ONLY
    + "flag 为 AR 或 AP（也是登录子系统）；登记日期就是登录日期 date，须在本系统未结账的期间内。"
    "currency 是外币名称（必填）；"
    "rate 省略取该期外币设置里的调整汇率，没有则 409「本期没有调整汇率」；partners 是 1 到 200 个客户（供应商）编码，"
    "省略为全部；settle_cleared 缺省 true：原币已结清只剩本币尾差的单据一并结清。响应带 fiscal_year、period、rate、rows"
    "（写入的明细行数）、total（本币差额合计），batches 是每个处理号的往来单位、单据类型代码 type、单号 id 和本币差额"
    " diff。"
    "409 state_mismatch：币种不存在或是本位币、日期不在会计期间或早于启用日期、期间已结账、批次过多；"
    "核对不符回滚，409 u8_rejected。撤销用 arap/exchange_gain/cancel，制单用 arap/process/voucher（须给 pl_code）。"
    "功能权限：AR0507 / AP0507；数据权限按往来单位。" + _DRY_ROLLBACK
)
EXGAIN_CANCEL_SUMMARY = "取消应收 / 应付汇兑损益"
EXGAIN_CANCEL_HELP = (
    "取消汇兑损益（U8「取消操作」里的汇兑损益）。"
    + _TEST_ONLY
    + "cancel_nos 是 1 到 200 个处理号（SYRAR… 用 flag AR，SYPAP… 用 AP）；"
    "省略时取消登记日期为登录日期 date 的全部未制单"
    "汇兑损益（响应的 by_date）。桥按 U8 界面执行的取消 SQL 写（实测核对），在一个事务里加回余额、删掉明细行并核对。"
    "响应带 rows（删掉的行数）、batches、total。404 not_found：处理号不存在或该日没有汇兑损益。409 state_mismatch：已"
    "制单"
    "（先用 arap/voucher/delete 删凭证）、期间已结账、之后还有其他处理；核对不符回滚，409 u8_rejected。"
    "功能权限：取消操作 AR0807 / AP0807。" + _DRY_ROLLBACK
)


def _printable(value: str, name: str) -> str:
    if has_control(value):
        raise ValueError(f"{name} 不能含控制字符")
    return value


class CoArapProcCancelIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统")
    cancel_no: str = Field(
        ...,
        pattern=_CANCEL_NO,
        description=(
            "处理号：YCFAP 应收冲应付、FCYAR 应付冲应收、BZAR / BZAP 并账、HRAR / HPAP 红票对冲、"
            "PJJAR / PJTAR / PJBAR / CLAR 应收票据结算、贴现、背书、退回，HZAR 坏账发生、收回、计提，后接数字"
        ),
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _same_flag(self) -> CoArapProcCancelIn:
        if _prefix(self.cancel_no)[1] != self.flag:
            raise ValueError("cancel_no 与 flag 不一致（" + _FLAG_NOTE + "）")
        return self

    def audit_ref(self) -> str:
        return self.cancel_no


def _prefix(no: str) -> tuple[str, str]:
    """处理号 → (处理方式, flag)；不认识返回 ("", "")。"""
    match = _NO.match(no)
    return PROC_PREFIXES.get(match.group(1), ("", "")) if match else ("", "")


class CoArapProcVoucherIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统和凭证的制单系统")
    cancel_nos: list[str] = Field(
        ...,
        min_length=1,
        max_length=_VOUCHER_MAX,
        description="同一类处理的 1 到 50 个处理号（如 YCFAP000000000001），不能重复",
    )
    sign: str | None = Field(None, min_length=1, max_length=2, description="凭证类别；省略为「转」")
    voucher_date: str | None = Field(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="制单日期；省略取各批次处理日期中最晚的"
    )
    digest: str | None = Field(None, max_length=_TEXT_MAX, description="摘要；省略取往来明细上的摘要")
    pl_code: str | None = Field(
        None,
        min_length=1,
        max_length=40,
        pattern=r"^\S+$",
        description="汇兑损益科目编码（如 660399）：只用于汇兑损益，且必填",
    )
    expense_code: str | None = Field(
        None,
        min_length=1,
        max_length=40,
        pattern=r"^\S+$",
        description="贴现费用科目编码：只用于票据贴现（PJTAR），省略取基本科目设置",
    )
    cash_items: CashItems = None
    dry_run: DryRunFlag = False

    @field_validator("digest")
    @classmethod
    def _digest(cls, value: str | None) -> str | None:
        return value if value is None else _printable(value, "digest")

    @model_validator(mode="after")
    def _one_kind(self) -> CoArapProcVoucherIn:
        styles = set()
        for no in self.cancel_nos:
            style, flag = _prefix(no)
            if _AP_NOTE.match(no):
                raise ValueError(_AP_NOTE_REFUSED)
            if not style:
                raise ValueError(
                    "批次号必须是 YCFAP、FCYAR、BZAR、BZAP、SYRAR、SYPAP、HRAR、HPAP、"
                    "PJJAR、PJTAR、PJBAR、CLAR、PJJAP、CLAP、HZAR 后接数字"
                )
            if flag != self.flag:
                raise ValueError(
                    "批次号与 flag 不一致（YCFAP、BZAR、SYRAR、HRAR、HZAR 和票据的 PJJAR 等用 AR；"
                    "FCYAR、BZAP、SYPAP、HPAP 和应付票据的 PJJAP、CLAP 用 AP）"
                )
            styles.add(style)
        if len(styles) > 1:
            raise ValueError("一次只能给同一类处理的批次号（" + _STYLE_TITLES[_prefix(self.cancel_nos[0])[0]] + "）")
        if len(set(self.cancel_nos)) != len(self.cancel_nos):
            raise ValueError("cancel_nos 有重复的批次号")
        self._pl_code(styles == {"9M"})
        if self.expense_code is not None and styles != {"9D"}:
            raise ValueError("expense_code 只用于票据贴现（PJTAR）制单")
        return self

    def _pl_code(self, needed: bool) -> None:
        if needed and self.pl_code is None:
            raise ValueError("汇兑损益制单必须给 pl_code（汇兑损益科目编码，如 660399）")
        if not needed and self.pl_code is not None:
            raise ValueError("pl_code 只用于汇兑损益（SYRAR / SYPAP）制单")

    def audit_ref(self) -> str:
        more = len(self.cancel_nos) - 1
        return self.cancel_nos[0] + (f"+{more}" if more else "")


def _unique(values: list[str], name: str) -> list[str]:
    if len(set(values)) != len(values):
        raise ValueError(f"{name} 里有重复的编码" if name == "partners" else f"{name} 里有重复的处理号")
    return values


class CoArapExGainIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统")
    currency: str = Field(..., min_length=1, max_length=20, description="外币名称，如 美元")
    rate: float | None = Field(
        None,
        gt=0,
        le=1000000,
        allow_inf_nan=False,
        strict=True,
        description="调整汇率，最多 10 位小数；省略取该期外币设置里的调整汇率",
    )
    partners: list[str] | None = Field(
        None, min_length=1, max_length=_LIST_MAX, description="1 到 200 个客户（供应商）编码，不能重复；省略为全部"
    )
    settle_cleared: StrictBool | None = Field(None, description="缺省 true：原币已结清只剩本币尾差的单据一并结清")
    dry_run: DryRunFlag = False

    @field_validator("currency")
    @classmethod
    def _currency(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("缺少 currency（外币名称，如 美元）")
        return _printable(value, "currency")

    @field_validator("rate")
    @classmethod
    def _scale(cls, value: float | None) -> float | None:
        if value is not None and Decimal(str(value)).as_tuple().exponent < -10:
            raise ValueError("rate 必须大于 0、不超过 1000000、最多 10 位小数")
        return value

    @field_validator("partners")
    @classmethod
    def _partners(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        for code in value:
            if not code.strip() or len(code.strip()) > 20 or has_control(code):
                raise ValueError("partners 里的编码无效")
        return _unique([code.strip() for code in value], "partners")

    def audit_ref(self) -> str:
        return self.currency


class CoArapExGainCancelIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统")
    cancel_nos: list[str] | None = Field(
        None,
        min_length=1,
        max_length=_LIST_MAX,
        description="1 到 200 个处理号（SYRAR… / SYPAP…）；省略时取消登记日期为登录日期 date 的全部未制单汇兑损益",
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _nos(self) -> CoArapExGainCancelIn:
        for no in self.cancel_nos or []:
            if _EXGAIN_NO.match(no) is None:
                raise ValueError("cancel_nos 里必须是 SYRAR 或 SYPAP 后接数字的处理号")
            if _prefix(no)[1] != self.flag:
                raise ValueError("处理号与 flag 不一致（SYRAR 是应收、SYPAP 是应付）")
        if self.cancel_nos is not None:
            _unique(self.cancel_nos, "cancel_nos")
        return self

    def audit_ref(self) -> str:
        if not self.cancel_nos:
            return "date:" + (self.date or "")
        more = len(self.cancel_nos) - 1
        return self.cancel_nos[0] + (f"+{more}" if more else "")
