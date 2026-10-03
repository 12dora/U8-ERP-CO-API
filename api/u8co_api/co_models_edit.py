"""修改、关闭和参照生单。校验规则与桥的 update/close/generate 一致。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from u8co_api.co_edit_dispatch_add import DISPATCH_ADD_HELP, check_dispatch_adds
from u8co_api.co_edit_qm import QM_UPDATE_HELP, check_qm_update
from u8co_api.co_gen_source import (
    SourceType,
    check_arrival,
    check_arrival_in,
    check_invoice,
    check_product_in,
    check_qm_in,
    lines_optional,
    source_of,
)
from u8co_api.co_gen_pu_settle import SETTLE_GEN_HELP, SETTLE_KIND, check_settle_gen
from u8co_api.co_models_gen_row import check_gen_row
from u8co_api.co_gen_qm import QM_GEN_HELP, QM_GEN_KINDS, check_qm_gen, nested_head
from u8co_api.co_gen_red_sale import RED_SALE_HELP, check_red_sale_invoice
from u8co_api.co_gen_sale_out import SALE_OUT_LINES_HELP, check_sale_out
from u8co_api.co_gen_sale_return import APPLY_GENERATE_HELP, APPLY_UPDATE_HELP, check_apply_update, check_sale_return
from u8co_api.co_gen_transfer import TRANSFER_GEN_HELP, check_transfer_gen
from u8co_api.co_models_bom import BOM_UPDATE_HELP, CoBomComponent, check_bom_update
from u8co_api.co_models import Cell, CoAuth, CoDocState
from u8co_api.co_models_dry import DryRunFlag
from u8co_api.co_models_mo_update import MO_UPDATE_HELP, check_mo_update
from u8co_api.co_models_wf import CoLoadState
from u8co_api.co_models_stock_opening import refuse_no_update
from u8co_api.co_stmisc import check_stmisc_lines

_OUT = ConfigDict(extra="ignore")
_ID_MAX = 2147483647
_UPDATE_TYPE = "可修改的单据类型：销售订单、采购订单、其他入库单、其他出库单、调拨单、采购入库单（无来源）"
_CLOSE_TYPE = "可关闭或打开的单据类型：销售订单、采购订单"
# 请购单可修改、可整单关闭或打开。
_UPDATE_TYPE += "、请购单"
_CLOSE_TYPE += "、请购单（只能整单，不带 line_ids）"
# 形态转换单、调拨申请单可修改（盘点单不开修改）。
_UPDATE_TYPE += "、形态转换单、调拨申请单"
# 期初结存单（stock_opening）不能修改（U8 的期初单一张一行），API 层直接 400，与桥同文（refuse_no_update）。
# 生单来的单据（不能新增行，数量只能减少）和应收应付（手工单，可增删行）。只改未审核的单据，见 docs/api-reference.md §8。
# 销售出库单不在这里拒绝新增行——来源库存的（未启用销售管理的账套）可增删行，来源发货单的由桥拒绝。
# 发货单可以新增参照来源销售订单的行（co_edit_dispatch_add.check_dispatch_adds），不在这里。
_NO_ADD = tuple("sale_return sale_invoice product_in material_out arrival purchase_return purchase_invoice".split())
_UPDATE_TYPE += (
    "、发货单、退货单、销售发票、销售出库单、产成品入库单、材料出库单、采购入库单（参照生成）、到货单、采购退货单、采购发票"
    "（这些生单来的类型不能新增行；发货单、来源为库存的销售出库单除外，可增删行、不能换仓库）、收款单、付款单、应收单、应付单"
)
# 物料清单（bom，id 是 BomId）按 sort_seq 定位行，只改未审核的标准 BOM。
_UPDATE_TYPE += "、物料清单"
# 生产订单（id 是 MoId）经 U8API MOrderLoad + MOrderUpdate 修改，子件全部重送并回读核对。
# 退货申请单只改已有行（co_return_apply.check_apply_update）。
_UPDATE_TYPE += "、生产订单、退货申请单"
# 来料检验单、产品检验单、其他检验单、其他报检单（只收 head，co_edit_qm.check_qm_update）。
_UPDATE_TYPE += "、来料检验单、产品检验单、其他检验单、其他报检单"
# 生产订单按行关闭或打开（id 是 MoId，line_ids 是 MoDId）。
_CLOSE_TYPE += "、生产订单（id 为 MoId，line_ids 为 MoDId）"
# 到货单整单或按行关闭、打开（只限已审核的蓝字到货单，line_ids 为 Autoid）。
_CLOSE_TYPE += "、到货单（line_ids 为 Autoid）"
_GENERATE_TYPE = (
    "参照生单的目标类型：发货单、销售出库单、采购入库单、销售发票、材料出库单、产成品入库单、采购发票、到货单"
    "、采购退货单"
    "、退货单（红字发货单）"
    "、来料报检单、产品报检单、来料检验单、产品检验单、来料不良品处理单、产品不良品处理单"
    "、调拨单（参照调拨申请单）" "、采购结算单（参照采购发票，整张自动结算）"
)
_SKIP_ADD = frozenset({"op"})
_SKIP_UPDATE = frozenset({"op", "line_id"})
_DELETE_KEYS = frozenset({"op", "line_id"})
_SKIP_GEN = frozenset({"source_line_id", "quantity"})
# 与桥 Json.Blocked 一致：公共保留名，再加该单据的主键、单号、审核人和审核日期。
_LISTED = frozenset(
    {
        "id", "autoid", "poid", "dlid", "isosid", "idlsid", "iposid", "cbsysbarcode", "code", "editprop", "ufts",
        "maker", "cmaker", "verifier", "cverifier", "chandler", "cscloser", "ifhquantity", "ikpquantity",
        "foutquantity"
    }
)
UpdateType = Literal[
    "sale_order", "purchase_order", "other_in", "other_out", "transfer", "purchase_in", "purchase_requisition",
    "dispatch", "sale_return", "sale_invoice", "sale_out", "product_in", "material_out", "arrival",
    "purchase_return", "purchase_invoice", "ar_receipt", "ap_payment", "ar_bill", "ap_bill",
    # 供应商退款、客户退款（同收付款单，ArapEdit）
    "ap_refund", "ar_refund",
    "bom",
    # 生产订单（U8API MOrderUpdate）
    "production_order",
    # 形态转换单、调拨申请单（盘点单不开修改）；退货申请单（只改已有行）
    "shape_change", "transfer_request", "sale_return_apply",
    # 检验单、其他报检单（QmEdit，只收 head）
    "qm_incoming_check", "qm_product_check", "qm_other_check", "qm_other_inspect",
]
CloseType = Literal[
    "sale_order", "purchase_order", "purchase_requisition", "production_order",
    # 到货单（PuArrClose）
    "arrival",
]
GenerateType = Literal[
    "dispatch", "sale_out", "purchase_in", "sale_invoice",
    "material_out", "product_in", "purchase_invoice", "arrival",
    # 采购退货单（红字到货单）
    "purchase_return",
    # 退货单（红字发货单）
    "sale_return",
    # 报检单（QM01 ← 到货单，QM02 ← 生产订单）、检验单（QM03 ← QM01，QM04 ← QM02）
    "qm_incoming_inspect", "qm_product_inspect",
    "qm_incoming_check", "qm_product_check",
    # 不良品处理单（QM05 ← QM03，QM06 ← QM04）；其他检验单（QM15 ← QM11）
    "qm_incoming_reject", "qm_product_reject", "qm_other_check",
    # 调拨单参照调拨申请单；采购结算单参照采购发票（整张发票自动结算）
    "transfer", "purchase_settle",
]
# 检验单表头可带 items（检验项目列表），其余值不能嵌套（nested_head 把关）。
GenHead = dict[str, Cell | list[dict[str, Cell]]]
LineId = Annotated[int, Field(strict=True, gt=0, le=_ID_MAX)]


def _ban(*extra: str) -> frozenset[str]:
    return _LISTED | {item.lower() for item in extra}


_BANNED = {
    "sale_order": _ban("ID", "cSOCode", "cVerifier", "dverifydate"),
    "purchase_order": _ban("POID", "cPOID", "cVerifier", "cAuditDate"),
    "other_in": _ban("ID", "cCode", "cHandler", "dVeriDate"),
    "other_out": _ban("ID", "cCode", "cHandler", "dVeriDate"),
    "transfer": _ban("ID", "cTVCode", "cVerifyPerson", "dVerifyDate"),
    # 形态转换单、调拨申请单。
    "shape_change": _ban("ID", "cAVCode", "cVerifyPerson", "dVerifyDate"),
    "transfer_request": _ban("ID", "cTVCode", "cVerifyPerson", "dVerifyDate"),
    "dispatch": _ban("DLID", "cDLCode", "cVerifier", "dverifydate"),
    "sale_out": _ban("ID", "cCode", "cHandler", "dVeriDate"),
    "purchase_in": _ban("ID", "cCode", "cHandler", "dVeriDate"),
    "sale_invoice": _ban("SBVID", "cSBVCode", "cChecker", "dverifydate"),
    "material_out": _ban("ID", "cCode", "cHandler", "dVeriDate"),
    "product_in": _ban("ID", "cCode", "cHandler", "dVeriDate"),
    # 采购发票的表头走 co_gen_source 的白名单，这里只管明细；cPBVCode 不能进禁用名单。
    "purchase_invoice": _ban("PBVID", "cVerifier", "cAuditDate"),
    "arrival": _ban("ID", "cCode", "cverifier", "cAuditDate"),
    # 采购退货单，表头另走到货单白名单（check_arrival）。
    "purchase_return": _ban("ID", "cCode", "cverifier", "cAuditDate"),
    # 退货单，表头表体另走 check_sale_return 白名单。
    "sale_return": _ban("DLID", "cDLCode", "cVerifier", "dverifydate"),
    # 请购单。
    "purchase_requisition": _ban("ID", "cCode", "cVerifier", "cAuditDate"),
    # 应收应付修改。与桥 Json.Blocked 一致（Kinds 的主键、单号、审核人、审核日期）。
    "ar_receipt": _ban("iID", "cVouchID", "cCheckMan", "dverifydate"),
    "ap_payment": _ban("iID", "cVouchID", "cCheckMan", "dverifydate"),
    "ar_bill": _ban("Auto_ID", "cVouchID", "cCheckMan", "dverifydate"),
    "ap_bill": _ban("Auto_ID", "cVouchID", "cCheckMan", "dverifydate"),
    # 供应商退款、客户退款（Ap_CloseBill，同收付款单）。
    "ap_refund": _ban("iID", "cVouchID", "cCheckMan", "dverifydate"),
    "ar_refund": _ban("iID", "cVouchID", "cCheckMan", "dverifydate"),
}


def _reserved(key: str, banned: frozenset[str]) -> bool:
    if not key:
        return True
    return key.lower() in banned


def _blocked(keys: list[str], kind: str) -> None:
    banned = _BANNED[kind]
    for key in keys:
        if _reserved(key, banned):
            raise ValueError("不能填写字段 " + key)


def _fields(row: dict[str, Cell], kind: str, skip: frozenset[str]) -> list[str]:
    found = [key for key in row if key not in skip]
    _blocked(found, kind)
    return found


def _take_line_id(row: dict[str, Cell], seen: set[int]) -> None:
    raw = row.get("line_id")
    if type(raw) is not int:
        raise ValueError("line_id 无效")
    if raw < 1 or raw > _ID_MAX:
        raise ValueError("line_id 无效")
    if raw in seen:
        raise ValueError("line_id 重复")
    seen.add(raw)


def _add_row(row: dict[str, Cell], kind: str) -> None:
    if "line_id" in row:
        raise ValueError("新增行不能带 line_id")
    _fields(row, kind, _SKIP_ADD)


def _delete_row(row: dict[str, Cell], seen: set[int]) -> None:
    _take_line_id(row, seen)
    if set(row) != _DELETE_KEYS:
        raise ValueError("删除行只能有 op 和 line_id")


def _update_row(row: dict[str, Cell], seen: set[int], kind: str) -> None:
    _take_line_id(row, seen)
    if not _fields(row, kind, _SKIP_UPDATE):
        raise ValueError("修改行至少再改一个字段")


def _check_update_row(row: dict[str, Cell], seen: set[int], kind: str) -> None:
    op = row.get("op")
    if op == "add" and kind in _NO_ADD:
        raise ValueError("该单据类型修改不能新增行")
    if op == "add":
        _add_row(row, kind)
        return
    if op == "delete":
        _delete_row(row, seen)
        return
    if op == "update":
        _update_row(row, seen, kind)
        return
    raise ValueError("op 无效")


def _check_rows(lines: list[dict[str, Cell]], kind: str) -> None:
    seen: set[int] = set()
    for row in lines:
        _check_update_row(row, seen, kind)


def _gen_row(row: dict[str, Cell], kind: str, seen: set[int]) -> None:
    check_gen_row(row, seen)
    _fields(row, kind, _SKIP_GEN)


def _need_lines(lines: list[dict[str, Cell]] | None, kind: str) -> None:
    if not lines:
        raise ValueError("必须指定明细")
    seen: set[int] = set()
    for row in lines:
        _gen_row(row, kind, seen)


def _refuse_out_head(head: dict[str, Cell] | None) -> None:
    if not head:
        return
    raise ValueError("不能设置字段 " + next(iter(head)))


class CoUpdateIn(CoAuth):
    type: UpdateType = Field(..., description=_UPDATE_TYPE)
    id: int = Field(..., gt=0, le=_ID_MAX, description="单据主键，1 到 2147483647")
    head: GenHead | None = Field(
        None,
        description="要改的表头。只改列出的字段，值只能是字符串、数字或布尔，不能填主键、单号或审核人"
        "（检验单的 items 是检验项目列表）。" + QM_UPDATE_HELP,
    )
    lines: list[dict[str, Cell]] | None = Field(
        None,
        max_length=200,
        description="明细变更，0 到 200 行。op 为 add、update 或 delete。"
        "add 不能带 line_id。update 和 delete 要有不重复的 line_id。"
        "delete 只能有 op 和 line_id。update 至少再改一个字段。"
        "生单来的单据不能 add（发货单、来源为库存的销售出库单除外），不能删光明细，数量只能减少（退货单、采购退货单填正数）。"
        + DISPATCH_ADD_HELP + "。" + BOM_UPDATE_HELP + "。" + MO_UPDATE_HELP + "。" + APPLY_UPDATE_HELP,
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="before")
    @classmethod
    def _updatable(cls, data: object) -> object:
        return refuse_no_update(data)

    @model_validator(mode="after")
    def _checked(self) -> CoUpdateIn:
        if not self.head and not self.lines:
            raise ValueError("没有要修改的内容")
        nested_head(self.type, self.head)  # 只有检验单的 head.items 可以是列表
        if check_qm_update(self.type, self.head, self.lines):  # 质量单据只收 head
            return self
        if self.type == "bom":
            check_bom_update(self.head, self.lines)
            return self
        # 生产订单走自己的名单；退货申请单只改已有行（co_return_apply）
        if check_mo_update(self.type, self.head, self.lines) or check_apply_update(self.type, self.head, self.lines):
            return self
        if self.head:
            _blocked(list(self.head), self.type)
        if self.lines:
            _check_rows(self.lines, self.type)
            check_dispatch_adds(self.type, self.lines)  # 发货单新增行
            check_stmisc_lines(self.type, self.lines)
        return self


class CoCloseIn(CoAuth):
    type: CloseType = Field(..., description=_CLOSE_TYPE)
    id: int = Field(..., gt=0, le=_ID_MAX, description="单据主键，1 到 2147483647")
    action: Literal["close", "open"] = Field(..., description="close 关闭，open 打开")
    line_ids: list[LineId] | None = Field(
        None,
        min_length=1,
        max_length=200,
        description="明细主键，1 到 200 个不重复的整数。省略则处理整张单据。不能传 null",
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="before")
    @classmethod
    def _line_ids_not_null(cls, data: object) -> object:
        if isinstance(data, dict) and "line_ids" in data and data["line_ids"] is None:
            raise ValueError("line_ids 不能为 null")
        return data

    @field_validator("line_ids")
    @classmethod
    def _unique(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if len(value) != len(set(value)):
            raise ValueError("line_ids 重复")
        return value

    @model_validator(mode="after")
    def _whole_only(self) -> CoCloseIn:
        # 请购单只做整单关闭或打开（CloseApp / OpenApp）。
        if self.type == "purchase_requisition" and self.line_ids is not None:
            raise ValueError("请购单只支持整单关闭或打开")
        return self


class CoGenerateIn(CoAuth):
    type: GenerateType = Field(..., description=_GENERATE_TYPE)
    id: int = Field(
        ...,
        gt=0,
        le=_ID_MAX,
        description="来源单据主键。发货单来自销售订单，销售出库来自发货单，采购入库来自采购订单、来料检验单或蓝字到货单（ID），"
        "销售发票来自发货单，材料出库来自生产订单（MoId），"
        "产成品入库来自产品检验单（检验单 ID）、产品不良品处理单（处理单 ID）或生产订单（MoId），"
        "采购发票来自采购入库单（红字入库单生成红字发票），到货单来自采购订单（POID），"
        "采购退货单来自蓝字到货单（ID）或采购订单（POID）"
        "，退货单（红字发货单）来自已审核的蓝字发货单（DLID）或已审核的退货申请单（source_type=sale_return_apply，申请单 ID）"
        "，红字销售发票（source_type=sale_return）来自已审核的退货单（DLID）"
        "，红冲蓝字销售发票（source_type=sale_invoice）来自已复核的蓝字销售发票（SBVID）"
        "，来料报检单来自到货单（ID），产品报检单来自生产订单（MoId），检验单来自报检单（ID），不良品处理单来自检验单（ID），其他检验单来自其他报检单（ID）"
        "，调拨单来自已审核的调拨申请单（ID）" "，采购结算单来自已复核的采购发票（PBVID）",
    )
    source_type: SourceType | None = Field(
        None,
        description="来源单据类型。省略时取缺省来源。采购入库可选 purchase_order（缺省）、qm_incoming_check"
        "、purchase_return（采购退货单，生成红字采购入库）或 arrival（蓝字到货单，lines 可省）；"
        "产成品入库可选 qm_product_check（缺省）、qm_product_reject 或 production_order；"
        "采购发票只能是 purchase_in；采购退货单可选 arrival（缺省）或 purchase_order；"
        "销售发票可选 dispatch（缺省）、sale_return（退货单，生成红字发票）或 sale_invoice（蓝字发票，红冲）；采购结算单只能是 purchase_invoice；" "其它目标只能是各自唯一的来源",
    )
    head: GenHead | None = Field(
        None,
        description="目标表头覆盖。销售出库不能带表头。值只能是字符串、数字或布尔，不能填主键、单号或审核人。"
        "采购发票只收字符串 cPBVCode（必填，最长 30）、cPBVBillType（01 或 02）、dPBVDate（yyyy-MM-dd）、"
        "cPBVMemo（最长 255）。到货单和采购退货单只收字符串 cWhCode、dDate（yyyy-MM-dd）、cMemo、cDepCode，可省略。"
        "参照来料检验单的采购入库必须有 cWhCode。"
        "退货单只收 dDate（yyyy-MM-dd）、cMemo、cDepCode、cPersonCode、cDefine1–16，可省略；"
        "另可带布尔 invoiced（false 未开票退货，true 已开票退货，省略时按可退数量自动选）。"
        "只有检验单的 items 可以是列表。" + QM_GEN_HELP + "。" + RED_SALE_HELP + "。" + TRANSFER_GEN_HELP + "。" + SETTLE_GEN_HELP,
    )
    lines: list[dict[str, Cell]] | None = Field(
        None,
        max_length=200,
        description="来源明细，1 到 200 行。每行要有 source_line_id 和大于 0 的 quantity。"
        + SALE_OUT_LINES_HELP
        + "材料出库的 source_line_id 是子件分配 AllocateId；材料出库、产成品入库的行另可带 cbatch、cbmemo 和货位 cposition（启用货位管理的仓库必填末级货位，否则不能填）；"
        "产成品入库参照产品检验单：非合并检验只能有 1 行，source_line_id 是检验单 ID；合并检验（BMERGECHECKFLAG=1）每个来源一行（1 到 20 行），source_line_id 是合并来源 AUTOID（vouchers/load 的 merge_sources），数量不超过该来源的剩余；参照产品不良品处理单（qm_product_reject）或生产订单时只能有 1 行，source_line_id 分别是处理单表体 AUTOID、订单行 MoDId。"
        "采购发票和到货单的行只能有 source_line_id 和 quantity（到货单是 PO_Podetails.ID）。"
        "采购退货单同样只收这两项，source_line_id 是原到货单行 Autoid 或 PO_Podetails.ID，quantity 填正数（桥写负数）。"
        "参照来料检验单的采购入库只能有 1 行（source_line_id 是检验单 ID），可带 cbatch、cbmemo 和货位 cposition"
        "（启用货位管理的仓库必填末级货位，否则不能填）。"
        "参照采购退货单的红字采购入库：source_line_id 是退货单行 Autoid，quantity 填正数（桥写负数）。"
        "参照到货单的采购入库：source_line_id 是到货单行 Autoid，可带 cbatch、cbmemo、cposition、cWhCode（表头没给仓库时各行须一致），"
        "lines 省略时按各行剩余可入库数量整单生成；需要来料检验的存货 409。"
        "采购入库（四种来源）的行都可带货位 cposition：表头仓库启用货位管理时每行必填该仓库的末级货位，否则不能填。"
        "参照红字入库单的采购发票同样 quantity 填正数，桥写负数、表头 bNegative=1。"
        "退货单的 source_line_id 是原发货单行 iDLsID，quantity 填正数（桥写负数，不超过发货数量减累计退货数量），"
        "另可带 cWhCode、cMemo、cDefine22–37" + APPLY_GENERATE_HELP,
    )
    dry_run: DryRunFlag = False

    @model_validator(mode="after")
    def _checked(self) -> CoGenerateIn:
        source = source_of(self.type, self.source_type)
        nested_head(self.type, self.head)
        if self.type in QM_GEN_KINDS:
            check_qm_gen(self.type, self.head, self.lines, self.id)
            return self
        if self.type == "transfer":
            check_transfer_gen(self.head, self.lines)  # 调拨单参照调拨申请单（co_gen_transfer）
            return self
        if self.type == SETTLE_KIND:
            check_settle_gen(self.head, self.lines, self.year, self.date)  # 采购结算参照采购发票（co_gen_pu_settle）
            return self
        if self.type == "sale_out":
            _refuse_out_head(self.head)
            if self.lines is not None:
                check_sale_out(self.lines)  # 另收 cbatch、cposition，可按批号 / 货位拆行（co_gen_sale_out）
            return self
        # 红字销售发票参照退货单，lines 可省略（co_gen_red_sale）；红冲蓝字发票同一校验。
        if self.type == "sale_invoice" and source in ("sale_return", "sale_invoice"):
            check_red_sale_invoice(self.head, self.lines)
            return self
        if self.head and self.type not in ("purchase_invoice", "arrival"):
            _blocked(list(self.head), self.type)
        if self.lines is not None or not lines_optional(self.type, source):  # 参照到货单的采购入库 lines 可省
            _need_lines(self.lines, self.type)
        _check_special(self.type, source, self.head, self.lines or [])
        return self


def _check_special(kind: str, source: str, head: dict[str, Cell] | None, lines: list[dict[str, Cell]]) -> None:
    if kind == "purchase_invoice":
        check_invoice(head, lines)
    if kind in ("arrival", "purchase_return"):
        check_arrival(head, lines)
    if source == "qm_incoming_check":
        check_qm_in(head, lines)
    if lines_optional(kind, source):
        check_arrival_in(head, lines)
    if kind == "sale_return":
        check_sale_return(head, lines)
    if kind == "product_in":
        check_product_in(source, lines)


class CoEditOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已保存")
    type: str = Field(description="单据类型")
    id: int = Field(description="单据主键")
    code: str = Field(description="单据编号")
    state: CoDocState = Field(description="审核状态")
    lines: int = Field(description="保存后的明细行数")
    version: int | None = Field(None, description="物料清单：版本号。其它类型省略")
    components: list[CoBomComponent] | None = Field(
        None, description="物料清单：保存后的每行子件（主键、行号、存货、用量分子分母）。其它类型省略"
    )


class CoGenWf(BaseModel):
    model_config = ConfigDict(extra="allow")
    controlled: bool | None = Field(None, description="是否受审批流控制（IsWfControlled）")
    verify_state_new: int | None = Field(None, description="表头 iVerifyStateNew。0 未提交")


class CoGenerateOut(CoEditOut):
    source_type: str = Field(description="来源单据类型")
    source_id: int = Field(description="来源单据主键")
    ids: list[int] | None = Field(None, description="销售出库按仓库拆成多张时，全部新单据主键")
    wf: CoGenWf | None = Field(None, description="检验单、不良品处理单：新单据的审批流状态（未提交）。其它类型省略")
    items: int | None = Field(None, description="检验单：保存后的检验项目行数。其它类型省略")
    settle_date: str | None = Field(None, description="采购结算单：结算日期（即本次的 U8 登录日期）。其它类型省略")


class CoCloseLine(BaseModel):
    model_config = _OUT
    line_id: int = Field(description="明细主键")
    closed: bool = Field(description="该行是否已关闭")


class CoCloseOut(BaseModel):
    model_config = _OUT
    ok: bool = Field(description="是否已关闭或打开")
    type: str = Field(description="单据类型")
    id: int = Field(description="单据主键")
    action: Literal["close", "open"] = Field(description="close 关闭，open 打开")
    closed: bool = Field(description="表头关闭人是否非空")
    closed_by: str = Field(description="关闭人。打开后为空")
    closed_at: str = Field(description="关闭时间。打开后为空")
    lines: list[CoCloseLine] = Field(description="全部明细行的关闭状态")
    code: str | None = Field(None, description="生产订单编号。其它类型不返回")
    changed: int | None = Field(None, description="生产订单本次关闭或打开的行数。其它类型不返回")
    state: CoLoadState | None = Field(None, description="生产订单处理后的整单状态。其它类型不返回")
