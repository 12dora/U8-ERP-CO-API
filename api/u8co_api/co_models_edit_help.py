"""修改、参照生单请求体（co_models_edit）的字段说明。各单据类型的小节在各自的校验模块里。"""

from __future__ import annotations

from u8co_api.co_doctext import field_doc
from u8co_api.co_edit_dispatch_add import DISPATCH_ADD_HELP
from u8co_api.co_edit_qm import QM_UPDATE_HELP
from u8co_api.co_gen_pu_settle import SETTLE_GEN_HELP
from u8co_api.co_gen_qm import QM_GEN_HELP
from u8co_api.co_gen_red_sale import RED_SALE_HELP
from u8co_api.co_gen_sale_out import SALE_OUT_LINES_HELP
from u8co_api.co_gen_transfer import TRANSFER_GEN_HELP
from u8co_api.co_models_bom import BOM_UPDATE_HELP
from u8co_api.co_models_mo_update import MO_UPDATE_HELP
from u8co_api.co_return_apply import APPLY_GENERATE_HELP, APPLY_UPDATE_HELP

# 期初结存单不能修改，不在名单里（co_models_stock_opening.refuse_no_update）。
UPDATE_TYPE_HELP = field_doc(
    "可修改的单据类型。",
    (
        "",
        (
            "销售订单、采购订单、其他入库单、其他出库单、调拨单、采购入库单（无来源）、请购单",
            "形态转换单、调拨申请单；收款单、付款单、应收单、应付单",
            "生单来的：发货单、退货单、销售发票、销售出库单、产成品入库单、材料出库单",
            "生单来的：采购入库单（参照生成）、到货单、采购退货单、采购发票",
            "生单来的类型不能新增行；发货单、来源为库存的销售出库单除外，可增删行、不能换仓库",
            "物料清单、生产订单、退货申请单",
            "来料检验单、产品检验单、其他检验单、其他报检单",
        ),
    ),
)
UPDATE_HEAD_HELP = "\n\n".join(
    (
        field_doc(
            "要改的表头，只改列出的字段。",
            (
                "规则",
                (
                    "值只能是字符串、数字或布尔，不能填主键、单号或审核人",
                    "检验单的 items 是检验项目列表",
                ),
            ),
        ),
        QM_UPDATE_HELP,
    )
)
UPDATE_LINES_HELP = "\n\n".join(
    (
        field_doc(
            "明细变更，0 到 200 行。",
            (
                "规则",
                (
                    "op 为 add、update 或 delete",
                    "add 不能带 line_id；update 和 delete 要有不重复的 line_id",
                    "delete 只能有 op 和 line_id；update 至少再改一个字段",
                    "生单来的单据不能 add（发货单、来源为库存的销售出库单除外）",
                    "生单来的单据不能删光明细，数量只能减少（退货单、采购退货单填正数）",
                ),
            ),
        ),
        DISPATCH_ADD_HELP,
        BOM_UPDATE_HELP,
        MO_UPDATE_HELP,
        APPLY_UPDATE_HELP,
    )
)

