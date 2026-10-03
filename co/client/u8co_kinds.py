"""单据类型。顺序只影响命令行帮助。"""

from __future__ import annotations

_INT32_MAX = 2147483647

# 与桥契约的 type 字段一致。原有名字的相对顺序不变。
KIND_NAMES = (
    "sale_order", "dispatch", "sale_invoice",
    "purchase_order", "arrival", "purchase_invoice",
    "purchase_in", "other_in", "other_out", "transfer",
    "product_in", "material_out", "sale_out", "production_order",
    "qm_incoming_check", "qm_product_check", "qm_incoming_reject", "qm_product_reject",
    "ar_receipt", "ap_payment", "ar_bill", "ap_bill",
)
# 应收应付：收款单、付款单、应收单、应付单。
ARAP_KINDS = ("ar_receipt", "ap_payment", "ar_bill", "ap_bill")
CREATABLE_KINDS = ("sale_order", "other_in", "other_out", "purchase_order", "transfer") + ARAP_KINDS
# 无来源采购入库单可新增、修改。
CREATABLE_KINDS = CREATABLE_KINDS + ("purchase_in",)
DELETABLE_KINDS = (
    "sale_order", "other_in", "other_out", "purchase_order", "transfer",
    "dispatch", "arrival", "sale_out", "purchase_in", "sale_invoice",
    "material_out", "product_in", "purchase_invoice",
) + ARAP_KINDS
UPDATABLE_KINDS = ("sale_order", "purchase_order", "other_in", "other_out", "transfer")
UPDATABLE_KINDS = UPDATABLE_KINDS + ("purchase_in",)
CLOSABLE_KINDS = ("sale_order", "purchase_order")
GENERATABLE_KINDS = (
    "dispatch", "sale_out", "purchase_in", "sale_invoice", "material_out", "product_in", "purchase_invoice",
    "arrival",
)
# 生单可选 source_type：目标类型 → 允许的来源类型。不给时桥用默认来源。
GENERATE_SOURCES = {
    "purchase_in": ("purchase_order", "qm_incoming_check"),
    "purchase_invoice": ("purchase_in",),
    "arrival": ("purchase_order",),
    # 产成品入库参照产品检验单（缺省）或产品不良品处理单。
    "product_in": ("qm_product_check", "qm_product_reject"),
}
SOURCE_TYPES = ("purchase_order", "qm_incoming_check", "purchase_in")
SOURCE_TYPES += ("qm_product_check", "qm_product_reject")  # 产成品入库的两种来源
# 可直接审核的读取类型。采购发票的审核是采购复核。
VERIFIABLE_KINDS = (
    "sale_order", "dispatch", "sale_invoice",
    "purchase_order", "arrival",
    "purchase_invoice",
    "purchase_in", "other_in", "other_out", "transfer",
    "product_in", "material_out", "sale_out", "production_order",
) + ARAP_KINDS  # 质量单据只走 workflow/*（不良品处理单例外，见下）
WORKFLOW_KINDS = ("qm_incoming_check", "qm_product_check", "qm_incoming_reject", "qm_product_reject")
# 采购退货单（红字到货单）可读、审核、删除，参照原蓝字到货单（缺省）或采购订单生单。
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + ("purchase_return",) + ARAP_KINDS  # 应收应付仍在最后
DELETABLE_KINDS += ("purchase_return",)
GENERATABLE_KINDS += ("purchase_return",)
VERIFIABLE_KINDS = VERIFIABLE_KINDS[: -len(ARAP_KINDS)] + ("purchase_return",) + ARAP_KINDS
GENERATE_SOURCES["purchase_return"] = ("arrival", "purchase_order")
SOURCE_TYPES += ("arrival",)
# 退货单（红字发货单）可读、审核、删除，参照原蓝字发货单生单。
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + ("sale_return",) + ARAP_KINDS  # 应收应付仍在最后
DELETABLE_KINDS += ("sale_return",)
GENERATABLE_KINDS += ("sale_return",)
VERIFIABLE_KINDS = VERIFIABLE_KINDS[: -len(ARAP_KINDS)] + ("sale_return",) + ARAP_KINDS
GENERATE_SOURCES["sale_return"] = ("dispatch",)
SOURCE_TYPES += ("dispatch",)
# 请购单可读、新增、修改、删除、审核、整单关闭或打开，不能生单。
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + ("purchase_requisition",) + ARAP_KINDS  # 应收应付仍在最后
CREATABLE_KINDS += ("purchase_requisition",)
DELETABLE_KINDS += ("purchase_requisition",)
UPDATABLE_KINDS += ("purchase_requisition",)
CLOSABLE_KINDS += ("purchase_requisition",)
VERIFIABLE_KINDS = VERIFIABLE_KINDS[: -len(ARAP_KINDS)] + ("purchase_requisition",) + ARAP_KINDS
# 生单来的单据（不能新增行，数量只能减少；采购入库另含参照生成的）和应收应付可修改，都只改未审核的。
MORE_UPDATABLE_KINDS = (
    "dispatch", "sale_return", "sale_invoice", "sale_out", "product_in", "material_out",
    "arrival", "purchase_return", "purchase_invoice",
) + ARAP_KINDS
UPDATABLE_KINDS += MORE_UPDATABLE_KINDS
# 生产订单可按行关闭或打开（id 是 MoId，line_ids 是 MoDId）。
CLOSABLE_KINDS += ("production_order",)
# 到货单可整单或按行关闭、打开（VoucherCO_PU.CloseArrItems / OpenArrItems，line_ids 是 Autoid）。
CLOSABLE_KINDS += ("arrival",)
# 来料报检单（QM01）、产品报检单（QM02）：读取、列表；没有审批流（可生单、删除、审核，见下）。
INSPECT_KINDS = ("qm_incoming_inspect", "qm_product_inspect")
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + INSPECT_KINDS + ARAP_KINDS  # 应收应付仍在最后
# 生产订单可新增（U8API MOrderAdd，U8 按标准 BOM 展开子件）和删除（MOrderDelete）；修改见下文。
CREATABLE_KINDS += ("production_order",)
DELETABLE_KINDS += ("production_order",)
# 物料清单（bom，只含标准 BOM）可读、列表、新增版本、修改（按 sort_seq 定位行）、删除、审核（U8API BOM）。
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + ("bom",) + ARAP_KINDS  # 应收应付仍在最后
CREATABLE_KINDS += ("bom",)
DELETABLE_KINDS += ("bom",)
UPDATABLE_KINDS += ("bom",)
VERIFIABLE_KINDS = VERIFIABLE_KINDS[: -len(ARAP_KINDS)] + ("bom",) + ARAP_KINDS
# 报检单参照到货单 / 生产订单生单、可删除（不开放单独审核：U8 只在保存时自动审核）；检验单参照报检单生单、可删除（审核仍走 workflow/*）。
QM_GEN_KINDS = INSPECT_KINDS + ("qm_incoming_check", "qm_product_check")
DELETABLE_KINDS += QM_GEN_KINDS
GENERATABLE_KINDS += QM_GEN_KINDS
GENERATE_SOURCES["qm_incoming_inspect"] = ("arrival",)
GENERATE_SOURCES["qm_product_inspect"] = ("production_order",)
GENERATE_SOURCES["qm_incoming_check"] = ("qm_incoming_inspect",)
GENERATE_SOURCES["qm_product_check"] = ("qm_product_inspect",)
SOURCE_TYPES += ("production_order",) + INSPECT_KINDS
# 红字采购入库参照采购退货单（红字到货单）；采购退货单作为来源类型。
GENERATE_SOURCES["purchase_in"] += ("purchase_return",)
SOURCE_TYPES += ("purchase_return",)
# 红字销售发票参照退货单（lines 可省略，整张退货单按剩余数量生成）。
GENERATE_SOURCES["sale_invoice"] = ("dispatch", "sale_return")
SOURCE_TYPES += ("sale_return",)
# 形态转换单、调拨申请单、盘点单可读、新增、删除；形态转换单、调拨申请单可审核、修改；不能关闭、生单。
STOCK_MISC_KINDS = ("shape_change", "transfer_request", "stock_check")
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + STOCK_MISC_KINDS + ARAP_KINDS  # 应收应付仍在最后
CREATABLE_KINDS += STOCK_MISC_KINDS
DELETABLE_KINDS += STOCK_MISC_KINDS
UPDATABLE_KINDS += ("shape_change", "transfer_request")
# 盘点单审核暂不支持（U8 生成盘盈盘亏单时报类型不匹配），请到 U8 客户端审核。
VERIFIABLE_KINDS = VERIFIABLE_KINDS[: -len(ARAP_KINDS)] + ("shape_change", "transfer_request") + ARAP_KINDS
# 无来源新增——发货单、先开票销售发票（U8 另生成发货单）、到货单、材料出库单（账套打开「必有订单」一类选项时桥 409）；
# 产成品入库另可参照生产订单（id 是 MoId，source_line_id 是 MoDId）。
SRCLESS_CREATE_KINDS = ("dispatch", "sale_invoice", "arrival", "material_out")
CREATABLE_KINDS += SRCLESS_CREATE_KINDS
GENERATE_SOURCES["product_in"] = GENERATE_SOURCES["product_in"] + ("production_order",)
# 调拨单参照已审核的调拨申请单生单（申请单行要有核准数量 iTvChkQuantity）。
GENERATABLE_KINDS += ("transfer",)
GENERATE_SOURCES["transfer"] = ("transfer_request",)
SOURCE_TYPES += ("transfer_request",)
# 生产订单可修改（U8API MOrderLoad + MOrderUpdate；id 是 MoId，行按 line_id 定位，改未关闭的订单；已审核、已领料的也可改，材料出库引用由桥改写）。
UPDATABLE_KINDS += ("production_order",)
# 期初结存单（库存期初，U8 单据类型 34）可读、新增、删除、审核、弃审；不能修改（删除后重新录入），没有记账。
# 新增走 U8 官方 EAI 导入，每行建成一张单据（响应 docs）；日期由桥固定为库存启用日前一天。
# 新增、删除、审核、弃审只对测试账套开放；存货核算期初记账后不能再新增、删除、弃审。
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + ("stock_opening",) + ARAP_KINDS  # 应收应付仍在最后
CREATABLE_KINDS += ("stock_opening",)
DELETABLE_KINDS += ("stock_opening",)
VERIFIABLE_KINDS = VERIFIABLE_KINDS[: -len(ARAP_KINDS)] + ("stock_opening",) + ARAP_KINDS
# 来料 / 产品不良品处理单（QM05 / QM06）参照已审核的来料 / 产品检验单生单（每行一种处理方式，
# source_line_id 等于检验单 ID），可删除（未审核、无下游）；未受审批流控制时可直接审核、弃审，受控时仍走 workflow/*。
REJECT_KINDS = ("qm_incoming_reject", "qm_product_reject")
DELETABLE_KINDS += REJECT_KINDS
GENERATABLE_KINDS += REJECT_KINDS
VERIFIABLE_KINDS = tuple(name for name in KIND_NAMES if name in VERIFIABLE_KINDS + REJECT_KINDS)  # 按 KIND_NAMES 次序
GENERATE_SOURCES["qm_incoming_reject"] = ("qm_incoming_check",)
GENERATE_SOURCES["qm_product_reject"] = ("qm_product_check",)
# 其他报检单（QM11）、其他检验单（QM15）：无来源、没有审批流（不进 WORKFLOW_KINDS）；读取、列表、查找。
OTHER_QM_KINDS = ("qm_other_inspect", "qm_other_check")
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + OTHER_QM_KINDS + ARAP_KINDS  # 应收应付仍在最后
# 其他报检单无来源新增、审核 / 弃审（新增后桥按选项补审核）、删除；其他检验单参照其他报检单生单
# （source_line_id 是报检单表体 AUTOID）、直接审核 / 弃审、删除。
CREATABLE_KINDS += ("qm_other_inspect",)
DELETABLE_KINDS += OTHER_QM_KINDS
GENERATABLE_KINDS += ("qm_other_check",)
GENERATE_SOURCES["qm_other_check"] = ("qm_other_inspect",)
SOURCE_TYPES += ("qm_other_inspect",)
VERIFIABLE_KINDS = tuple(name for name in KIND_NAMES if name in VERIFIABLE_KINDS + OTHER_QM_KINDS)
# 来料 / 产品检验单、其他检验单、其他报检单可修改（只收 head，检验单的检验项目在 head.items；不收 lines）。
QM_UPDATE_KINDS = ("qm_incoming_check", "qm_product_check") + OTHER_QM_KINDS
UPDATABLE_KINDS += QM_UPDATE_KINDS
# 产品报检单 vouchers/verify 只收 unverify（不能修改、不能审核）。
VERIFIABLE_KINDS = tuple(name for name in KIND_NAMES if name in VERIFIABLE_KINDS + ("qm_product_inspect",))
# 采购结算单（卡片 99）：没有审核；读取、列表、查找，事件服务按 psufts / psdufts 轮询新增、修改、删除。
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + ("purchase_settle",) + ARAP_KINDS  # 应收应付仍在最后
# 采购结算单参照采购发票整张自动结算（不带 lines，表头只收 settle_date）、删除。
DELETABLE_KINDS += ("purchase_settle",)
GENERATABLE_KINDS += ("purchase_settle",)
GENERATE_SOURCES["purchase_settle"] = ("purchase_invoice",)
# 采购手工结算（vouchers/create，第一级）：lines 1 到 400 行，每行 in_line_id、invoice_line_id、quantity、amount（可省）。
CREATABLE_KINDS += ("purchase_settle",)
SOURCE_TYPES += ("purchase_invoice",)
# 不带 lines 时整单生成的（目标类型, 来源类型）；来源为空串表示任一来源。
WHOLE_GENERATE = (("sale_out", ""), ("sale_invoice", "sale_return"), ("purchase_settle", ""))
# 红冲蓝字销售发票（source_type=sale_invoice，id 是已复核的蓝字发票 SBVID），lines 可省略（整张按剩余红冲）。
GENERATE_SOURCES["sale_invoice"] += ("sale_invoice",)
SOURCE_TYPES += ("sale_invoice",)
WHOLE_GENERATE += (("sale_invoice", "sale_invoice"),)
# 蓝字采购入库参照到货单（source_type=arrival，id 是到货单 ID），lines 可省略（按各行剩余可入库数量整单生成）。
GENERATE_SOURCES["purchase_in"] += ("arrival",)
WHOLE_GENERATE += (("purchase_in", "arrival"),)
# 货位调整单（U8 单据类型 19，AdjustPVouch）可读、列表；新增、审核、弃审、删除在所有账套开放，不能修改。
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + ("position_adjust",) + ARAP_KINDS  # 应收应付仍在最后
CREATABLE_KINDS += ("position_adjust",)
DELETABLE_KINDS += ("position_adjust",)
VERIFIABLE_KINDS = tuple(name for name in KIND_NAMES if name in VERIFIABLE_KINDS + ("position_adjust",))
# 出入库调整单（存货核算，JustInVouch，入库 20 / 出库 21 等）、存货调价单（销售管理，卡片 SA18）只读（读取、列表、查找）；
# 事件服务按表头 ufts 轮询。出入库调整单的 verified 表示已记账，记账和期末处理走 ia/post、ia/period_end。
ADJUST_READ_KINDS = ("ia_adjust", "inventory_price_adjust")
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + ADJUST_READ_KINDS + ARAP_KINDS  # 应收应付仍在最后
# 供应商退款（ap_refund，AP48，应付的收款单）、客户退款（ar_refund，AR49，应收的付款单）：同收付款单可读、新增、修改、
# 删除、审核；金额为正，往来明细的负数由 U8 审核时写。不进核销、制单。并入 ARAP_KINDS，仍排在最后。
REFUND_KINDS = ("ap_refund", "ar_refund")
ARAP_KINDS += REFUND_KINDS
KIND_NAMES += REFUND_KINDS
CREATABLE_KINDS += REFUND_KINDS
DELETABLE_KINDS += REFUND_KINDS
UPDATABLE_KINDS += REFUND_KINDS
VERIFIABLE_KINDS += REFUND_KINDS
# 无来源销售出库单（cSource=库存，表头必填 cwhcode、ccuscode、cdepcode）。只给没启用销售管理的账套用：
# 已启用销售管理或库存选项「销售出库单由销售系统生成」打开时桥 409，须参照发货单生成。删除另放行来源库存的单据。
CREATABLE_KINDS += ("sale_out",)
# 退货申请单（销售管理，卡片 SA31，SA_ReturnsApplyMain）读取、列表、查找、单据追溯；事件服务按表头 ufts 轮询（写入见下）。
RETURN_APPLY_KINDS = ("sale_return_apply",)
KIND_NAMES = KIND_NAMES[: -len(ARAP_KINDS)] + RETURN_APPLY_KINDS + ARAP_KINDS  # 应收应付仍在最后
# 退货申请单可新增（每行参照已审核的蓝字发货单行）、修改（只改已有行）、删除、审核 / 弃审（销售 CO，VT 34）；
# 退货单另可参照已审核的退货申请单生单（source_line_id 是申请单行 AutoID）。不能关闭。
CREATABLE_KINDS += RETURN_APPLY_KINDS
DELETABLE_KINDS += RETURN_APPLY_KINDS
UPDATABLE_KINDS += RETURN_APPLY_KINDS
VERIFIABLE_KINDS = tuple(name for name in KIND_NAMES if name in VERIFIABLE_KINDS + RETURN_APPLY_KINDS)
GENERATE_SOURCES["sale_return"] += ("sale_return_apply",)
SOURCE_TYPES += ("sale_return_apply",)

