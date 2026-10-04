"""核销记录查询 /v1/co/reports/arap_writeoffs 的请求和响应。校验规则与桥的 ReportsArapWriteoffReq 一致，响应放行桥多给的字段。

金额是数字（原币，两位小数）。after 是桥给的不透明游标，原样传回。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator, model_validator

from u8co_api.co_doctext import PAGE, op_doc
from u8co_api.co_models import CoAuth
from u8co_api.co_models_gl import DATE, PASS, Scalar
from u8co_api.co_models_reports import _AFTER, _NEXT, After, Word, _real_date

_FORBID = ConfigDict(extra="forbid")
_ID_MAX = 2147483647
_RECEIPT = {"AR": "ar_receipt", "AP": "ap_payment"}
_TARGETS = {"AR": ("sale_invoice", "ar_bill"), "AP": ("purchase_invoice", "ap_bill")}

WRITEOFFS_SUMMARY = "核销记录"
WRITEOFFS_HELP = op_doc(
    "列出应收（flag=AR）或应付（flag=AP）的核销批次。",
    (
        "用法",
        (
            PAGE,
            "过滤：往来单位 partner、核销号 cancel_no、登记日期 date_from / date_to（含两端）",
            "收付款单：receipt {type, id} 或 receipt_code 单号，二选一",
            "被核销单据：target {type, id} 或 target_code 单号，二选一",
        ),
    ),
    (
        "规则",
        (
            "按核销号（Ar_Detail / Ap_Detail.cCancelNo，cProcStyle=9P）分批",
            "本服务 arap/writeoff 做的和 U8 客户端做的都列",
            "按登记日期降序、核销号降序",
            "每批给出日期、会计年度和期间、往来单位、币种、操作员和合计 amount",
            "收付款单：type、id、line_id、code",
            "targets：被核销单据行的 type、id、line_id、code、amount",
            "gl_voucher、voucher：是否已制单及凭证",
            "cancellable：按取消核销（arap/writeoff/cancel）同一套规则只读判断",
            "cancellable 为 false 时，reason 是取消核销会返回的原因",
            "常见原因：已制单、期间已结账、之后还有其他处理、外币、不是一张收付款单对单据的核销",
            "判断不加锁，只是查询时刻的快照",
            "只查数据库，不调用 U8 组件",
        ),
    ),
    (
        "权限",
        (
            "功能权限（任一）：应收核销明细表 AR060107、手工核销 AR050201、取消操作 AR0807",
            "应付对应 AP060107 / AP050201 / AP0807",
            "数据权限按客户或供应商",
        ),
    ),
)


class WriteoffReceiptRef(BaseModel):
    model_config = _FORBID
    type: Literal["ar_receipt", "ap_payment"] = Field(..., description="flag=AR 时 ar_receipt，AP 时 ap_payment")
    id: StrictInt = Field(..., gt=0, le=_ID_MAX, description="收付款单主键 Ap_CloseBill.iID")


class WriteoffTargetRef(BaseModel):
    model_config = _FORBID
    type: Literal["sale_invoice", "ar_bill", "purchase_invoice", "ap_bill"] = Field(
        ..., description="flag=AR 时 sale_invoice / ar_bill，AP 时 purchase_invoice / ap_bill"
    )
    id: StrictInt = Field(..., gt=0, le=_ID_MAX, description="单据主键（SBVID、PBVID 或 Ap_Vouch.Auto_ID）")


class ReportWriteoffsIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统")
    partner: Word | None = Field(None, description="客户或供应商编码等于")
    receipt: WriteoffReceiptRef | None = Field(None, description="只看这张收付款单的核销；与 receipt_code 二选一")
    receipt_code: Word | None = Field(None, description="收付款单号等于；与 receipt 二选一")
    target: WriteoffTargetRef | None = Field(None, description="只看核销了这张单据的批次；与 target_code 二选一")
    target_code: Word | None = Field(None, description="被核销单据的单号等于；与 target 二选一")
    date_from: str | None = Field(None, pattern=DATE, description="登记日期下限 yyyy-MM-dd（含）")
    date_to: str | None = Field(None, pattern=DATE, description="登记日期上限 yyyy-MM-dd（含）")
    cancel_no: str | None = Field(
        None, pattern=r"^HX(AR|AP)[0-9]{1,20}$", description="核销号（HXAR… / HXAP…），前缀须与 flag 一致"
    )
    after: After | None = Field(None, description=_AFTER)
    limit: StrictInt | None = Field(None, ge=1, le=200, description="每页批数，1 到 200，缺省 50")

    @field_validator("date_from", "date_to")
    @classmethod
    def _dates(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _consistent(self) -> ReportWriteoffsIn:
        if self.receipt is not None and self.receipt_code is not None:
            raise ValueError("receipt 和 receipt_code 只能给一个")
        if self.target is not None and self.target_code is not None:
            raise ValueError("target 和 target_code 只能给一个")
        if self.receipt is not None and self.receipt.type != _RECEIPT[self.flag]:
            raise ValueError(f"flag={self.flag} 时 receipt.type 只能是 {_RECEIPT[self.flag]}")
        if self.target is not None and self.target.type not in _TARGETS[self.flag]:
            raise ValueError(f"flag={self.flag} 时 target.type 只能是 {' 或 '.join(_TARGETS[self.flag])}")
        if self.cancel_no is not None and self.cancel_no[2:4] != self.flag:
            raise ValueError("cancel_no 与 flag 不一致（HXAR 是应收、HXAP 是应付）")
        if self.date_from and self.date_to and self.date_from > self.date_to:
            raise ValueError("date_from 不能晚于 date_to")
        return self

    def audit_ref(self) -> str:
        return self.cancel_no or self.flag


class ReportWriteoffsOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否读到")
    flag: Scalar = Field(None, description="AR 或 AP")
    next: Scalar = Field(None, description=_NEXT)
    items: list[dict[str, Any]] | None = Field(
        None,
        description="每批一项：cancel_no、flag、date（登记日期）、year、period（U8 会计期间）、partner、currency、operator、"
        "receipt（type：ar_receipt / ap_payment，退款方向为 null；vouch_type、id、line_id（只涉及一行时）、line_ids、code）、"
        "targets（每个被核销单据行：type、vouch_type、id、line_id（发票行，应收应付单为 null）、code、amount）、"
        "amount（targets 合计）、gl_voucher、voucher（已制单时 {id, sign, no, date}，否则 null）、cancellable、"
        "reason（cancellable 为 false 时的原因，否则 null）",
    )
