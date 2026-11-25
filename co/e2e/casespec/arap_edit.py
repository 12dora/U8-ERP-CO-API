"""应收应付修改用例：收款单、付款单、应收单、应付单的修改。

每种类型：新建（金额 10）→ 改金额为 7、改摘要 → 回读表头合计、未核销、表体合计与行数、摘要、修改人
→ 往来明细仍为 0 → 删除并确认不在。新建的单据都在本用例里删掉；快照还原只是兜底。
"""

from __future__ import annotations

from co.e2e.casespec.arap_ctl import ctl_step_for
from co.e2e.casespec.arap_edit_bodies import KINDS, UPD_TEXT, line_key
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_made, pred_step, stash_step

_UPDATE = "/v1/vouchers/update"
_NEED = {
    "ar_receipt": ("ar_master", "收款单"),
    "ap_payment": ("ap_master", "付款单"),
    "ar_bill": ("ar_master", "应收单"),
    "ap_bill": ("ap_master", "应付单"),
}


def _ref(kind: str) -> tuple:
    return (("type", kind), ("id", "$id"))


def _readback(kind: str) -> tuple[Step, ...]:
    # 表头金额用新建用例的回读（<类型>_amt），往来明细用 <类型>_det。
    return (
        pred_step(kind + "_amt", "eq:" + UPD_TEXT),
        pred_step(kind + "_edit_ramt", "eq:" + UPD_TEXT),
        pred_step(kind + "_edit_sum", "eq:" + UPD_TEXT),
        pred_step(kind + "_edit_cnt", "eq:1"),
        pred_step(kind + "_edit_dig", "eq:1"),
        pred_step(kind + "_edit_mod", "nonempty"),
        pred_step(kind + "_det", "eq:0"),
    )


def _edit(kind: str) -> CaseSpec:
    need, title = _NEED[kind]
    ref = _ref(kind)
    steps = (
        ctl_step_for(need),
        Step("create", sql_name="edit_" + kind + "_new", checks=(("type", kind), ("code", "$nonempty"))),
        pred_step(kind + "_amt", "eq:10.00"),
        stash_step(line_key(kind), line_key(kind)),
        apply_step(_UPDATE, "edit_" + kind + "_upd", "made", (("ok", "true"), ("type", kind), ("lines", "1"))),
    )
    steps += _readback(kind)
    steps += (
        call_made("/v1/vouchers/delete", ref, (("deleted", "true"),)),
        call_made("/v1/vouchers/load", ref, (), 404, "not_found"),
        pred_step(kind + "_gone", "eq:0"),
    )
    name = kind.replace("_", "-") + "-edit"
    return CaseSpec(name, title + "新建、改金额和摘要、回读、删除", (need,), steps, True)


def arap_edit_cases() -> tuple:
    return tuple(_edit(kind) for kind in KINDS)
