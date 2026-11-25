"""用例名单。空 bag 也能列出全部名字，供命令行在连服务器之前拒绝未知用例。"""

from __future__ import annotations

from co.e2e.casespec.mo_gen_bad import mo_gen_cases
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.smoke import smoke_cases
from co.e2e.casespec.doc_basic import doc_cases
from co.e2e.casespec.qm_workflow import wf_cases
from co.e2e.casespec.edit_close_gen import edit_close_gen_cases
from co.e2e.casespec.arap_gen import arap_gen_cases
from co.e2e.casespec.arc_write import cases as arc_cases
from co.e2e.casespec.gl_voucher import cases as fin_cases
from co.e2e.casespec.returns_misc import returns_misc_cases
from co.e2e.casespec.wiring import wiring_cases
from co.e2e.casespec.arc_write_more import arc_more_cases
from co.e2e.casespec.arc_project_delete import proj_cases
from co.e2e.casespec.bom_write import bomw_cases
from co.e2e.casespec.mo_create import moc_cases
from co.e2e.casespec.report_detail import detail_cases
from co.e2e.casespec.report_stock import stock_rpt_cases
from co.e2e.casespec.report_trace import trace_cases
from co.e2e.casespec.arc_gl import glarc_cases
from co.e2e.casespec.qm_write import qmi_cases
from co.e2e.casespec.sale_red_invoice import rs_cases
from co.e2e.casespec.so_lock import lock_cases
from co.e2e.casespec.pu_red_chain import redpur_cases
from co.e2e.casespec.stock_other_docs import sm_cases
from co.e2e.casespec.transfer_gen import tg_cases
from co.e2e.casespec.srcless import srcless_cases
from co.e2e.casespec.sale_return_so import sale_return_so_cases
from co.e2e.casespec.report_openings import open_rpt_cases
from co.e2e.casespec.arc_partner import partner_cases
from co.e2e.casespec.stock_plus import stockplus_cases
from co.e2e.casespec.mo_update import mou_cases
from co.e2e.casespec.arap_verify import arv_cases
from co.e2e.casespec.writeoff import hx_cases
from co.e2e.casespec.writeoff_auto import hxa_cases
from co.e2e.casespec.gl_post import glpost_cases
from co.e2e.casespec.arap_voucher import av_cases
from co.e2e.casespec.openings_pu import openings_cases
from co.e2e.casespec.periods_close import periods_cases
from co.e2e.casespec.writeoff_prepay_fx import arap_more_cases
from co.e2e.casespec.arap_voucher_merge import voucher_merge_cases
from co.e2e.casespec.arc_exch import exch_cases
from co.e2e.casespec.arc_reason import reason_cases
from co.e2e.casespec.binned_sale_out import binned_sale_out_cases
from co.e2e.casespec.ia_post import ia_cases
from co.e2e.casespec.ia_opening import ia_opening_cases
from co.e2e.casespec.live_close import live_close_cases
from co.e2e.casespec.ia_voucher import ia_voucher_cases
from co.e2e.casespec.openings_arap import openings_arap_cases
from co.e2e.casespec.openings_live import openings_live_cases
from co.e2e.casespec.qm_reject import qm_reject_cases
from co.e2e.casespec.qm_reject_update import qm_reject_update_cases
from co.e2e.casespec.purchase_in_qm_reject import purchase_in_qm_reject_cases
from co.e2e.casespec.mo_subs_replace import mo_subs_replace_cases
from co.e2e.casespec.stock_opening import stock_opening_cases
from co.e2e.casespec.arap_extra import arap_extra_cases
from co.e2e.casespec.arap_proc import arap_proc_cases
from co.e2e.casespec.flip import flip_cases
from co.e2e.casespec.mo_probe import mo_probe_cases
from co.e2e.casespec.notes import notes_cases
from co.e2e.casespec.stock_binned import stock_binned_cases
from co.e2e.casespec.bad_debt import bad_debt_cases
from co.e2e.casespec.bom_qty import bom_more_cases
from co.e2e.casespec.notes_return import notes_more_cases
from co.e2e.casespec.notes_split import split_reg_cases
from co.e2e.casespec.stock_more import stock_more_cases
from co.e2e.casespec.gl_controlled import gl_controlled_cases
from co.e2e.casespec.ia_sa_fa import ia_sa_fa_cases
from co.e2e.casespec.made_inv import made_cases
from co.e2e.casespec.mo_alloc import mo_alloc_cases
from co.e2e.casespec.position_adjust import position_adjust_cases
from co.e2e.casespec.pu_settle_read import pu_settle_cases
from co.e2e.casespec.pu_settle_write import pu_settle_write_cases
from co.e2e.casespec.pu_arrival_in import pu_arr_in_cases
from co.e2e.casespec.arrival_close import arrival_close_cases
from co.e2e.casespec.pu_settle_manual import pu_settle_man_cases
from co.e2e.casespec.qm_binned_in import qm_bin_cases
from co.e2e.casespec.qm_merged_in import qm_merged_cases
from co.e2e.casespec.qm_merge import qm_merge_cases
from co.e2e.casespec.qm_other_read import qm_other_cases
from co.e2e.casespec.qm_other_write import qm_other_write_cases
from co.e2e.casespec.sale_out_trim import sale_out_trim_cases
from co.e2e.casespec.arap_refund import arap_refund_cases
from co.e2e.casespec.exgain_replay import exgain_replay_cases
from co.e2e.casespec.refund_voucher import refund_voucher_cases
from co.e2e.casespec.fa_eq import fa_eq_cases
from co.e2e.casespec.settle_replay import settle_replay_cases
from co.e2e.casespec.return_apply import return_apply_cases
from co.e2e.casespec.sale_out_nosrc import sale_out_nosrc_cases
from co.e2e.casespec.sale_out_nosrc_update import sale_out_nosrc_update_cases
from co.e2e.casespec.sale_blue_red import blue_red_cases
from co.e2e.casespec.dispatch_add import dispatch_add_cases
from co.e2e.casespec.invoice_multi import invoice_multi_cases
from co.e2e.casespec.dispatch_line_bdefine import dispatch_bdefine_cases
from co.e2e.casespec.gl_unpost import gl_unpost_cases
from co.e2e.casespec.write_policy import write_policy_cases
from co.e2e.casespec.gl_reverse import gl_reverse_cases
from co.e2e.casespec.gl_transfer import gl_transfer_cases
from co.e2e.casespec.mgmt_gl import mgmt_gl_cases
from co.e2e.casespec.mgmt_sa import mgmt_sa_cases
from co.e2e.casespec.col_auth import col_auth_cases
from co.e2e.casespec.perm_check import perm_cases
from co.e2e.casespec.qm_other_update import qm_other_update_cases
from co.e2e.casespec.qm_update import qmu_cases
from co.e2e.casespec.taxno_search import taxno_cases
from co.e2e.casespec.dry_suite import dry_suite_cases
from co.e2e.casespec.rpt_acct import rpt_acct_cases
from co.e2e.casespec.rpt_fin import rpt_fin_cases
from co.e2e.casespec.rpt_mfg import rpt_mfg_cases
from co.e2e.casespec.rpt_pu import rpt_pu_cases
from co.e2e.casespec.rpt_qm import rpt_qm_cases
from co.e2e.casespec.rpt_sa import rpt_sa_cases
from co.e2e.casespec.rpt_stock_age import rpt_stock_age_cases


