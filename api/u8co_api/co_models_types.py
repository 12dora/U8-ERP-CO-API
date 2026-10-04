"""/v1/co 单据类型的枚举与说明（读取、审核、新增、删除各一套）。co_models 原样再导出。"""

from __future__ import annotations

from typing import Literal

from u8co_api.co_doctext import field_doc
from u8co_api.co_srcless import SRCLESS_HELP

VOUCHER_TYPE_HELP = field_doc(
    "单据类型。",
    (
        "",
        (
            "供销：销售订单、发货单、销售发票、采购订单、到货单、采购发票、请购单",
            "出入库：采购入库、其他入库、其他出库、产成品入库、材料出库、销售出库、调拨单",
            "生产与质量：生产订单、来料检验、产品检验、来料不良品处理、产品不良品处理",
            "往来：收款单、付款单、应收单、应付单",
            "供应商退款（ap_refund，应付的收款单 AP48）、客户退款（ar_refund，应收的付款单 AR49）：金额为正",
            "采购退货单（红字到货单）、退货单（红字发货单）",
            "来料报检单、产品报检单：读取、列表、参照生单、删除",
            "物料清单（bom）：只含标准 BOM，id 是 BomId，code 是母件存货编码",
            "形态转换单（shape_change）、调拨申请单（transfer_request）、盘点单（stock_check）",
            "期初结存单（stock_opening）：库存期初，U8 单据类型 34",
            "其他报检单（qm_other_inspect）：可无来源新增、删除；没有审批流",
            "其他检验单（qm_other_check）：参照其他报检单生单、审核、删除；没有审批流",
            "采购结算单（purchase_settle）：id 是 PSVID，没有审核",
            "采购结算单可参照采购发票生单（自动结算）、删除，可手工结算（新增，第一级写入，按写入策略放行）",
            "货位调整单（position_adjust）：U8 单据类型 19，同一仓库内货位之间移库",
            "出入库调整单（ia_adjust）：存货核算的入库 / 出库调整单，只读，verified 表示已记账",
            "存货调价单（inventory_price_adjust）：只读",
            "退货申请单（sale_return_apply）：销售管理卡片 SA31；数量、金额为负数",
        ),
    ),
)
VERIFY_TYPE_HELP = field_doc(
    "可直接审核的单据类型。",
    (
        "",
        (
            "供销：销售订单、发货单、销售发票、采购订单、到货单",
            "出入库：采购入库、其他入库、其他出库、产成品入库、材料出库、销售出库、调拨单",
            "生产订单；收款单、付款单、应收单、应付单、供应商退款（ap_refund）、客户退款（ar_refund）",
        ),
    ),
    (
        "规则",
        (
            "采购发票的 verify 是采购复核（取消复核）",
            "应付款管理的审核用 action=arap_verify / arap_unverify（销售发票同理，应收审核）",
            "采购退货单（红字到货单）同到货单；退货单（红字发货单）同发货单；请购单同采购订单",
            "物料清单（bom）：审核只收未审核的版本，弃审只收已审核的版本",
            "形态转换单审核时生成其他出入库单（见 generated）；调拨申请单审核不生单",
            "不良品处理单未受审批流控制时可直接审核、弃审；受控时返回 409，请用 workflow/*",
            "期初结存单（stock_opening）只有审核、弃审（第二级写入，默认关闭，打开后只限测试账套），没有记账",
            "期初结存单审核日期写成单据日期；存货核算期初记账后不能弃审",
            "货位调整单（position_adjust）审核时 U8 写货位台账（每行一出一入），弃审退回",
            "货位调整单审核、弃审的响应另有 ledger_rows、bin_moves",
            "其他检验单（qm_other_check）不受审批流控制，可直接审核、弃审（弃审要求没有入库、不良品处理）",
            "其他报检单（qm_other_inspect）可审核、弃审：新增后桥按选项补审核，没成功时用它补审；弃审要求还没有其他检验单",
            "退货申请单（sale_return_apply）可审核、弃审（弃审要求还没有退货单参照它）",
        ),
    ),
    (
        "限制",
        (
            "检验单不能直接审核，请用 workflow/*",
            "来料报检单不支持单独审核、弃审（保存时由 U8 按选项自动审核；删除时桥会先弃审）",
            "产品报检单（qm_product_inspect）只支持弃审（action=unverify，要求还没有检验单），不能审核、不能修改",
            "盘点单暂不支持审核（请到 U8 客户端审核）",
        ),
    ),
)
CREATE_TYPE_HELP = field_doc(
    "可新增的单据类型。",
    (
        "",
        (
            "销售订单、采购订单、其他入库单、其他出库单、调拨单、采购入库单（无来源）、请购单",
            "收款单、付款单、应收单、应付单、供应商退款（ap_refund）、客户退款（ar_refund）",
            "生产订单；物料清单（bom，标准 BOM 的新版本）",
            "形态转换单（shape_change）、调拨申请单（transfer_request）",
            "盘点单（stock_check）：账面数量不给时按现存量填",
            "期初结存单（stock_opening）：第二级写入，默认关闭，打开后只限测试账套",
            "期初结存单走 U8 官方 EAI 导入，每行一张单据；日期固定为库存启用日前一天",
            "货位调整单（position_adjust）：表头 cwhcode 是货位管理仓库",
            "货位调整单每行 cinvcode、cbposcode、caposcode、iquantity",
            "其他报检单（qm_other_inspect，无来源）",
            "退货申请单（sale_return_apply）：每行参照已审核的蓝字发货单行",
            "采购结算单（purchase_settle）：手工结算；明细见 lines 的说明",
        ),
    ),
    SRCLESS_HELP,
)
DELETE_TYPE_HELP = field_doc(
    "可删除的未审核单据，括号内为另需满足的条件。",
    (
        "",
        (
            "销售订单、其他入库单、其他出库单、采购订单、调拨单、发货单、到货单、销售出库单",
            "采购入库单、销售发票、采购发票、收款单、付款单、应收单、应付单、供应商退款、客户退款",
            "参照生产订单或产品检验单生成的材料出库单和产成品入库单",
            "采购退货单（未报检、未入库）",
            "退货单（红字发货单，未被销售出库或销售发票引用）",
            "请购单（未审核、未关闭、未被采购订单参照）",
            "生产订单（行状态都未审核、未提交审批、未被材料出库或产成品入库引用）",
            "物料清单（未审核、未进审批流、没有生产订单或委外订单引用）",
            "期初结存单（未审核，存货核算尚未期初记账；第二级写入，默认关闭，打开后只限测试账套）",
            "来料报检单、产品报检单（没有检验单引用；已审核的先弃审再删除，要有弃审权限）",
            "来料检验单、产品检验单（未审核、未提交审批、未被入库或不良品处理引用）",
            "来料不良品处理单、产品不良品处理单（未审核、未被下游单据引用）；删除后 U8 恢复检验单的不良品处理标记",
            "形态转换单、盘点单；调拨申请单（未关闭、未生成调拨单）；货位调整单（未审核）",
            "其他报检单（没有其他检验单引用；已审核的先弃审再删除，要有弃审权限）",
            "其他检验单（未审核、未被入库或不良品处理引用）",
            "采购结算单（存货核算未记账、关联发票未应付审核、结算期间采购未结账）；删除后 U8 清除发票的结算日期",
            "退货申请单（未审核、没有退货单参照）",
        ),
    ),
)
Cell = str | int | float | bool
VoucherType = Literal[
    "sale_order",
    "dispatch",
    "sale_invoice",
    "purchase_order",
    "arrival",
    "purchase_invoice",
    "purchase_in",
    "other_in",
    "other_out",
    "product_in",
    "material_out",
    "sale_out",
    "transfer",
    "production_order",
    "qm_incoming_check", "qm_product_check",
    "qm_incoming_reject", "qm_product_reject",
    "ar_receipt",
    "ap_payment",
    "ar_bill",
    "ap_bill",
    # 供应商退款（AP48）、客户退款（AR49），同收付款单
    "ap_refund", "ar_refund",
    # 采购退货单（红字到货单）
    "purchase_return",
    # 退货单（红字发货单）
    "sale_return",
    # 请购单
    "purchase_requisition",
    # 来料报检单（QM01）、产品报检单（QM02），只读
    "qm_incoming_inspect", "qm_product_inspect",
    # 物料清单（标准 BOM，U8API BOM）
    "bom",
    # 形态转换单、调拨申请单、盘点单（USERPCO.VoucherCO）
    "shape_change", "transfer_request", "stock_check",
    # 期初结存单（库存期初，U8 单据类型 34）
    "stock_opening",
    # 其他报检单（QM11）、其他检验单（QM15），无来源、没有审批流，只读
    "qm_other_inspect", "qm_other_check",
    # 采购结算单（卡片 99），没有审核；参照采购发票生单、删除见 GenerateType、DeleteType
    "purchase_settle",
    # 货位调整单（U8 单据类型 19）
    "position_adjust",
    # 出入库调整单（存货核算，U8 单据类型 20 / 21 等）、存货调价单（销售管理，卡片 SA18），只读
    "ia_adjust", "inventory_price_adjust",
    # 退货申请单（销售管理，卡片 SA31）；可新增、修改、删除、审核
    "sale_return_apply",
]
VerifyType = Literal[
    "sale_order",
    "dispatch",
    "sale_invoice",
    "purchase_order",
    "arrival",
    # 采购发票复核
    "purchase_invoice",
    "purchase_in",
    "other_in",
    "other_out",
    "product_in",
    "material_out",
    "sale_out",
    "transfer",
    "production_order",
    "ar_receipt",
    "ap_payment",
    "ar_bill",
    "ap_bill",
    # 供应商退款（AP48）、客户退款（AR49），同收付款单
    "ap_refund", "ar_refund",
    # 采购退货单审核 / 弃审
    "purchase_return",
    # 退货单审核 / 弃审
    "sale_return",
    # 请购单审核 / 弃审
    "purchase_requisition",
    # 物料清单审核 / 弃审（U8API BomAuditing / BomUnauditing）
    "bom",
    # 形态转换单、调拨申请单（盘点单审核暂不支持：U8 生成盘盈盘亏单时报类型不匹配）
    "shape_change", "transfer_request",
    # 期初结存单只有审核、弃审，没有记账
    "stock_opening",
    # 不良品处理单（未受审批流控制时）
    "qm_incoming_reject", "qm_product_reject",
    # 货位调整单（所有账套可写）
    "position_adjust",
    # 其他报检单、其他检验单（不受审批流控制）
    "qm_other_inspect", "qm_other_check",
    # 产品报检单只收 unverify（co_edit_qm.check_qm_verify）
    "qm_product_inspect",
    # 退货申请单（销售 CO，VT 34）
    "sale_return_apply",
]
CreateType = Literal[
    "sale_order",
    "other_in",
    "other_out",
    "purchase_order",
    "transfer",
    "ar_receipt",
    "ap_payment",
    "ar_bill",
    "ap_bill",
    # 供应商退款（AP48）、客户退款（AR49），同收付款单
    "ap_refund", "ar_refund",
    # 无来源采购入库单。
    "purchase_in",
    # 请购单
    "purchase_requisition",
    # 生产订单（U8API MOrderAdd）
    "production_order",
    # 物料清单（U8API BomAdd）
    "bom",
    # 形态转换单、调拨申请单、盘点单（USERPCO.VoucherCO）
    "shape_change",
    "transfer_request",
    "stock_check",
    # 无来源发货单、先开票销售发票、无来源到货单、无来源材料出库单（co_srcless）
    "dispatch",
    "sale_invoice",
    "arrival",
    "material_out",
    # 期初结存单（日期由桥固定为库存启用日前一天）
    "stock_opening",
    # 货位调整单（所有账套可写）
    "position_adjust",
    # 其他报检单（无来源，U8 按选项自动审核）；无来源销售出库单（来源库存，说明见 co_srcless.SRCLESS_HELP）
    "qm_other_inspect", "sale_out",
    # 退货申请单（每行参照已审核的蓝字发货单行）
    "sale_return_apply",
    # 采购手工结算（第一级写入，co_pu_settle_man）
    "purchase_settle",
]
DeleteType = Literal[
    "sale_order",
    "other_in",
    "other_out",
    "purchase_order",
    "transfer",
    "dispatch",
    "arrival",
    "sale_out",
    "purchase_in",
    "sale_invoice",
    "ar_receipt",
    "ap_payment",
    "ar_bill",
    "ap_bill",
    # 供应商退款（AP48）、客户退款（AR49），同收付款单
    "ap_refund", "ar_refund",
    "material_out",
    "product_in",
    "purchase_invoice",
    # 采购退货单
    "purchase_return",
    # 退货单（红字发货单）
    "sale_return",
    # 请购单
    "purchase_requisition",
    # 生产订单（U8API MOrderDelete）
    "production_order",
    # 物料清单（U8API BomDelete）
    "bom",
    # 报检单、检验单（VoucherOperate delete）
    "qm_incoming_inspect",
    "qm_product_inspect",
    "qm_incoming_check", "qm_product_check",
    # 形态转换单、调拨申请单、盘点单（USERPCO.VoucherCO）
    "shape_change",
    "transfer_request",
    "stock_check",
    # 期初结存单（未审核，存货核算尚未期初记账）
    "stock_opening",
    # 不良品处理单（未审核、无下游）
    "qm_incoming_reject", "qm_product_reject",
    # 货位调整单（未审核；所有账套可写）
    "position_adjust",
    # 其他报检单、其他检验单（VO 接口，U8 自行提交）
    "qm_other_inspect", "qm_other_check",
    # 采购结算单（VoucherCO_PU.Delete，在桥的事务里）
    "purchase_settle",
    # 退货申请单（未审核、没有退货单参照）
    "sale_return_apply",
]