_ALL_KINDS = frozenset(KIND_NAMES)
# 应收 / 应付票据（AP_Note）只是列表类型：vouchers/list 收，vouchers/load 和写路由都不收（单张读取走 notes/get）。
# 故意不并入 KIND_NAMES——事件服务的缺省类型、写路由的类型校验都以 KIND_NAMES 为准。
NOTE_KINDS = ("ar_note", "ap_note")
_LIST_KINDS = _ALL_KINDS | frozenset(NOTE_KINDS)
_CREATABLE = frozenset(CREATABLE_KINDS)
_DELETABLE = frozenset(DELETABLE_KINDS)
_UPDATABLE = frozenset(UPDATABLE_KINDS)
_CLOSABLE = frozenset(CLOSABLE_KINDS)
_GENERATABLE = frozenset(GENERATABLE_KINDS)
_VERIFIABLE = frozenset(VERIFIABLE_KINDS)
# 采购发票、销售发票另有应付 / 应收审核。
_ARAP_AUDIT_KINDS = ("purchase_invoice", "sale_invoice")
_WORKFLOW = frozenset(WORKFLOW_KINDS)


def whole_generate(kind: str, source_type: str = "") -> bool:
    """不带 lines 时是否整单生成：销售出库、红字销售发票（参照退货单）、采购结算。"""
    return (kind, "") in WHOLE_GENERATE or (kind, source_type) in WHOLE_GENERATE