def role_report_cases() -> list[CaseSpec]:
    """按角色分族的 17 张只读报表，每张一个读用例（质量、生产、库龄、采购、销售、收付款与现金流、账务核对）。"""
    return (
        rpt_qm_cases()
        + rpt_mfg_cases()
        + rpt_stock_age_cases()
        + rpt_pu_cases()
        + rpt_sa_cases()
        + rpt_fin_cases()
        + rpt_acct_cases()
    )


def build_cases(bag: dict) -> list[CaseSpec]:
    first = smoke_cases(bag) + wf_cases() + doc_cases(bag) + edit_close_gen_cases()
    later = returns_misc_cases() + wiring_cases() + moc_cases() + bomw_cases() + arc_more_cases() + proj_cases() + detail_cases()
    # 按角色分族的报表（role_report_cases，只读）与它们合一条语句（PLR0915）。
    later += trace_cases() + stock_rpt_cases() + glarc_cases() + role_report_cases()
    # 质量单据新增 / 删除；检验单、其他报检单、其他检验单修改（自建自删，失败时 QMU_CLEANUP / 尽力补偿）。
    later += qmi_cases() + qmu_cases() + qm_other_update_cases()
    # 不良品处理单参照检验单生单、审核弃审、删除；不良品处理单修改；生产订单修改替换替代料
    # （都自建自删，失败时尽力补偿）
    later += qm_reject_cases() + qm_reject_update_cases() + purchase_in_qm_reject_cases() + mo_subs_replace_cases()
    later += rs_cases()  # 红字销售发票参照退货单
    later += redpur_cases()  # 红字采购链路
    later += lock_cases()  # 销售订单 / 采购订单锁定、解锁
    later += sm_cases()  # 形态转换单、调拨申请单、盘点单
    later += tg_cases()  # 调拨单参照调拨申请单
    later += srcless_cases() + sale_return_so_cases()  # 无来源新增、产成品入库参照生产订单；退货单参照销售订单
    later += open_rpt_cases()  # 期初余额报表、凭证与单据附件列表
    later += partner_cases()  # 客户、供应商的银行账户和联系人
    later += exch_cases()  # 汇率新增（EAI）、修改删除（受控 SQL）
    later += reason_cases()  # 原因码新增、修改、删除（EAI）
    later += stockplus_cases()  # 销售出库批号货位拆行、外币采购入库、红字退库
    later += mou_cases()  # 生产订单修改（子件全部重送）
    later += arv_cases()  # 采购发票应付审核、销售发票应收审核及弃审
    later += hx_cases()  # 应收 / 应付核销（取消核销自己清理，失败时 HX_CLEANUP 补偿）
    # 总账记账（不清理，快照还原）；记账、取消记账、删除（测试账套）；红字冲销（红字凭证自己删除，原凭证快照还原）。
    # 期间损益结转、自定义转账在带历史数据的账套上的核对（预演比对；重新生成的留给快照还原）。
    later += glpost_cases() + gl_unpost_cases() + gl_reverse_cases() + gl_transfer_cases()
    later += hxa_cases()  # 应收自动核销（取消核销自己清理，失败时 HXA_CLEANUP 补偿）
    later += av_cases()  # 应收制单、取消制单（取消制单自己清理，失败时 AV_CLEANUP 补偿）
    later += voucher_merge_cases()  # 应收合并制单（自建两张收款单，取消制单、弃审、删除自己清理）
    later += openings_cases()  # 采购期初记账（预演、已记账时 409、坏 module）
    later += periods_cases()  # 月末结账（预演逐月结账、顺序拒绝、坏 module；非测试账套 N/A）
    later += ia_cases()  # 存货核算（预演记账、预演期末处理、坏 action；非测试账套 N/A）
    later += openings_arap_cases()  # 应收应付期初单据（测试账套名单、启用月已结账、新增到删除的一整轮）
    later += stock_opening_cases()  # 库存期初结存单（测试账套名单、库存已月结时 U8 拒绝、不能修改）
    later += openings_live_cases()  # 已结账月份临时打开后期初单据、期初结存单的一整轮（独占，结账标志原样恢复）
    later += ia_opening_cases()  # 存货核算期初记账（独占；取消必成功的账套才真记账，其余只预演）
    later += binned_sale_out_cases()  # 有货位记录的销售出库删除
    later += arap_more_cases()  # 预收 / 预付行核销、外币核销（取消核销自己清理，失败时按核销号补偿）
    later += arap_extra_cases()  # 自动核销 include_prepay（取消核销自己清理，失败时按核销号补偿）
    later += arap_proc_cases()  # 应收冲应付、并账、红票对冲、汇兑损益的一整轮（取消到处理之前，失败时倒序补偿）
    later += notes_cases()  # 应收票据登记、结算、贴现、背书、分包结算的一整轮（取消、删票据复原，失败时倒序补偿）
    later += split_reg_cases()  # 分包票据登记、子票结算与取消、删除（可用区间逐步核对，失败时倒序补偿）
    later += notes_more_cases()  # 应收票据退回（整张、分包子票）、应付票据登记到退回（仅测试账套）的一整轮（失败时倒序补偿）
    later += bad_debt_cases()  # 坏账发生、收回、计提的一整轮（取消到处理之前、参数行复原，失败时倒序补偿）
    later += stock_more_cases()  # 库存待实测补用例：改批号日期列、保质期入库、货位盘点、单号卡片、调拨申请关闭（自建自删）
    later += stock_binned_cases()  # 有货位记录的采购入库、其他出库删除（货位台账写入时点）
    # 参照来料检验单生成带货位的采购入库后删除；货位调整单新建、审核、弃审、删除（货位结存回到原值，所有账套）。
    later += qm_bin_cases() + position_adjust_cases()
    # 参照合并检验的产品检验单按来源生成产成品入库后删除；产品检验单合并检验生成、删除（qm_merge）；其他报检单、其他检验单只读核对（没有数据 N/A）；
    # 采购结算单只读（读取对照、列表、增量、查找，没有数据 N/A）；出入库调整单、存货调价单、固定资产变动单与折旧报表只读核对。
    # 其他报检单新增、其他检验单生单 / 审核 / 删除的一整轮（qm_other_write_cases，自己删除，失败时尽力补偿）。
    # 退货申请单只读核对与写路由拒绝（return_apply_cases，没有数据 N/A）；经营管理报表（总账口径）只读核对（mgmt_gl_cases）；
    # 经营管理销售分析、往来账期只读核对（mgmt_sa_cases）。
    # 并在同一句里，build_cases 语句数有上限。
    later += qm_merged_cases() + qm_merge_cases() + qm_other_cases() + qm_other_write_cases() + pu_settle_cases() + pu_settle_write_cases() + ia_sa_fa_cases() + return_apply_cases() + mgmt_gl_cases() + mgmt_sa_cases()
    later += made_cases()  # 自建保质期存货的无来源采购入库（自建存货，本步删单删存货）
    # 生产订单子件固定用量、损耗率、辅计量（自建母件、物料清单和单位，本步删除）；ATO 生产订单参照销售订单生成
    # （带 ato 固定数据的账套，生成后删除；拒绝路径），并在同一句里，build_cases 语句数有上限。
    later += mo_alloc_cases() + mo_gen_cases()
    # 销售出库按行部分生单（跨仓库改回、第 2 步失败补偿）；总账受控科目选项关开两态（凭证删除、选项恢复）。都需要种子数据。
    # 来源库存的销售出库单修改（改数量、加行删行、换仓库 409，本步删除）。并在同一句里，build_cases 语句数有上限。
    # 无来源销售出库单新增（销售管理已启用时 409）与预演（sale_out_nosrc_cases，自建自删）。
    later += sale_out_trim_cases() + gl_controlled_cases() + sale_out_nosrc_update_cases() + sale_out_nosrc_cases()
    # 固定资产卡片新增 / 撤销本期新增（自建自删）、设备台账读取与新增（新设备留给快照还原）、拒绝路径（fa_eq）。
    # 与生产订单新增探查合一条语句（PLR0915）。
    later += fa_eq_cases() + mo_probe_cases()  # 生产订单新增探查：非自制件、没有物料清单的自制件（新建的订单本步删除）
    # 物料清单用量精度、只送行时表头保持、改生效日期（自建版本，本步删除）；供应商退款、客户退款：新建到删除的
    # 一整轮（自建自删）、列表与读取（没有数据 N/A）；采购手工结算（配对部分数量、红蓝入库对冲、红蓝发票对冲，
    # 自建自删，只限测试账套）；采购入库参照到货单（自建订单、到货单，本步删除）。并在同一句里，build_cases 语句数有上限。
    # 到货单整单关闭、打开（自建订单、到货单，本步删除）；退款单制单（自建、制单、取消制单、自删）。
    # 汇兑损益、采购手工结算在带历史数据的账套上按历史单据重做比对（不补偿，留给快照还原）。
    later += bom_more_cases() + arap_refund_cases() + refund_voucher_cases() + pu_settle_man_cases() + pu_arr_in_cases() \
        + arrival_close_cases() + exgain_replay_cases() + settle_replay_cases()
    later += flip_cases()  # 临时改选项或造数据、跑完原样恢复（采购取消记账、浮动汇率、信用余额表选项、价格表、别人锁定）
    # 发货单修改新增参照来源订单的行（自建订单、发货单，本步删除）、红冲蓝字销售发票（自建订单到蓝字发票，
    # 蓝字发票删不掉时留给快照还原），与写预演等合一条语句（PLR0915）。
    later += dispatch_add_cases() + blue_red_cases() + invoice_multi_cases() + dispatch_bdefine_cases() + dry_suite_cases() + taxno_cases()  # 写预演、名称解析、幂等结果查询、结构化错误、应付自动核销与制单（失败时 DRY_SUITE_CLEANUP 补偿）；数电号搜索、核销筛选与销售发票字段权限遮蔽
    # 存货核算记账与月末结账的真实写入：改整个账套的期间状态，独占、排在最后，各自还原（失败时尽力还原）。
    # 写入策略（write_policy_cases）：临时换本通道桥的策略文件（独占，退出时写回原文件），排在真写结账之前。
    # 权限核对（perm_cases）：evaluate / snapshot 与账套库独立算出的数据权限一致（只读，没配评估操作员记 N/A），
    # 排在写入策略之前，与它合一条语句（PLR0915）。
    # 字段权限遮蔽（col_auth_cases）：临时给测试操作员加字段权限行，核对置空与 masked_fields，结束删行（独占）。
    # 存货核算制单（ia_voucher_cases）：记账、制单、取消制单、恢复记账一整轮，同样独占、排在最后。
    later += perm_cases() + col_auth_cases() + write_policy_cases() + live_close_cases() + ia_voucher_cases()
    return first + arap_gen_cases() + fin_cases() + arc_cases() + later


def known_names() -> tuple[str, ...]:
    return tuple(spec.name for spec in build_cases({}))
