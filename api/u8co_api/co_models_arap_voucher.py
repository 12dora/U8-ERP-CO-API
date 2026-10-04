"""应收 / 应付制单（arap/voucher）和取消制单（arap/voucher/delete）。校验规则与桥的 ArapVoucherReq 一致。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictStr, model_validator

from u8co_api.co_models import CoAuth
from u8co_api.co_models_dry import DryRunFlag

# 响应模型保留桥多给的字段（co/bridge/CHECKLIST.md「响应与错误码」）。
_OUT = ConfigDict(extra="allow")
_ID_MAX = 2147483647
_MERGE_MAX = 20
CASH_ITEMS_MAX = 20
_ACCOUNT_MAX = 40
_ITEM_MAX = 20

VoucherDocType = Literal[
    "sale_invoice", "purchase_invoice", "ar_receipt", "ap_payment", "ar_bill", "ap_bill", "ar_refund", "ap_refund"
]
_FLAG = {
    "sale_invoice": "AR",
    "ar_receipt": "AR",
    "ar_bill": "AR",
    "ar_refund": "AR",
    "purchase_invoice": "AP",
    "ap_payment": "AP",
    "ap_bill": "AP",
    "ap_refund": "AP",
}

VOUCHER_SUMMARY = "应收 / 应付制单"
VOUCHER_HELP = (
    "一张已审核的单据（id）生成一张总账凭证；也可用 ids 把同类型的 2 到 20 张单据合并成一张凭证（U8 的合并制单，"
    "每张单据各拼各的分录、只在本单内合并，凭证行的 coutid 是该行来源单据的单号，附单据数 = 张数，省略 voucher_date 时"
    "取最晚的单据日期；任一张不合格整笔 409，消息带「ids 第 i 张」）。支持：销售发票（26 / 27，须已应收审核）、采购发票（01 / 02，"
    "须已应付审核）、"
    "收款单 48、付款单 49、应收单 R0、应付单 P0（须已审核），另有客户退款 ar_refund（AR49）、供应商退款 ap_refund（AP48）："
    "U8 审核时把退款登记成负数，凭证是同一方的红字往来行加正数结算行（供应商退款借 应付 负数、借 银行；客户退款贷 应收 负数、"
    "贷 银行），缺省摘要「收<供应商>退款」/「付<客户>退款」。flag 是 AR 或 AP（也是登录子系统），type 须与 flag 同"
    "侧，id 是单据主键。"
    "U8 没有无界面的制单组件，桥照 U8 的规则拼分录：往来科目取审核时登记在往来明细上的科目；销售发票的收入、销项税"
    "科目，"
    "采购发票的采购、进项税科目，应收应付单的税金科目按对方科目设置（AP_OppCodeSet）找最具体的一行，没有再用基本科"
    "目设置；"
    "收付款单的结算科目取往来明细的结算行；应收应付单的对方科目取表体科目。同科目同辅助项合并（应收应付选项 bYPzKMH"
    "B / bSPzKMHB），"
    "借方在前；辅助核算只按科目要求填，值取自单据，缺了 409；现金流量按总账的现金流量项目数据来源取。"
    "凭证经 U8 的凭证导入 U8PzInsert 保存（它自己提交），带外部来源（制单系统 AR / AP、外部业务号 pz_id、原始单据类"
    "型和单号），"
    "外部业务号照 U8 从 Ap_CancelNo 取；之后桥按 U8 界面的回写（实测核对）写：往来明细 cPZid / dPZDate / cGLSign / iGLno_id，发票表"
    "体线索号、"
    "凭证号，收付款单、应收应付单表头凭证号。回写失败时桥删掉刚生成的凭证并返回 409；删不掉返回 504，消息里有凭证号"
    "和外部业务号。"
    "sign 是凭证类别，省略时有现金、银行科目的借方「收」、贷方「付」，其余「转」；voucher_date 是制单日期（yyyy-MM-"
    "dd），"
    "省略取单据日期，不能早于单据日期、必须在登录年度内（请求里的 date 是登录日期）、总账和应收应付该月未结账，并受"
    "制单序时控制；"
    "digest 是摘要，省略取往来明细上的摘要。cash_items 指定现金流量项目，见该字段说明。只做本币、蓝字单据（退款单除外）。"
    "409 state_mismatch：未审核、已制单（「凭证已生成，不能重复制单。」）、外币或红字、期间已结账、科目未设置或不是"
    "末级、"
    "辅助项缺值、现金流量项目无法按数据来源确定（用 cash_items 指定）、凭证类别不存在、收款单已用于坏账收回（"
    "请对坏账收回制单）；409 u8_rejected：U8 拒绝保存凭证（原文带回）。"
    "合并制单的凭证用 arap/voucher/delete 一次取消，全部单据一起清掉凭证号。"
    "收到 504 outcome_unknown 时先按单据编号（凭证的 coutid）或外部业务号在总账里核对，不要直接重试：总账里已有这张单据未被引用的凭证时重试 409。现结发票、科目或辅助项档案不合格（部门、人员、客户、供应商、结算方式、现金流量项目）也是 409。取消制单用 arap/voucher/de"
    "lete。"
)
DELETE_SUMMARY = "取消应收 / 应付制单"
DELETE_HELP = (
    "按外部业务号 pz_id（单据往来明细的 cPZid = 凭证的 coutno_id，AR… / AP…，前缀须与 flag 一致）删除应收（应付）生"
    "成的凭证，"
    "并按 U8 界面执行的清除 SQL（实测核对）清掉单据上的凭证号。只收销售发票、采购发票、收付款单（含退款单）、应收应付单原始行生成的凭证，"
    "以及 arap/process/voucher 生成的处理凭证（应收冲应付、应付冲应收、并账、汇兑损益整批；汇兑损益凭证属第二级"
    "写入（默认关闭，打开后只对测试账套开放）；坏账发生、收回、计提的凭证同样，计提凭证清坏账准备参数行上的凭证号）；"
    "核销等其他处理生成的凭证 409。"
    "404 not_found：没有这张凭证。409 state_mismatch：此凭证已记账 / 已审核 / 已经出纳签字，不能删除；此凭证所在期"
    "间已结账；"
    "已做银行对账或往来两清；被红字冲销；正被别人编辑；不是本系统生成的；应收（应付）已结账。在一个事务里完成，提交"
    "后回读确认。"
)

CASH_ITEMS_HELP = (
    "指定现金流量项目：{科目编码: 现金流量项目编码}，最多 20 个科目。\n\n"
    "- 何时要挂：凭证里既有现金流量科目又有别的科目、总账开了现金流量（bXJLL），别的科目每行挂一个项目\n"
    "- 不给：桥按「现金流量项目数据来源」推（U8 客户端由用户手选）\n"
    "- 推不出：409「…请用 cash_items 指定，或在 U8 客户端制单」\n"
    "- 给了的科目直接用给的项目，不再推\n"
    "- 400：项目不存在或已关闭\n"
    "- 400「科目 X 在本次凭证里没有现金流量行」：科目没有要挂项目的行，或这张凭证不挂项目"
)


def _code(text: str, most: int) -> str | None:
    code = text.strip()
    if not code or len(code) > most or any(ch.isspace() or not ch.isprintable() for ch in code):
        return None
    return code


def check_cash_items(value: dict[str, str] | None) -> dict[str, str] | None:
    """cash_items 的形状（同桥 ArapCashItems.Parse）：键是科目编码（≤ 40 位）、值是项目编码（≤ 20 位），不含空白，
    去掉首尾空格；科目不分大小写不能重复。"""
    if value is None:
        return None
    if len(value) > CASH_ITEMS_MAX:
        raise ValueError(f"cash_items 最多 {CASH_ITEMS_MAX} 个科目")
    out: dict[str, str] = {}
    seen: set[str] = set()
    for key, item in value.items():
        account = _code(key, _ACCOUNT_MAX)
        if account is None:
            raise ValueError("cash_items 的键必须是不超过 40 位、不含空白的科目编码")
        code = _code(item, _ITEM_MAX)
        if code is None:
            raise ValueError("cash_items 的值必须是不超过 20 位、不含空白的现金流量项目编码")
        if account.upper() in seen:
            raise ValueError(f"cash_items 有重复的科目 {account}")
        seen.add(account.upper())
        out[account] = code
    return out


CashItems = Annotated[
    dict[StrictStr, StrictStr] | None,
    AfterValidator(check_cash_items),
    Field(description=CASH_ITEMS_HELP, examples=[{"660399": "07"}]),
]


class ArapVoucherRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: VoucherDocType = Field(..., description="单据类型，各项相同")
    id: int = Field(..., ge=1, le=_ID_MAX, description="单据主键")


class CoArapVoucherIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统")
    type: VoucherDocType | None = Field(None, description="单据类型，须与 flag 同侧；用 ids 时可省略（取 ids 的）")
    id: int | None = Field(
        None, ge=1, le=_ID_MAX, description="单据主键（发票 SBVID / PBVID、收付款单和退款单 iID、应收应付单 Auto_ID）；与 ids 二选一"
    )
    ids: list[ArapVoucherRef] | None = Field(
        None,
        min_length=2,
        max_length=_MERGE_MAX,
        description="合并制单：同类型的 2 到 20 张单据 [{type, id}]，合成一张凭证；与 id 二选一",
    )
    sign: str | None = Field(None, min_length=1, max_length=2, description="凭证类别；省略按规则选（收 / 付 / 转）")
    voucher_date: str | None = Field(
        None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="制单日期；省略取单据日期（请求的 date 是登录日期）"
    )
    digest: str | None = Field(None, max_length=120, description="摘要；省略取往来明细上的摘要")
    cash_items: CashItems = None
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _same_side(self) -> CoArapVoucherIn:
        if (self.id is None) == (self.ids is None):
            raise ValueError("id 与 ids 必须给且只给一个（一张单据用 id，合并制单用 ids）")
        if self.ids is not None:
            self._merge_refs(self.ids)
        if self.type is None:
            raise ValueError("缺少 type")
        if _FLAG[self.type] != self.flag:
            raise ValueError(
                "type 与 flag 不一致（应收 AR：sale_invoice、ar_receipt、ar_bill、ar_refund；"
                "应付 AP：purchase_invoice、ap_payment、ap_bill、ap_refund）"
            )
        return self

    def _merge_refs(self, refs: list[ArapVoucherRef]) -> None:
        kind = self.type or refs[0].type
        if any(ref.type != kind for ref in refs):
            raise ValueError("合并制单的单据类型必须相同（ids 各项的 type 与 type 一致）")
        if len({ref.id for ref in refs}) != len(refs):
            raise ValueError("ids 有重复的单据")
        self.type = kind

    def audit_ref(self) -> str:
        refs = self.ids or []
        return "ids:" + ",".join(str(ref.id) for ref in refs)


class ArapVoucherKeyOut(BaseModel):
    model_config = _OUT
    year: int = Field(description="会计年度")
    period: int = Field(description="会计期间")
    sign: str = Field(description="凭证类别")
    no: int = Field(description="凭证号")
    num: str | None = Field(None, description="单据上回写的凭证号，如 转-0172")
    date: str | None = Field(None, description="制单日期")


class ArapVoucherLineOut(BaseModel):
    model_config = _OUT
    entry: int = Field(description="分录号")
    account: str = Field(description="科目编码")
    digest: str = Field(description="摘要")
    debit: float = Field(description="借方金额")
    credit: float = Field(description="贷方金额")
    dept: str | None = Field(None, description="部门")
    person: str | None = Field(None, description="个人")
    customer: str | None = Field(None, description="客户")
    supplier: str | None = Field(None, description="供应商")
    item_class: str | None = Field(None, description="项目大类")
    item: str | None = Field(None, description="项目")
    settle: str | None = Field(None, description="结算方式")
    bill_code: str | None = Field(None, description="该行的来源单据号（凭证行 coutid；合并制单时各行是各自单据的单号）")


class ArapVoucherBillOut(BaseModel):
    model_config = _OUT
    type: str = Field(description="单据类型")
    id: int = Field(description="单据主键")
    code: str | None = Field(None, description="单据号")
    vouch_type: str | None = Field(None, description="U8 单据类型代码（26 / 27 / 01 / 02 / 48 / 49 / R0 / P0）")


class CoArapVoucherOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已制单")
    acc: str | None = Field(None, description="账套号")
    flag: str = Field(description="AR 或 AP")
    type: str = Field(description="单据类型（合并制单时是第一张的）")
    id: int = Field(description="单据主键（合并制单时是第一张的）")
    code: str | None = Field(None, description="单据号（合并制单时是第一张的）")
    bills: list[ArapVoucherBillOut] | None = Field(
        None, description="本凭证覆盖的单据（按请求顺序；单张制单也有一项）。type / id / code 是第一张"
    )
    pz_id: str = Field(description="外部业务号（往来明细 cPZid = 凭证 coutno_id），取消制单要用")
    making_system: str | None = Field(
        None, description="导入凭证时用的制单系统（AR / AP，被拒时改用的 GL）；凭证上最终都写成 flag"
    )
    voucher: ArapVoucherKeyOut
    lines: list[ArapVoucherLineOut] = Field(description="凭证分录（回读）")


class CoArapVoucherDeleteIn(CoAuth):
    flag: Literal["AR", "AP"] = Field(..., description="应收 AR 或应付 AP，也是登录子系统")
    pz_id: str = Field(..., pattern=r"^(AR|AP)[0-9]{1,20}$", description="外部业务号（AR… / AP…），前缀须与 flag 一致")
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _same_flag(self) -> CoArapVoucherDeleteIn:
        if self.pz_id[:2] != self.flag:
            raise ValueError("pz_id 与 flag 不一致（AR 开头是应收、AP 开头是应付）")
        return self

    def audit_ref(self) -> str:
        return self.pz_id


class CoArapVoucherDeleteOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已取消")
    acc: str | None = Field(None, description="账套号")
    flag: str = Field(description="AR 或 AP")
    pz_id: str = Field(description="已取消的外部业务号")
    deleted: bool = Field(description="凭证已删除")
    voucher: ArapVoucherKeyOut | None = Field(None, description="删掉的凭证")
