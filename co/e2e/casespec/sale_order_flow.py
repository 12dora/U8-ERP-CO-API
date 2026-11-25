"""销售订单修改、关闭，发货单生单，以及销售发票的生成和弃复。"""

from __future__ import annotations

from co.e2e.casespec.assertutil import EITHER
from co.e2e.casespec.model import (
    CaseSpec,
    Step,
    apply_step,
    call,
    call_from,
    call_made,
    keep_step,
    pred_step,
    stash_step,
)


def _sale_update() -> CaseSpec:
    made = (
        Step("create", sql_name="sale_order", checks=(("code", "$nonempty"),)),
        pred_step("so_sum", "gt0"),
        stash_step("so_line", "so_line"),
        stash_step("so_qty", "so_qty"),
        apply_step("/v1/vouchers/update", "sale_update", "made", (("lines", "$gt0"),)),
        pred_step("so_back", "saleupd:2"),
        stash_step("so_other", "so_extra"),
        apply_step("/v1/vouchers/update", "sale_drop"),
        pred_step("so_back", "saleupd:1"),
        call_made("/v1/vouchers/delete", (("type", "sale_order"), ("id", "$id")), (("deleted", "true"),)),
        call_made("/v1/vouchers/load", (("type", "sale_order"), ("id", "$id")), (), 404, "not_found"),
    )
    return CaseSpec("sale-update", "销售订单修改", ("sale_master",), made, True)


def _sale_close() -> CaseSpec:
    kind = (("type", "sale_order"), ("id", "$id"))
    steps = (
        call("/v1/vouchers/close", kind + (("action", "close"),), (("closed", "true"), ("action", "close"))),
        pred_step("so_closer", "nonempty", "need"),
        call("/v1/vouchers/close", kind + (("action", "open"),), (("closed", "false"), ("action", "open"))),
        pred_step("so_closer", "empty", "need"),
        stash_step("so_open_line", "sale_line", "need"),
        apply_step("/v1/vouchers/close", "sale_close_line", "need"),
        pred_step("so_line_state", "lineclosed", "need"),
        apply_step("/v1/vouchers/close", "sale_open_line", "need"),
        pred_step("so_line_state", "lineopen", "need"),
        Step("create", sql_name="sale_order"),
        apply_step("/v1/vouchers/close", "sale_close_made", "made", (), (409, "state_mismatch")),
        call_made("/v1/vouchers/delete", kind, (("deleted", "true"),)),
    )
    return CaseSpec("sale-close", "销售订单关闭与打开", ("sale_verified", "sale_master"), steps, True)


def _invoice_generate() -> CaseSpec:
    kind = (("type", "sale_invoice"), ("id", "$id"))
    on = kind + (("action", "verify"),)
    off = kind + (("action", "unverify"),)
    made = (("type", "sale_invoice"), ("code", "$nonempty"))
    steps = (
        stash_step("inv_settle", "inv_settle", "need"),
        stash_step("inv_qty", "inv_qty", "need"),
        apply_step("/v1/vouchers/generate", "invoice_gen", "need", made),
        keep_step("inv_id"),
        pred_step("inv_settle", "added:inv_settle+inv_qty", "need"),
        call_from("/v1/vouchers/delete", kind, "bag:inv_id", (("deleted", "true"),)),
        pred_step("inv_settle", "same:inv_settle", "need"),
        pred_step("inv_gone", "eq:0", "bag:inv_id"),
        # 复核再弃复后 U8 有时不回退发货单的已复核开票数量（在测试账套上实测两种情况都出现过）。
        # 有残留时桥拒绝删除（409，发票留给快照还原），没有残留时正常删除。
        apply_step("/v1/vouchers/generate", "invoice_gen", "need", made),
        keep_step("inv_id"),
        call_from("/v1/vouchers/verify", on, "bag:inv_id"),
        pred_step("inv_checker", "opname", "bag:inv_id"),
        call_from("/v1/vouchers/verify", off, "bag:inv_id"),
        pred_step("inv_checker", "empty", "bag:inv_id"),
        call_from("/v1/vouchers/delete", kind, "bag:inv_id", (("deleted", "true"),), (EITHER, "200|409:state_mismatch")),
    )
    needs = ("inv_src", "inv_src_line")
    return CaseSpec("invoice-generate", "发货单生成销售发票：删除回退累计开票数；复核弃复后按是否残留删除或拒绝", needs, steps, True)


def _invoice() -> CaseSpec:
    fields = (("type", "sale_invoice"), ("id", "$id"), ("action", "unverify"))
    step = call("/v1/vouchers/verify", fields, (), 409, "u8_rejected")
    return CaseSpec("sale-invoice-unverify-reject", "应收已审发票不能弃复", ("sale_invoice_ar",), (step,), True)


def _update_verified() -> CaseSpec:
    step = apply_step("/v1/vouchers/update", "sale_touch", "need", (), (409, "state_mismatch"))
    return CaseSpec("update-verified", "修改已审核销售订单", ("sale_verified",), (step,), True, tags=frozenset(("w:sale_order",)))


def _dispatch() -> CaseSpec:
    verify = (("type", "dispatch"), ("id", "$id"), ("action", "verify"))
    unver = (("type", "dispatch"), ("id", "$id"), ("action", "unverify"))
    steps = (
        stash_step("so_fh", "so_fh", "need"),
        apply_step("/v1/vouchers/generate", "dispatch_gen", "need", (("type", "dispatch"), ("code", "$nonempty"))),
        keep_step("disp_id"),
        pred_step("so_fh", "plus1:so_fh", "need"),
        call_from("/v1/vouchers/verify", verify, "bag:disp_id"),
        apply_step("/v1/vouchers/generate", "sale_out_gen", "bag:disp_id", (("type", "sale_out"),)),
        keep_step("out_id"),
        pred_step("out_rows", "eq:1", "bag:disp_id"),
        call_from("/v1/vouchers/delete", (("type", "sale_out"), ("id", "$id")), "bag:out_id", (("deleted", "true"),)),
        call_from("/v1/vouchers/verify", unver, "bag:disp_id"),
        call_from("/v1/vouchers/delete", (("type", "dispatch"), ("id", "$id")), "bag:disp_id", (("deleted", "true"),)),
        pred_step("so_fh", "same:so_fh", "need"),
    )
    needs = ("so_ship", "so_ship_line", "so_ship_wh")
    return CaseSpec("dispatch-generate", "参照发货并生成销售出库", needs, steps, True)


def sale_cases() -> list[CaseSpec]:
    return [_sale_update(), _sale_close(), _dispatch(), _invoice_generate(), _invoice(), _update_verified()]
