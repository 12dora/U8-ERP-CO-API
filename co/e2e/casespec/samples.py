"""供单元测试收集全部挑单和改派 SQL。"""

from __future__ import annotations

from co.e2e.casespec.mo_gen_ato_sql import mo_gen_samples
from co.e2e.casespec.wiring import edit_samples
from co.e2e.casespec.stock_plus_sql import stockplus_samples
from co.e2e.casespec.gl_post_sql import glpost_samples
from co.e2e.casespec.gl_unpost_sql import gl_unpost_samples
from co.e2e.casespec.gl_transfer_sql import gl_transfer_samples
from co.e2e.casespec.mgmt_gl_sql import mgmt_gl_samples
from co.e2e.casespec.mgmt_sa_sql import mgmt_sa_samples
from co.e2e.casespec.col_auth_sql import col_auth_samples
from co.e2e.casespec.perm_check_sql import perm_samples
from co.e2e.casespec.openings_pu_sql import openings_samples
from co.e2e.casespec.periods_close_sql import periods_samples
from co.e2e.casespec.arap_voucher_merge_sql import voucher_merge_samples
from co.e2e.casespec.writeoff_prepay_fx_sql import arap_more_samples
from co.e2e.casespec.binned_sale_out_sql import binned_sale_out_samples
from co.e2e.casespec.ia_opening_sql import ia_opening_samples
from co.e2e.casespec.ia_post_sql import ia_samples
from co.e2e.casespec.live_close_sql import live_close_samples
from co.e2e.casespec.ia_voucher_sql import ia_voucher_samples
from co.e2e.casespec.openings_arap_sql import openings_arap_samples
from co.e2e.casespec.openings_live_sql import openings_live_samples
from co.e2e.casespec.qm_reject_sql import qm_reject_samples
from co.e2e.casespec.qm_reject_update_sql import qm_reject_update_samples
from co.e2e.casespec.mo_subs_replace_sql import mo_subs_replace_samples
from co.e2e.casespec.stock_opening_sql import stock_opening_samples
from co.e2e.casespec.arap_proc_more_sql import arap_proc_more_samples
from co.e2e.casespec.arap_proc_sql import arap_proc_samples
from co.e2e.casespec.arap_extra_sql import arap_extra_samples
from co.e2e.casespec.arc_probe_sql import arc_probe_samples
from co.e2e.casespec.flip import flip_samples
from co.e2e.casespec.mo_probe_sql import mo_probe_samples
from co.e2e.casespec.notes_sql import notes_samples
from co.e2e.casespec.stock_binned_sql import stock_binned_samples
from co.e2e.casespec.bad_debt_sql import bad_debt_samples
from co.e2e.casespec.bom_qty_sql import bom_more_samples
from co.e2e.casespec.notes_return_sql import notes_more_samples
from co.e2e.casespec.notes_split_sql import split_reg_samples
from co.e2e.casespec.stock_more_sql import stock_more_samples
from co.e2e.casespec.gl_controlled_sql import gl_controlled_samples
from co.e2e.casespec.made_inv_sql import made_samples
from co.e2e.casespec.position_adjust_sql import position_adjust_samples
from co.e2e.casespec.pu_settle_read_sql import pu_settle_samples
from co.e2e.casespec.pu_settle_write_sql import pu_settle_write_samples
from co.e2e.casespec.pu_arrival_in_sql import pu_arr_in_samples
from co.e2e.casespec.arrival_close_sql import arrival_close_samples
from co.e2e.casespec.pu_settle_manual_sql import pu_settle_man_samples
from co.e2e.casespec.qm_binned_in_sql import qm_bin_samples
from co.e2e.casespec.qm_merged_in_sql import qm_merged_samples
from co.e2e.casespec.qm_merge_sql import qm_merge_samples
from co.e2e.casespec.qm_other_write_sql import qm_other_write_samples
from co.e2e.casespec.sale_out_trim_sql import sale_out_trim_samples
from co.e2e.casespec.sale_blue_red_sql import blue_red_samples
from co.e2e.casespec.dispatch_add_sql import dispatch_add_samples
from co.e2e.casespec.invoice_multi_sql import invoice_multi_samples
from co.e2e.casespec.dispatch_line_bdefine_sql import dispatch_bdefine_samples
from co.e2e.casespec.return_apply_cycle_sql import return_apply_cycle_samples
from co.e2e.casespec.arap_refund_sql import arap_refund_samples
from co.e2e.casespec.exgain_replay_sql import exgain_replay_samples
from co.e2e.casespec.refund_voucher_sql import refund_voucher_samples
from co.e2e.casespec.replay_prep_sql import replay_prep_samples
from co.e2e.casespec.fa_eq_sql import fa_eq_samples
from co.e2e.casespec.settle_replay_sql import settle_replay_samples
from co.e2e.casespec.sale_out_nosrc_sql import sale_out_nosrc_samples
from co.e2e.casespec.sale_out_nosrc_update_sql import sale_out_nosrc_update_samples
from co.e2e.casespec.sql_load import LOAD_SOURCES, newest_sql
from co.e2e.casespec.arap_gen_sql import core_samples
from co.e2e.casespec.returns_misc_sql import custom_samples
from co.e2e.casespec.flow_pick_sql import pick_samples
from co.e2e.casespec.flow_read_sql import read_samples
from co.e2e.casespec.sql_qm import qm04_sql, qm_state_sql
from co.e2e.casespec.sql_sa import (
    dispatch_down_sql,
    dispatch_open_sql,
    dispatch_verified_sql,
    sale_master_sql,
    sale_open_sql,
)
from co.e2e.casespec.sql_shape import code_sql, pending_qm04, read_sql, running_qm04, shape_qm04
from co.e2e.casespec.sql_st import (
    arrival_open_sql,
    other_in_master_sql,
    other_out_master_sql,
    other_out_open_sql,
    po_open_sql,
    voucher_prefix_sql,
)


