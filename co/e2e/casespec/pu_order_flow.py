"""采购订单的新建、修改、关闭，以及参照生成采购入库。"""

from __future__ import annotations

from co.e2e.casespec.model import (
    CaseSpec,
    Step,
    apply_step,
    call_made,
    pred_step,
    read_step,
    stash_step,
)


def _po_cycle() -> CaseSpec:
    kind = (("type", "purchase_order"), ("id", "$id"))
    on = kind + (("action", "verify"),)
    off = kind + (("action", "unverify"),)
    steps = (
        Step("create", sql_name="purchase_order", checks=(("code", "$prefix:PO"),)),
        Step("readcode", sql_name="po", id_from="made"),
        pred_step("po_sum", "gt0"),
        stash_step("po_sum", "po_isum"),
        stash_step("po_line", "po_line"),
        apply_step("/v1/vouchers/update", "po_qty"),
        pred_step("po_sum", "double:po_isum"),
        call_made("/v1/vouchers/verify", on),
        read_step("po", "verified", "made"),
        call_made("/v1/vouchers/close", kind + (("action", "close"),), (("closed", "true"), ("action", "close"))),
        pred_step("po_closer", "nonempty"),
        call_made("/v1/vouchers/close", kind + (("action", "open"),), (("closed", "false"), ("action", "open"))),
        pred_step("po_closer", "empty"),
        call_made("/v1/vouchers/verify", off),
        read_step("po", "cleared", "made"),
        call_made("/v1/vouchers/delete", kind, (("deleted", "true"),)),
        call_made("/v1/vouchers/load", kind, (), 404, "not_found"),
    )
    return CaseSpec("po-cycle", "采购订单新建到删除", ("po_master",), steps, True)


def _po_lines() -> CaseSpec:
    steps = (
        stash_step("po_open_line", "po_one", "need"),
        apply_step("/v1/vouchers/close", "po_close_line", "need", (("closed", "false"),)),
        pred_step("po_line_state", "lineclosed", "need"),
        apply_step("/v1/vouchers/close", "po_open_line", "need", (("action", "open"),)),
        pred_step("po_line_state", "lineopen", "need"),
    )
    return CaseSpec("po-close-lines", "采购订单按行关闭", ("po_lines",), steps, True, tags=frozenset(("w:purchase_order",)))


def _pin() -> CaseSpec:
    kind = (("type", "purchase_in"), ("id", "$id"))
    on = kind + (("action", "verify"),)
    off = kind + (("action", "unverify"),)
    steps = (
        stash_step("po_got", "po_got", "need"),
        apply_step("/v1/vouchers/generate", "pin_gen", "need", (("type", "purchase_in"), ("code", "$nonempty"))),
        pred_step("po_got", "plus1:po_got", "need"),
        call_made("/v1/vouchers/verify", on),
        call_made("/v1/vouchers/verify", off),
        call_made("/v1/vouchers/delete", kind, (("deleted", "true"),)),
        pred_step("po_got", "same:po_got", "need"),
    )
    needs = ("po_recv", "po_recv_line", "po_recv_wh")
    return CaseSpec("purchase-in-generate", "参照采购订单生成入库", needs, steps, True)


def pu_cases() -> list[CaseSpec]:
    return [_po_cycle(), _po_lines(), _pin()]
