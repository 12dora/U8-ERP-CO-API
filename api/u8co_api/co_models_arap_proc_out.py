"""应收应付处理各路由的响应：字段名与桥一致，放行桥多给的字段（co/bridge/CHECKLIST.md「响应与错误码」）。
除 ok 外都可空：桥的形状以后加字段不影响调用方。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from u8co_api.co_models_gl import PASS, Scalar


class ProcRowOut(BaseModel):
    model_config = PASS
    type: Scalar = Field(None, description="U8 单据类型代码")
    id: Scalar = Field(None, description="单据号")
    doc_id: Scalar = Field(None, description="单据主键")
    line_id: Scalar = Field(None, description="发票行；整单时省略")
    amount: Scalar = Field(None, description="本次金额（原币）")
    amount_native: Scalar = Field(None, description="本次金额（本币）")
    remaining: Scalar = Field(None, description="处理后的余额（原币）")


class CoArapTransferOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    acc: Scalar = Field(None, description="账套号")
    flag: Scalar = Field(None, description="AR 或 AP")
    style: Scalar = Field(None, description="处理方式：9I 应收冲应付，9J 应付冲应收")
    cancel_no: Scalar = Field(None, description="处理号（YCFAP… / FCYAR…），取消、制单要用")
    date: Scalar = Field(None, description="处理日期")
    customer: Scalar = Field(None, description="客户编码")
    vendor: Scalar = Field(None, description="供应商编码")
    currency: Scalar = Field(None, description="币种")
    amount: Scalar = Field(None, description="转账金额（原币）")
    digest: Scalar = Field(None, description="摘要")
    ar_rows: list[ProcRowOut] | None = Field(None, description="应收一侧每张单据每一行")
    ap_rows: list[ProcRowOut] | None = Field(None, description="应付一侧每张单据每一行")


class MergeRowOut(ProcRowOut):
    from_remaining: Scalar = Field(None, description="并账后 from 名下的余额（原币）")
    to_remaining: Scalar = Field(None, description="并账后 to 名下的余额（原币）")


class CoArapMergeOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    acc: Scalar = Field(None, description="账套号")
    flag: Scalar = Field(None, description="AR 或 AP")
    cancel_no: Scalar = Field(None, description="并账号（BZAR… / BZAP…），取消、制单要用")
    date: Scalar = Field(None, description="并账日期")
    sender: Scalar = Field(None, alias="from", description="并出的往来单位")
    to: Scalar = Field(None, description="并入的往来单位")
    currency: Scalar = Field(None, description="币种")
    digest: Scalar = Field(None, description="摘要")
    amount: Scalar = Field(None, description="并账金额合计（原币）")
    rows: list[MergeRowOut] | None = Field(None, description="每张单据（行）的本次金额和并账后两边的余额")


class CoArapRedOffsetOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    acc: Scalar = Field(None, description="账套号")
    flag: Scalar = Field(None, description="AR 或 AP")
    cancel_no: Scalar = Field(None, description="处理号（HRAR… / HPAP…），取消、制单要用")
    amount: Scalar = Field(None, description="对冲金额（原币）")
    red_rows: list[ProcRowOut] | None = Field(None, description="红字一侧每张单据（行）")
    blue_rows: list[ProcRowOut] | None = Field(None, description="蓝字一侧每张单据（行）")


class ProcCancelItemOut(BaseModel):
    model_config = PASS
    ledger: Scalar = Field(None, description="往来明细所在的账：AR 或 AP")
    type: Scalar = Field(None, description="单据类型")
    id: Scalar = Field(None, description="单据主键")
    code: Scalar = Field(None, description="单据号")
    line_id: Scalar = Field(None, description="发票行（iBVid）；整单时省略")
    partner: Scalar = Field(None, description="往来单位")
    debit: Scalar = Field(None, description="删掉的处理行借方（原币）")
    credit: Scalar = Field(None, description="删掉的处理行贷方（原币）")


class ProcRestoredOut(BaseModel):
    model_config = PASS
    ledger: Scalar = Field(None, description="AR 或 AP")
    type: Scalar = Field(None, description="单据类型")
    id: Scalar = Field(None, description="单据主键")
    code: Scalar = Field(None, description="单据号")
    line_id: Scalar = Field(None, description="收付款单行；应收应付单省略")
    amount: Scalar = Field(None, description="加回的金额")
    remaining: Scalar = Field(None, description="取消后的余额")


class CoArapProcCancelOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已取消")
    acc: Scalar = Field(None, description="账套号")
    flag: Scalar = Field(None, description="AR 或 AP")
    cancel_no: Scalar = Field(None, description="已取消的处理号")
    style: Scalar = Field(None, description="处理方式：9I、9J、BZ、9N、9A 等；坏账 9F、9G、9H")
    kind: Scalar = Field(None, description="处理种类：transfer、merge 等")
    ar_rows: Scalar = Field(None, description="应收明细删掉的行数")
    ap_rows: Scalar = Field(None, description="应付明细删掉的行数")
    items: list[ProcCancelItemOut] | None = Field(None, description="删掉的处理行，按单据行汇总")
    restored: list[ProcRestoredOut] | None = Field(None, description="加回了余额的单据行")


class ProcVoucherKeyOut(BaseModel):
    model_config = PASS
    year: Scalar = Field(None, description="会计年度")
    period: Scalar = Field(None, description="会计期间")
    sign: Scalar = Field(None, description="凭证类别")
    no: Scalar = Field(None, description="凭证号")
    num: Scalar = Field(None, description="凭证号文本，如 转-0172")
    date: Scalar = Field(None, description="制单日期")


class ProcVoucherLineOut(BaseModel):
    model_config = PASS
    entry: Scalar = Field(None, description="分录号")
    account: Scalar = Field(None, description="科目编码")
    digest: Scalar = Field(None, description="摘要")
    debit: Scalar = Field(None, description="借方金额")
    credit: Scalar = Field(None, description="贷方金额")
    customer: Scalar = Field(None, description="客户")
    supplier: Scalar = Field(None, description="供应商")
    bill_code: Scalar = Field(None, description="该行的来源单据号（凭证行 coutid）")


class CoArapProcVoucherOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已制单")
    acc: Scalar = Field(None, description="账套号")
    flag: Scalar = Field(None, description="AR 或 AP")
    proc_style: Scalar = Field(None, description="处理方式：9I、9J、BZ、9N、9M、9F、9G、9H 等")
    out_sign: Scalar = Field(None, description="凭证来源 coutsign：ZZ、BZ、SY、JT 等")
    cancel_nos: list[str] | None = Field(None, description="本凭证覆盖的处理号")
    rows: Scalar = Field(None, description="回写的往来明细行数（两张表合计）")
    pz_id: Scalar = Field(None, description="外部业务号（往来明细 cPZid = 凭证 coutno_id），取消制单要用")
    making_system: Scalar = Field(None, description="导入凭证时用的制单系统")
    voucher: ProcVoucherKeyOut | None = Field(None, description="生成的凭证")
    lines: list[ProcVoucherLineOut] | None = Field(None, description="凭证分录（回读）")


class ExGainBatchOut(BaseModel):
    model_config = PASS
    cancel_no: Scalar = Field(None, description="处理号（SYRAR… / SYPAP…）")
    partner: Scalar = Field(None, description="往来单位")
    type: Scalar = Field(None, description="U8 单据类型代码")
    id: Scalar = Field(None, description="单据号")
    lines: Scalar = Field(None, description="该处理号的明细行数")
    diff: Scalar = Field(None, description="本币差额（借正贷负）")


class CoArapExGainOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已完成")
    acc: Scalar = Field(None, description="账套号")
    flag: Scalar = Field(None, description="AR 或 AP")
    date: Scalar = Field(None, description="登记日期")
    fiscal_year: Scalar = Field(None, description="会计年度")
    period: Scalar = Field(None, description="会计期间")
    currency: Scalar = Field(None, description="外币")
    rate: Scalar = Field(None, description="使用的调整汇率")
    rows: Scalar = Field(None, description="写入的明细行数")
    batches: list[ExGainBatchOut] | None = Field(None, description="每个处理号")
    total: Scalar = Field(None, description="本币差额合计")


class CoArapExGainCancelOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已取消")
    acc: Scalar = Field(None, description="账套号")
    flag: Scalar = Field(None, description="AR 或 AP")
    by_date: Scalar = Field(None, description="按登记日期取消时的日期；按处理号取消时省略")
    rows: Scalar = Field(None, description="删掉的明细行数")
    batches: list[ExGainBatchOut] | None = Field(None, description="取消的处理号")
    total: Scalar = Field(None, description="取消的本币差额合计")