def sample_sql() -> list[tuple[str, str]]:
    rows = [
        ("sale", sale_open_sql()),
        ("dispatch-open", dispatch_open_sql()),
        ("dispatch-verified", dispatch_verified_sql()),
        ("dispatch-down", dispatch_down_sql()),
        ("sale-master", sale_master_sql()),
        ("qm04", qm04_sql()),
        ("qm03", qm_state_sql("QM03")),
        ("qm06", qm_state_sql("QM06")),
        ("other-out-open", other_out_open_sql()),
        ("po", po_open_sql()),
        ("arrival", arrival_open_sql()),
        ("other-in-master", other_in_master_sql()),
        ("other-out-master", other_out_master_sql()),
        ("prefix", voucher_prefix_sql()),
        ("shape", shape_qm04("6117", "OP-001")),
        ("pending", pending_qm04("6117")),
        ("running", running_qm04("6117")),
        ("read-sale", read_sql("sale", "100")),
        ("code-rd08", code_sql("rd08", "100")),
        ("code-po", code_sql("po", "100")),
    ]
    for key, table, col, where in LOAD_SOURCES:
        rows.append((key, newest_sql(table, col, where)))
    bag = {
        "so_line": "100",
        "sale_line": "100",
        "so_ship_line": "100",
        "po_one": "100",
        "po_recv_line": "100",
        "oi_line": "100",
        "inv_src_line": "100",
    }
    rows.extend(pick_samples())
    rows.extend(read_samples(bag))
    rows.extend(_round_samples())
    return rows