def check_source(kind: str, source_type: str) -> str:
    if source_type not in GENERATE_SOURCES.get(kind, ()):
        raise ValueError("该单据类型不能从这种来源生单")
    return source_type


def require_verify(doc_id: int | None, action: str, kind: str = "") -> None:
    allowed = ("verify", "unverify") + (("arap_verify", "arap_unverify") if kind in _ARAP_AUDIT_KINDS else ())
    if doc_id is None or action not in allowed:
        raise ValueError("审核调用需要单据 id，以及 action=verify 或 unverify（采购、销售发票另可 arap_verify、arap_unverify）")


def _one_line_id(item: object, seen: list[int]) -> int:
    if type(item) is not int or not 1 <= item <= _INT32_MAX:
        raise ValueError("line_ids 必须是正整数")
    if item in seen:
        raise ValueError("line_ids 不能重复")
    return item


def check_line_ids(values: object) -> list[int]:
    """vouchers/close 的 line_ids：1 到 200 个不重复的正整数（从 u8co_client 移来，给那边腾行数）。"""
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError("line_ids 必须是 1 到 200 个正整数")
    if not 1 <= len(values) <= 200:
        raise ValueError("line_ids 必须是 1 到 200 个正整数")
    seen: list[int] = []
    for item in values:
        seen.append(_one_line_id(item, seen))
    return seen
