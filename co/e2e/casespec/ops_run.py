"""自定义步骤（Step.kind == "custom"，sql_name 是操作名）：只读检查、幂等键、带错误消息断言的拒绝调用、
生单前核对可退数量、比较 bag 里记下的回读值，以及不良品处理单入库的删除。"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from co.e2e.casespec.arap_ctl import ARAP_CTL_OPS
from co.e2e.casespec.assertutil import first_cell, status_problem
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.mo_gen_bad import MO_GEN_OPS
from co.e2e.casespec.gl_idem import IDEM_OPS
from co.e2e.casespec.readonly import READ_OPS
from co.e2e.casespec.arc_write_more import ARC_MORE_OPS
from co.e2e.casespec.arc_project_delete import PROJ_OPS
from co.e2e.casespec.report_detail import DETAIL_OPS
from co.e2e.casespec.report_stock import STOCK_RPT_OPS
from co.e2e.casespec.report_trace import TRACE_OPS
from co.e2e.casespec.arc_gl import GLARC_OPS
from co.e2e.casespec.srcless import SRCLESS_OPS
from co.e2e.casespec.sale_return_so import SALE_RETURN_SO_OPS
from co.e2e.casespec.report_openings import OPEN_RPT_OPS
from co.e2e.casespec.arc_partner import PARTNER_OPS
from co.e2e.casespec.stock_plus import STOCKPLUS_OPS
from co.e2e.casespec.gl_post import GLPOST_OPS
from co.e2e.casespec.writeoff_list import HX_LIST_OPS
from co.e2e.casespec.rpt_fin import RPT_FIN_OPS
from co.e2e.casespec.rpt_sa import RPT_SA_OPS
from co.e2e.casespec.writeoff_auto_ops import HXA_OPS
from co.e2e.casespec.preds import judge_pred
from co.e2e.casespec.qm_tpl import QM_TPL_OPS
from co.e2e.casespec.openings_pu import OPENINGS_OPS
from co.e2e.casespec.periods_close import PERIODS_OPS
from co.e2e.casespec.writeoff_prepay_fx import ARAP_MORE_OPS
from co.e2e.casespec.arap_voucher_merge import VOUCHER_MERGE_OPS
from co.e2e.casespec.arc_exch import EXCH_OPS
from co.e2e.casespec.arc_reason import REASON_OPS
from co.e2e.casespec.binned_sale_out import BINNED_SALE_OUT_OPS
from co.e2e.casespec.ia_post import IA_OPS
from co.e2e.casespec.ia_opening import IA_OPENING_OPS
from co.e2e.casespec.live_close import LIVE_CLOSE_OPS
from co.e2e.casespec.ia_voucher import IA_VOUCHER_OPS
from co.e2e.casespec.openings_arap import OPENINGS_ARAP_OPS
from co.e2e.casespec.openings_live import OPENINGS_LIVE_OPS
from co.e2e.casespec.qm_reject import QM_REJECT_OPS
from co.e2e.casespec.qm_reject_update import QM_REJECT_UPDATE_OPS
from co.e2e.casespec.purchase_in_qm_reject import PURCHASE_IN_QM_REJECT_OPS
from co.e2e.casespec.mo_subs_replace import MO_SUBS_REPLACE_OPS
from co.e2e.casespec.stock_opening import STOCK_OPENING_OPS
from co.e2e.casespec.arap_extra import ARAP_EXTRA_OPS
from co.e2e.casespec.arap_proc import ARAP_PROC_OPS
from co.e2e.casespec.arc_probe import ARC_PROBE_OPS
from co.e2e.casespec.flip import FLIP_OPS
from co.e2e.casespec.mo_probe import MO_PROBE_OPS
from co.e2e.casespec.notes import NOTES_OPS
from co.e2e.casespec.stock_binned import STOCK_BINNED_OPS
from co.e2e.casespec.operator_probe import UA_PROBE_OPS
from co.e2e.casespec.bad_debt import BAD_DEBT_OPS
from co.e2e.casespec.bom_qty import BOM_MORE_OPS
from co.e2e.casespec.notes_return import NOTES_MORE_OPS
from co.e2e.casespec.notes_split import SPLIT_REG_OPS
from co.e2e.casespec.stock_more import STOCK_MORE_OPS
from co.e2e.casespec.gl_controlled import GL_CONTROLLED_OPS
from co.e2e.casespec.ia_sa_fa import IA_SA_FA_OPS
from co.e2e.casespec.made_inv import MADE_OPS
from co.e2e.casespec.mo_alloc import MO_ALLOC_OPS
from co.e2e.casespec.position_adjust import POSITION_ADJUST_OPS
from co.e2e.casespec.pu_settle_read import PU_SETTLE_OPS
from co.e2e.casespec.pu_settle_write import PU_SETTLE_WRITE_OPS
from co.e2e.casespec.pu_arrival_in import PU_ARR_IN_OPS
from co.e2e.casespec.arrival_close import ARRIVAL_CLOSE_OPS
from co.e2e.casespec.pu_settle_manual import PU_SETTLE_MAN_OPS
from co.e2e.casespec.qm_binned_in import QM_BIN_OPS
from co.e2e.casespec.qm_merged_in import QM_MERGED_OPS
from co.e2e.casespec.qm_merge import QM_MERGE_OPS
from co.e2e.casespec.qm_other_read import QM_OTHER_OPS
from co.e2e.casespec.qm_other_write import QM_OTHER_WRITE_OPS
from co.e2e.casespec.qm_other_update import QMU_OTHER_OPS
from co.e2e.casespec.sale_out_trim import SALE_OUT_TRIM_OPS
from co.e2e.casespec.arap_refund import ARAP_REFUND_OPS
from co.e2e.casespec.exgain_replay import EXGAIN_REPLAY_OPS
from co.e2e.casespec.refund_voucher import REFUND_VOUCHER_OPS
from co.e2e.casespec.fa_eq import FA_EQ_OPS
from co.e2e.casespec.settle_replay import SETTLE_REPLAY_OPS
from co.e2e.casespec.return_apply import RETURN_APPLY_OPS
from co.e2e.casespec.mgmt_gl import MGMT_GL_OPS
from co.e2e.casespec.mgmt_sa import MGMT_SA_OPS
from co.e2e.casespec.sale_out_nosrc import SALE_OUT_NOSRC_OPS
from co.e2e.casespec.sale_out_nosrc_update import SALE_OUT_NOSRC_UPDATE_OPS
from co.e2e.casespec.sale_blue_red import BLUE_RED_OPS
from co.e2e.casespec.invoice_multi import INVOICE_MULTI_OPS
from co.e2e.casespec.dispatch_line_bdefine import DISPATCH_BDEFINE_OPS
from co.e2e.casespec.dispatch_add import DISPATCH_ADD_OPS
from co.e2e.casespec.gl_unpost import GL_UNPOST_OPS
from co.e2e.casespec.write_policy import WRITE_POLICY_OPS
from co.e2e.casespec.gl_reverse import GL_REVERSE_OPS
from co.e2e.casespec.gl_transfer import GL_TRANSFER_OPS
from co.e2e.casespec.col_auth import COL_AUTH_OPS
from co.e2e.casespec.perm_check import PERM_OPS
from co.e2e.casespec.qm_workflow import QM_WF_OPS
from co.e2e.casespec.taxno_search import TAXNO_OPS
from co.e2e.casespec.dry_suite import DRY_SUITE_OPS
from co.e2e.casespec.returns_misc_readback_sql import custom_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import _make, auth_fields, focus_id, invoke


def _send(env: Env, route: str, extra: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, route, fields)


def _made_id(payload: dict) -> int:
    raw = payload.get("id")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return raw
    return 0


def refuse(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    # 按构造器生成正文调用 step.route，要求状态码、错误码一致，且 message 含 fields 里的 text。
    args = dict(step.fields)
    if step.id_from != "none":
        focus_id(env, spec, step)
    body = _make(args["builder"])(env)
    status, payload = _send(env, step.route, body)
    bad = status_problem(status, payload, step.status, step.code)
    if bad:
        if status == 200 and _made_id(payload):
            # 本该被拒绝却建出了单据：先删掉再报失败。
            _send(env, "/v1/vouchers/delete", {"type": body["type"], "id": _made_id(payload)})
        return "FAIL", bad[:240]
    message = str(payload.get("message") or "")
    if args["text"] not in message:
        return "FAIL", "消息不含「" + args["text"] + "」：" + message[:160]
    return "PASS", f"{status} {payload.get('code')}"


def _cell(env: Env, spec: CaseSpec, name: str, doc: str) -> str:
    return first_cell(run_guarded(env, custom_sql(name, doc, env.bag), spec.name)).strip()


def room(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    # 生单前再核可退数量；挑单之后被别的用例占掉时记 SKIP（缺可用单据），不当成桥的失败。
    args = dict(step.fields)
    cell = _cell(env, spec, args["read"], focus_id(env, spec, step))
    try:
        left = Decimal(cell)
    except InvalidOperation:
        return "FAIL", "回读 " + (cell or "空")
    if left < Decimal(args["min"]):
        return "SKIP", "可退数量只剩 " + cell + "（挑单后被别的用例占用）"
    return "PASS", cell


def bag_compare(env: Env, _spec: CaseSpec, step: Step) -> tuple[str, str]:
    # 比较 bag 里先记下的回读值（例如生单后、删除前记的数），让删除先跑、断言后判。
    args = dict(step.fields)
    return judge_pred(env.bag.get(args["got"], ""), args["pred"], env.bag, env.operator_name)


def reject_drop(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    # 删除不良品处理单生成的产成品入库：200 时再核读取 404、处理单行已入库行数和标记回到 0；
    # 409 是文档写明的情形（U8 已自动关闭生产订单行），留给快照还原。
    made = env.made.get(spec.name, "")
    if not made.isdigit():
        return "FAIL", "没有新建的入库单"
    status, payload = _send(env, "/v1/vouchers/delete", {"type": "product_in", "id": int(made)})
    if status == 409:
        return "PASS", "409 " + str(payload.get("message") or "")[:120]
    if status != 200 or payload.get("deleted") is not True:
        return "FAIL", f"{status} {payload.get('code')} {payload.get('message')}"[:240]
    status, payload = _send(env, "/v1/vouchers/load", {"type": "product_in", "id": int(made)})
    if status != 404:
        return "FAIL", "删除后仍能读取"
    used = _cell(env, spec, "qmrej_used", made)
    flag = _cell(env, spec, "qmrej_flag", env.bag.get(spec.needs[0], ""))
    if used != "0" or flag != "0":
        return "FAIL", f"删除后入库行 {used or '空'}、已入库标记 {flag or '空'}，期望 0"
    return "PASS", "已删除并回退"


_OPS = {"refuse": refuse, "room": room, "bag_compare": bag_compare, "reject_drop": reject_drop}
_OPS.update(READ_OPS)
_OPS.update(IDEM_OPS)
# 档案写入（arc_write_more）。
_OPS.update(ARC_MORE_OPS)
_OPS.update(PROJ_OPS)
# 明细账报表（report_detail）。
_OPS.update(DETAIL_OPS)
_OPS.update(TRACE_OPS)
# 库存与销售支持报表（report_stock）。
_OPS.update(STOCK_RPT_OPS)
# 总账档案写入（arc_gl）。
_OPS.update(GLARC_OPS)
# 无来源新增、产成品入库参照生产订单（srcless）。
_OPS.update(SRCLESS_OPS)
# 退货单参照销售订单（sale_return_so）。
_OPS.update(SALE_RETURN_SO_OPS)
# 期初余额报表与附件列表（report_openings）。
_OPS.update(OPEN_RPT_OPS)
# 客户、供应商的银行账户和联系人（arc_partner）。
_OPS.update(PARTNER_OPS)
# 汇率写入（arc_exch）。
_OPS.update(EXCH_OPS)
# 原因码写入（arc_reason）。
_OPS.update(REASON_OPS)
# 销售出库批号 / 货位 / 拆行、外币采购入库、无来源红字采购入库（stock_plus）。
_OPS.update(STOCKPLUS_OPS)
# 总账记账（gl_post）。
_OPS.update(GLPOST_OPS)
# 总账记账后取消记账（gl_unpost，测试账套）。
_OPS.update(GL_UNPOST_OPS)
# 核销记录查询（writeoff_list，核销用例里的两步）。
_OPS.update(HX_LIST_OPS)
# 自动核销的试算一步（writeoff_auto_ops）。
_OPS.update(HXA_OPS)
# 写预演、名称解析、幂等结果查询等（dry_suite）。
_OPS.update(DRY_SUITE_OPS)
# 采购期初记账（openings_pu）。
_OPS.update(OPENINGS_OPS)
# 月末结账（periods_close）。
_OPS.update(PERIODS_OPS)
# 存货核算记账、期末处理（ia_post）。
_OPS.update(IA_OPS)
# 存货核算记账与月末结账的真实写入（live_close）。
_OPS.update(LIVE_CLOSE_OPS)
# 存货核算制单、取消制单的真实写入（ia_voucher）。
_OPS.update(IA_VOUCHER_OPS)
# 应收应付期初单据（openings_arap）。
_OPS.update(OPENINGS_ARAP_OPS)
# 库存期初结存单（stock_opening）。
_OPS.update(STOCK_OPENING_OPS)
# 已结账月份临时打开后的期初一整轮（openings_live）。
_OPS.update(OPENINGS_LIVE_OPS)
# 存货核算期初记账（ia_opening）。
_OPS.update(IA_OPENING_OPS)
# 有货位记录的销售出库删除（binned_sale_out）。
_OPS.update(BINNED_SALE_OUT_OPS)
# 应收合并制单（arap_voucher_merge）。
_OPS.update(VOUCHER_MERGE_OPS)
# 预收 / 预付行核销、外币核销（writeoff_prepay_fx）。
_OPS.update(ARAP_MORE_OPS)
# 不良品处理单参照检验单生单、审核、删除（qm_reject）。
_OPS.update(QM_REJECT_OPS)
_OPS.update(QM_REJECT_UPDATE_OPS)  # 不良品处理单修改（qm_reject_update）
_OPS.update(PURCHASE_IN_QM_REJECT_OPS)  # 采购入库参照来料不良品处理单（purchase_in_qm_reject）
_OPS.update(MO_SUBS_REPLACE_OPS)  # 生产订单修改替换替代料（mo_subs_replace）
# 档案写入附加核对（arc_probe）、操作员读取附加核对（operator_probe）、生产订单新增探查（mo_probe）。
_OPS.update(ARC_PROBE_OPS)
_OPS.update(UA_PROBE_OPS)
_OPS.update(MO_PROBE_OPS)
# 自动核销 include_prepay、制单来源列（arap_extra）。
_OPS.update(ARAP_EXTRA_OPS)
# 应收应付处理：转账、并账、红票对冲、汇兑损益（arap_proc）。
_OPS.update(ARAP_PROC_OPS)
# 应收票据：登记、结算、贴现、背书、分包结算（notes）。
_OPS.update(NOTES_OPS)
# 分包应收票据登记（notes_split）。
_OPS.update(SPLIT_REG_OPS)
# 应收票据退回、应付票据（notes_return）。
_OPS.update(NOTES_MORE_OPS)
# 应收坏账：发生、收回、计提（bad_debt）。
_OPS.update(BAD_DEBT_OPS)
# 物料清单用量精度与表头修改（bom_qty）。
_OPS.update(BOM_MORE_OPS)
# 库存补用例（stock_more）。
_OPS.update(STOCK_MORE_OPS)
# 有货位记录的采购入库、其他出库删除（stock_binned）。
_OPS.update(STOCK_BINNED_OPS)
# 参照来料检验单生成带货位的采购入库（qm_binned_in）。
_OPS.update(QM_BIN_OPS)
# 货位调整单往返（position_adjust）。
_OPS.update(POSITION_ADJUST_OPS)
# 参照合并检验的产品检验单生成产成品入库（qm_merged_in）。
_OPS.update(QM_MERGED_OPS)
# 产品检验单合并检验生成、删除（qm_merge）。
_OPS.update(QM_MERGE_OPS)
# 采购结算单只读（pu_settle_read）。
_OPS.update(PU_SETTLE_OPS)
# 采购结算单自动结算、删除（pu_settle_write）；手工结算（pu_settle_manual，第一级）。
_OPS.update(PU_SETTLE_WRITE_OPS)
_OPS.update(PU_SETTLE_MAN_OPS)
# 采购入库参照到货单（pu_arrival_in）。
_OPS.update(PU_ARR_IN_OPS)
# 到货单关闭、打开（arrival_close）。
_OPS.update(ARRIVAL_CLOSE_OPS)
# 其他报检单、其他检验单只读核对（qm_other_read）。
_OPS.update(QM_OTHER_OPS)
# 其他报检单新增、其他检验单生单 / 审核 / 删除（qm_other_write）。
_OPS.update(QM_OTHER_WRITE_OPS)
# 其他报检单、其他检验单修改（qm_other_update）。
_OPS.update(QMU_OTHER_OPS)
# 出入库调整单、存货调价单、固定资产报表只读核对（ia_sa_fa）。
_OPS.update(IA_SA_FA_OPS)
# 退货申请单只读核对与写路由拒绝（return_apply）。
_OPS.update(RETURN_APPLY_OPS)
# 经营管理报表（总账口径）只读核对（mgmt_gl）。
_OPS.update(MGMT_GL_OPS)
# 经营管理报表（销售分析、往来账期）只读核对（mgmt_sa）。
_OPS.update(MGMT_SA_OPS)
# 自建存货的保质期入库（made_inv）、生产订单子件固定用量 / 损耗 / 辅计量（mo_alloc）。
_OPS.update(MADE_OPS)
_OPS.update(MO_ALLOC_OPS)
# ATO 生产订单参照销售订单生成与拒绝路径（mo_gen_ato、mo_gen_bad）。
_OPS.update(MO_GEN_OPS)
# 销售出库按行部分生单（sale_out_trim）、总账受控科目选项（gl_controlled）。
_OPS.update(SALE_OUT_TRIM_OPS)
# 来源库存的销售出库单修改（sale_out_nosrc_update）。
_OPS.update(SALE_OUT_NOSRC_UPDATE_OPS)
# 无来源销售出库单新增与预演（sale_out_nosrc）。
_OPS.update(SALE_OUT_NOSRC_OPS)
_OPS.update(GL_CONTROLLED_OPS)
# 发货单修改新增行（dispatch_add）。
_OPS.update(DISPATCH_ADD_OPS)
# 红冲蓝字销售发票（sale_blue_red）。
_OPS.update(BLUE_RED_OPS)
# 一张销售发票参照多张发货单（invoice_multi）。
_OPS.update(INVOICE_MULTI_OPS)
# 发货单生单表体扩展自定义项 cbdefineN（dispatch_line_bdefine）。
_OPS.update(DISPATCH_BDEFINE_OPS)
# 写入策略：临时换本账套桥的策略文件，核对冻结、时段、白名单、操作员、行数、限额（write_policy）。
_OPS.update(WRITE_POLICY_OPS)
# 临时改选项或造数据、跑完原样恢复的一组（flip）。
_OPS.update(FLIP_OPS)
# 供应商退款、客户退款的往返和列表（arap_refund）。
_OPS.update(ARAP_REFUND_OPS)
# 退款单制单（refund_voucher）。
_OPS.update(REFUND_VOUCHER_OPS)
# 固定资产卡片与设备台账（fa_eq、fa_eq_more）。
_OPS.update(FA_EQ_OPS)
# 汇兑损益、采购手工结算的重做比对（exgain_replay、settle_replay）。
_OPS.update(EXGAIN_REPLAY_OPS)
_OPS.update(SETTLE_REPLAY_OPS)
# 总账红字冲销（gl_reverse）。
_OPS.update(GL_REVERSE_OPS)
# 期间损益结转、自定义转账的核对（gl_transfer）。
_OPS.update(GL_TRANSFER_OPS)
# 应收 / 应付单据用例的前置：账套没有受控科目时 N/A（arap_ctl）；质量单据模板必输的自定义项（qm_tpl）。
_OPS.update(ARAP_CTL_OPS)
_OPS.update(QM_TPL_OPS)
# 权限核对：perm/evaluate、perm/snapshot 与账套库独立算出的数据权限一致（perm_check）。
_OPS.update(PERM_OPS)
# 字段权限遮蔽：临时给测试操作员加字段权限行，读取置空、masked_fields，结束删行（col_auth）。
_OPS.update(COL_AUTH_OPS)
# 销售发票数电号搜索、核销筛选、字段权限拒绝路径（taxno_search）。
_OPS.update(TAXNO_OPS)
# 审批流用例的收尾步骤：还有待办时继续同意，没有时核对已通过（qm_workflow）。
_OPS.update(QM_WF_OPS)
_OPS.update(RPT_FIN_OPS)
_OPS.update(RPT_SA_OPS)


def step_custom(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    op = _OPS.get(step.sql_name)
    if op is None:
        return "FAIL", "未知步骤"
    try:
        return op(env, spec, step)
    except (KeyError, RuntimeError, ValueError) as exc:
        return "FAIL", str(exc)[:200]


def has_op(name: str) -> bool:
    return name in _OPS