def _round_samples() -> list[tuple[str, str]]:
    """后加用例的样例 SQL。"""
    rows: list[tuple[str, str]] = []
    rows.extend(core_samples())
    rows.extend(custom_samples())
    rows.extend(edit_samples())
    rows.extend(stockplus_samples())  # stock_plus
    rows.extend(glpost_samples() + gl_unpost_samples() + gl_transfer_samples())  # gl_post；gl_unpost；gl_transfer
    rows.extend(openings_samples())  # openings_pu
    rows.extend(periods_samples())  # periods_close
    rows.extend(ia_samples())  # ia_post
    rows.extend(live_close_samples() + ia_voucher_samples())  # live_close；存货核算制单 ia_voucher
    rows.extend(openings_arap_samples())  # openings_arap
    rows.extend(stock_opening_samples())  # stock_opening
    rows.extend(openings_live_samples())  # openings_live
    rows.extend(ia_opening_samples())  # ia_opening
    rows.extend(binned_sale_out_samples())  # B4 binned_sale_out
    rows.extend(voucher_merge_samples())  # arap_voucher_merge
    rows.extend(arap_more_samples())  # writeoff_prepay_fx
    # qm_reject、不良品处理单修改 qm_reject_update、生产订单修改替换替代料 mo_subs_replace
    rows.extend(qm_reject_samples() + qm_reject_update_samples() + mo_subs_replace_samples())
    rows.extend(arap_extra_samples())  # arap_extra
    rows.extend(arap_proc_samples() + arap_proc_more_samples())  # arap_proc / arap_proc_more
    rows.extend(notes_samples())  # notes / notes_more
    rows.extend(split_reg_samples())  # notes_split
    rows.extend(notes_more_samples())  # notes_return
    rows.extend(bad_debt_samples())  # bad_debt
    rows.extend(bom_more_samples())  # bom_qty
    rows.extend(stock_more_samples())  # stock_more
    rows.extend(stock_binned_samples())  # stock_binned
    rows.extend(qm_bin_samples())  # qm_binned_in
    rows.extend(position_adjust_samples())  # position_adjust
    rows.extend(qm_merged_samples() + qm_merge_samples())  # qm_merged_in；产品检验单合并检验 qm_merge
    rows.extend(qm_other_write_samples())  # qm_other_write
    rows.extend(pu_settle_samples())  # pu_settle_read
    rows.extend(pu_settle_write_samples())  # pu_settle_write
    rows.extend(pu_settle_man_samples() + settle_replay_samples() + exgain_replay_samples() + replay_prep_samples())  # pu_settle_manual；重做比对
    rows.extend(pu_arr_in_samples())  # pu_arrival_in
    rows.extend(arrival_close_samples())  # arrival_close
    rows.extend(made_samples())  # made_inv / mo_alloc
    rows.extend(sale_out_trim_samples())  # sale_out_trim
    rows.extend(dispatch_add_samples() + blue_red_samples() + invoice_multi_samples() + dispatch_bdefine_samples())  # dispatch_add / sale_blue_red；invoice_multi；dispatch_line_bdefine
    rows.extend(sale_out_nosrc_update_samples())  # sale_out_nosrc_update
    rows.extend(sale_out_nosrc_samples())  # sale_out_nosrc
    rows.extend(arap_refund_samples() + refund_voucher_samples() + fa_eq_samples())  # arap_refund；refund_voucher、fa_eq
    rows.extend(return_apply_cycle_samples())  # return_apply_cycle
    rows.extend(mgmt_gl_samples())  # mgmt_gl
    rows.extend(mgmt_sa_samples() + perm_samples() + col_auth_samples())  # mgmt_sa；perm_check、col_auth（合一条语句，PLR0915）
    rows.extend(gl_controlled_samples())  # gl_controlled
    rows.extend(arc_probe_samples())  # arc_probe / operator_probe
    rows.extend(mo_probe_samples() + mo_gen_samples())  # mo_probe；ATO 生产订单参照销售订单生成（mo_gen_ato）
    rows.extend(flip_samples())  # flip（openings_pu_unpost、exch_floating、credit_flip、price_list、so_lock_other）
    return rows