GENERATE_TYPE_HELP = field_doc(
    "参照生单的目标类型。",
    (
        "",
        (
            "发货单、销售出库单、采购入库单、销售发票、材料出库单、产成品入库单、采购发票、到货单",
            "采购退货单、退货单（红字发货单）",
            "来料报检单、产品报检单、来料检验单、产品检验单、来料不良品处理单、产品不良品处理单",
            "调拨单（参照调拨申请单）",
            "采购结算单（参照采购发票，整张自动结算）",
        ),
    ),
)
GENERATE_ID_HELP = field_doc(
    "来源单据主键，按目标单据取。",
    (
        "",
        (
            "发货单来自销售订单；销售出库来自发货单；销售发票来自发货单",
            "采购入库来自采购订单、来料检验单或蓝字到货单（ID）",
            "材料出库来自生产订单（MoId）",
            "产成品入库来自产品检验单（检验单 ID）、产品不良品处理单（处理单 ID）或生产订单（MoId）",
            "采购发票来自采购入库单（红字入库单生成红字发票）",
            "到货单来自采购订单（POID）；采购退货单来自蓝字到货单（ID）或采购订单（POID）",
            "退货单（红字发货单）来自已审核的蓝字发货单（DLID）",
            "退货单也可来自已审核的退货申请单（source_type=sale_return_apply，申请单 ID）",
            "红字销售发票（source_type=sale_return）来自已审核的退货单（DLID）",
            "红冲蓝字销售发票（source_type=sale_invoice）来自已复核的蓝字销售发票（SBVID）",
            "来料报检单来自到货单（ID）；产品报检单来自生产订单（MoId）",
            "检验单来自报检单（ID）；其他检验单来自其他报检单（ID）；不良品处理单来自检验单（ID）",
            "调拨单来自已审核的调拨申请单（ID）；采购结算单来自已复核的采购发票（PBVID）",
        ),
    ),
)
GENERATE_SOURCE_HELP = (
    "来源单据类型，省略时取缺省来源。\n\n"
    "| 目标单据 | 可选来源（第一个是缺省） |\n"
    "|---|---|\n"
    "| 采购入库 | purchase_order、qm_incoming_check、purchase_return（采购退货单，生成红字采购入库）、"
    "arrival（蓝字到货单，lines 可省） |\n"
    "| 产成品入库 | qm_product_check、qm_product_reject、production_order |\n"
    "| 采购发票 | 只能是 purchase_in |\n"
    "| 采购退货单 | arrival、purchase_order |\n"
    "| 销售发票 | dispatch、sale_return（退货单，生成红字发票）、sale_invoice（蓝字发票，红冲） |\n"
    "| 采购结算单 | 只能是 purchase_invoice |\n"
    "| 其它目标 | 只能是各自唯一的来源 |"
)
GENERATE_HEAD_HELP = "\n\n".join(
    (
        field_doc(
            "目标表头覆盖。",
            (
                "规则",
                (
                    "值只能是字符串、数字或布尔，不能填主键、单号或审核人；只有检验单的 items 可以是列表",
                    "销售出库不能带表头",
                    "采购发票只收字符串 cPBVCode（必填，最长 30）、cPBVBillType（01 或 02）",
                    "采购发票另收 dPBVDate（yyyy-MM-dd）、cPBVMemo（最长 255）",
                    "到货单和采购退货单只收字符串 cWhCode、dDate（yyyy-MM-dd）、cMemo、cDepCode，可省略",
                    "参照来料检验单的采购入库必须有 cWhCode",
                    "退货单只收 dDate（yyyy-MM-dd）、cMemo、cDepCode、cPersonCode、cDefine1–16，可省略",
                    "退货单另可带布尔 invoiced：false 未开票退货，true 已开票退货，省略时按可退数量自动选",
                ),
            ),
        ),
        QM_GEN_HELP,
        RED_SALE_HELP,
        TRANSFER_GEN_HELP,
        SETTLE_GEN_HELP,
    )
)
GENERATE_LINES_HELP = field_doc(
    "来源明细，1 到 200 行；每行要有 source_line_id 和大于 0 的 quantity。",
    ("销售出库", SALE_OUT_LINES_HELP),
    (
        "材料出库、产成品入库",
        (
            "材料出库的 source_line_id 是子件分配 AllocateId",
            "两者的行另可带 cbatch、cbmemo 和货位 cposition",
            "产成品入库参照产品检验单：非合并检验只能有 1 行，source_line_id 是检验单 ID",
            "合并检验（BMERGECHECKFLAG=1）每个来源一行（1 到 20 行），数量不超过该来源的剩余",
            "合并检验的 source_line_id 是合并来源 AUTOID（vouchers/load 的 merge_sources）",
            "参照产品不良品处理单（qm_product_reject）或生产订单时只能有 1 行",
            "这两种来源的 source_line_id 分别是处理单表体 AUTOID、订单行 MoDId",
        ),
    ),
    (
        "采购发票、到货单、采购退货单",
        (
            "行只能有 source_line_id 和 quantity",
            "到货单的 source_line_id 是 PO_Podetails.ID",
            "采购退货单的 source_line_id 是原到货单行 Autoid 或 PO_Podetails.ID，quantity 填正数（桥写负数）",
            "参照红字入库单的采购发票同样 quantity 填正数，桥写负数、表头 bNegative=1",
        ),
    ),
    (
        "采购入库",
        (
            "参照来料检验单：只能有 1 行，source_line_id 是检验单 ID，可带 cbatch、cbmemo、cposition",
            "参照采购退货单（红字采购入库）：source_line_id 是退货单行 Autoid，quantity 填正数（桥写负数）",
            "参照到货单：source_line_id 是到货单行 Autoid，可带 cbatch、cbmemo、cposition、cWhCode",
            "参照到货单时表头没给仓库，各行 cWhCode 须一致",
            "参照到货单省略 lines 时按各行剩余可入库数量整单生成；需要来料检验的存货 409",
        ),
    ),
    (
        "货位 cposition",
        (
            "采购入库（四种来源）、材料出库、产成品入库的行都可带",
            "仓库启用货位管理时每行必填该仓库的末级货位，否则不能填",
        ),
    ),
    (
        "退货单",
        (
            "source_line_id 是原发货单行 iDLsID",
            "quantity 填正数（桥写负数，不超过发货数量减累计退货数量）",
            "另可带 cWhCode、cMemo、cDefine22–37",
            *APPLY_GENERATE_HELP,
        ),
    ),
)
