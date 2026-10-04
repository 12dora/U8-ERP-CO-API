"""公司间（数据归并）的请求、响应模型：共用的多账套登录，公司间对账（reports/intercompany_match）和
按卖方单据生成买方单据（intercompany/generate_buyer）。汇总、合并报表的模型在 co_models_ic_reports。"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator

from u8co_api.co_doctext import op_doc
from u8co_api.co_models import CoAuth, Cell
from u8co_api.co_models_gl import DATE, PASS, Scalar

_FORBID = ConfigDict(extra="forbid")
_ID_MAX = 2147483647
_QTY_MAX = 1e12
MAX_DAYS = 93
INVOICE_TYPES = frozenset({"sale_invoice", "purchase_invoice"})
SellerType = Literal["sale_out", "dispatch", "sale_invoice"]
BuyerType = Literal["purchase_in", "arrival", "purchase_invoice"]
GenBuyerType = Literal["purchase_in", "arrival", "other_in"]
_ACC = r"^\d{3}$"
_CODE = r"^[^\x00-\x1f\x7f]{1,60}$"

MATCH_SUMMARY = "公司间对账"
MATCH_HELP = op_doc(
    "把卖方账套的销售单据和买方账套的采购单据逐行配对（只读）。",
    (
        "用法",
        (
            "logins 给 2 到 3 个账套的登录，必须含 seller.acc 和 buyer.acc",
            "两边都是发票时缺省按月（invoice_mode=month）比较价税合计",
        ),
    ),
    (
        "规则",
        (
            "按公司间对照（U8CO_IC_MAP_FILE）配对，各账套须在同一个公司组",
            "卖方单据按「买方公司在卖方账套里的客户编码」查",
            "买方单据按「卖方公司在买方账套里的供应商编码」查",
            "存货按对照换成同一个存货 id；没有对照的不配对，列在未配对里（reason 为 unmapped）",
            "match=code：先按日期、存货、数量完全相同配对，再在 window_days 天内配对",
            "match=qty_date：只按数量和当天配对",
            "销售出库、采购入库的明细取自库存台账，只比对对照里的存货",
            "发货单、到货单和发票逐张读取",
            "按月比较发票时，差额不超过 0.05 算配上",
            "只比对到 clip_date（两边最后一笔日期中较早的那天），之后的明细计入 clipped",
        ),
    ),
    ("限制", ("日期区间最多 93 天",)),
    (
        "错误",
        (
            "403 account_not_allowed：令牌不能用其中某个账套",
            "404 ic_not_configured：没配置对照",
            "400 ic_group_mismatch：账套不在同一组",
            "409 ic_party_unmapped：对照里缺往来单位编码",
        ),
    ),
)
GENERATE_SUMMARY = "按卖方单据生成买方单据"
GENERATE_HELP = (
    "把卖方（seller_acc）的一组出库明细换成买方账套的存货，在买方账套参照采购订单生成采购入库单或到货单。\n\n"
    "**用法**\n"
    "- 按公司间对照换存货，参照买方账套里卖方公司（供应商）未执行完的采购订单。\n"
    "- 生单走 vouchers/generate，source_type=purchase_order。\n"
    "- 给了 po_id 就用这张订单；否则取能覆盖全部存货和数量的、id 最小的未执行完订单。\n"
    "- 同一存货的多行合并后按订单行顺序分配数量。\n"
    "- dry_run 缺省为 true（预演，返回 DryRunOut，另有 plan）。\n"
    "- 正式生成时 dry_run 必须写 false，并且必须带 Idempotency-Key（按买方账套记录）。\n\n"
    "**规则**\n"
    "- 正式生成同样经过买方账套的写入策略。\n\n"
    "**限制**\n"
    "- type 不能是 other_in（400）：公司间采购要走采购订单。\n\n"
    "**错误**\n"
    "- 没有可用的未执行完订单：409 ic_no_open_po。\n"
    "- 存货没有对照：409 ic_inventory_unmapped（detail.codes 列出）。"
)


def _real_date(value: str | None) -> str | None:
    if value is not None:
        try:
            dt.date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("日期无效") from exc
    return value


class IcLogin(CoAuth):
    """一个账套的登录：acc、operator、password，year、date 可省略（同单账套调用）。"""


def _distinct(logins: list[IcLogin]) -> None:
    accs = [login.acc for login in logins]
    if len(set(accs)) != len(accs):
        raise ValueError("logins 里的账套不能重复")


class IcSellerSide(BaseModel):
    model_config = _FORBID
    acc: str = Field(..., pattern=_ACC, description="卖方账套号")
    type: SellerType = Field(..., description="卖方单据：sale_out 销售出库，dispatch 发货单，sale_invoice 销售发票")


class IcBuyerSide(BaseModel):
    model_config = _FORBID
    acc: str = Field(..., pattern=_ACC, description="买方账套号")
    type: BuyerType = Field(..., description="买方单据：purchase_in 采购入库，arrival 到货单，purchase_invoice 采购发票")


class IcMatchIn(BaseModel):
    model_config = _FORBID
    logins: list[IcLogin] = Field(..., min_length=2, max_length=3, description="2 到 3 个账套的登录，账套不能重复")
    seller: IcSellerSide = Field(..., description="卖方账套和单据类型")
    buyer: IcBuyerSide = Field(..., description="买方账套和单据类型")
    date_from: str = Field(..., pattern=DATE, description="单据日期下限 yyyy-MM-dd（含）")
    date_to: str = Field(..., pattern=DATE, description="单据日期上限 yyyy-MM-dd（含），与 date_from 最多相隔 93 天")
    window_days: StrictInt = Field(1, ge=0, le=7, description="第二轮配对允许的日期差（天），0 到 7，缺省 1")
    invoice_mode: Literal["line", "month"] | None = Field(
        None, description="两边都是发票时：month 按月比较价税合计（缺省），line 逐行配对。其它类型不能给"
    )

    @field_validator("date_from", "date_to")
    @classmethod
    def _date(cls, value: str) -> str:
        _real_date(value)
        return value

    @model_validator(mode="after")
    def _checked(self) -> IcMatchIn:
        _distinct(self.logins)
        accs = {login.acc for login in self.logins}
        if self.seller.acc not in accs or self.buyer.acc not in accs:
            raise ValueError("seller.acc 和 buyer.acc 都必须在 logins 里")
        if self.seller.acc == self.buyer.acc:
            raise ValueError("seller.acc 和 buyer.acc 不能是同一个账套")
        days = (dt.date.fromisoformat(self.date_to) - dt.date.fromisoformat(self.date_from)).days
        if days < 0:
            raise ValueError("date_from 不能晚于 date_to")
        if days > MAX_DAYS:
            raise ValueError(f"日期区间最多 {MAX_DAYS} 天")
        both = self.seller.type in INVOICE_TYPES and self.buyer.type in INVOICE_TYPES
        either = self.seller.type in INVOICE_TYPES or self.buyer.type in INVOICE_TYPES
        if either and not both:
            raise ValueError("发票只能和发票对账：seller.type 为 sale_invoice 时 buyer.type 必须是 purchase_invoice")
        if self.invoice_mode is not None and not both:
            raise ValueError("invoice_mode 只用于两边都是发票")
        return self

    def mode(self) -> str:
        if self.seller.type not in INVOICE_TYPES:
            return "line"
        return self.invoice_mode or "month"

    def audit_ref(self) -> str:
        return f"{self.seller.acc}:{self.seller.type}>{self.buyer.acc}:{self.buyer.type}"


class IcMatchOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否完成对账")
    group: Scalar = Field(None, description="公司组 id")
    mode: Scalar = Field(None, description="line 逐行配对，month 按月比较金额")
    seller: dict[str, Any] | None = Field(None, description="卖方：acc、type、partner（买方公司在卖方账套里的客户编码）")
    buyer: dict[str, Any] | None = Field(None, description="买方：acc、type、partner（卖方公司在买方账套里的供应商编码）")
    clip_date: Scalar = Field(None, description="只比对到这一天（两边最后一笔日期中较早的）；有一边没有数据时为 null")
    window_days: Scalar = Field(None, description="第二轮配对的日期差")
    matched: list[dict[str, Any]] | None = Field(
        None,
        description="配上的：逐行时 seller、buyer（acc、type、id、code、date、line_id、inv_code、qty、amount）、inv_id、"
        "qty、pass（exact 或 window）、days（买方日期减卖方日期）；按月时 month、seller_amount、buyer_amount、diff、"
        "seller_docs、buyer_docs",
    )
    unmatched_seller: list[dict[str, Any]] | None = Field(
        None, description="卖方没配上的明细（逐行，另有 inv_id、reason：unmapped 或 no_counterpart）或月份（按月）"
    )
    unmatched_buyer: list[dict[str, Any]] | None = Field(None, description="买方没配上的明细或月份，键同 unmatched_seller")
    rates: dict[str, Any] | None = Field(None, description="seller、buyer：配上的条数除以该方 clip_date 前的条数，没有数据为 null")
    clipped: dict[str, Any] | None = Field(None, description="seller、buyer：晚于 clip_date、没参与比对的条数")
    warnings: list[str] | None = Field(None, description="提醒，如存货没有对照、读取失败的单据")


class IcGenLine(BaseModel):
    model_config = _FORBID
    inv_code: str = Field(..., pattern=_CODE, description="卖方账套的存货编码（按对照换成买方编码）")
    quantity: float = Field(..., gt=0, le=_QTY_MAX, description="数量，大于 0")
    seller_id: StrictInt | None = Field(None, gt=0, le=_ID_MAX, description="卖方单据 id（只用于计划和审计）")


class IcGenerateIn(BaseModel):
    model_config = _FORBID
    login: IcLogin = Field(..., description="买方账套的登录")
    seller_acc: str = Field(..., pattern=_ACC, description="卖方账套号，与买方同在一个公司组")
    type: GenBuyerType = Field(..., description="买方单据：purchase_in 采购入库，arrival 到货单（other_in 不允许）")
    po_id: StrictInt | None = Field(None, gt=0, le=_ID_MAX, description="参照的采购订单 POID；省略时自动挑选")
    lines: list[IcGenLine] = Field(..., min_length=1, max_length=200, description="卖方明细，1 到 200 行")
    date: str | None = Field(None, pattern=DATE, description="买方单据日期 yyyy-MM-dd（写进表头 dDate），省略时由 U8 取登录日期")
    head: dict[str, Cell] | None = Field(
        None, description="买方表头覆盖，规则同 vouchers/generate（到货单只收 cWhCode、dDate、cMemo、cDepCode）"
    )
    dry_run: StrictBool = Field(True, description="缺省 true 只预演；false 时正式生成，必须带 Idempotency-Key")

    @field_validator("date")
    @classmethod
    def _date(cls, value: str | None) -> str | None:
        return _real_date(value)

    @model_validator(mode="after")
    def _checked(self) -> IcGenerateIn:
        if self.type == "other_in":
            raise ValueError("公司间采购不能生成其他入库单，请用 purchase_in 或 arrival")
        if self.seller_acc == self.login.acc:
            raise ValueError("seller_acc 不能是买方账套")
        return self

    def audit_ref(self) -> str:
        return f"{self.seller_acc}>{self.login.acc}:{self.type}"


class IcGenerateOut(BaseModel):
    model_config = PASS
    ok: bool = Field(description="是否已生成")
    type: Scalar = Field(None, description="买方单据类型")
    id: Scalar = Field(None, description="买方单据主键")
    code: Scalar = Field(None, description="买方单据编号")
    state: dict[str, Any] | None = Field(None, description="审核状态")
    lines: Scalar = Field(None, description="保存后的明细行数")
    plan: dict[str, Any] | None = Field(
        None,
        description="生成计划：buyer_acc、seller_acc、vendor（供应商编码）、po_id、po_code、"
        "lines（buyer_inv_code、seller_inv_codes、quantity、source_line_id）",
    )
