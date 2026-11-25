"""调拨单和其他入库的修改，以及两条不改库的拒绝。"""

from __future__ import annotations

from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_made, pred_step, stash_step


def _transfer() -> CaseSpec:
    kind = (("type", "transfer"), ("id", "$id"))
    on = kind + (("action", "verify"),)
    off = kind + (("action", "unverify"),)
    made = (("generated#types", "other_out,other_in"),)
    steps = (
        Step("create", sql_name="transfer", checks=(("code", "$nonempty"),)),
        stash_step("tv_line", "tv_line"),
        apply_step("/v1/vouchers/update", "transfer_qty"),
        pred_step("tv_rows", "eq:2"),
        call_made("/v1/vouchers/verify", on, made),
        pred_step("tv_kids", "eq:2"),
        call_made("/v1/vouchers/verify", off),
        pred_step("tv_kids", "eq:0"),
        call_made("/v1/vouchers/delete", kind, (("deleted", "true"),)),
    )
    data = ("stock.transfer_out_rd", "stock.transfer_in_rd")
    return CaseSpec("transfer-cycle", "调拨单新建修改审核删除", ("transfer_master",), steps, True, data=data)


def _other() -> CaseSpec:
    steps = (
        Step("create", sql_name="other_in"),
        stash_step("oi_line", "oi_line"),
        stash_step("oi_qty", "oi_qty"),
        apply_step("/v1/vouchers/update", "other_in_edit"),
        pred_step("oi_price", "eq:1"),
        call_made("/v1/vouchers/delete", (("type", "other_in"), ("id", "$id")), (("deleted", "true"),)),
    )
    return CaseSpec("other-in-update", "其他入库修改金额", ("other_in_master",), steps, True)


def _reject() -> list[CaseSpec]:
    closed = apply_step("/v1/vouchers/close", "close_other", "none", (), (400, "bad_request"))
    return [
        CaseSpec("close-other-in", "其他入库不能关闭", (), (closed,), True, tags=frozenset(("probe", "w:other_in"))),
    ]


def stock_cases() -> list[CaseSpec]:
    return [_transfer(), _other()] + _reject()
