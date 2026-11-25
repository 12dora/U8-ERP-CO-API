"""应付一侧的自动核销、应付制单。应付手工核销与取消已由 ap-writeoff（writeoff）覆盖。

ap-writeoff-auto：挑一个供应商、它的一行有余额的本币付款单和一张本币采购发票（挑单 dry_sql.DRY_AP_PACKS），限定这一行和这张发票：
  先试算（mode=plan、计划非空，余额不变）→ 上限 1.00 执行，恰好一批，付款单行和发票行余额各少 1、发票行多一条核销行
  → 按该批核销号取消 → 复原（用例自己清理）。执行一步先把各批核销号记进 dry_ap_cno / dry_ap_cno_more，之后失败时
  dry_cleanup 逐批取消（404 视为已取消）；补偿也失败时留给快照还原。同 ar-writeoff-auto 的结构。
ap-voucher：新建应付单 → 修改、审核预演 → 审核 → 制单预演（validate、detail.voucher）→ 制单（pz_id 以 AP 开头，记进 dry_apb_pz）→ 再制单 409
  → 取消制单预演（rollback）→ 再制单仍 409（预演没有删掉凭证）→ 取消制单 → 再取消 404 → 弃审 → 删除预演 → 删除 → 读取 404。
  中途失败时 dry_cleanup 尽力取消制单、弃审、删除应付单。同 ar-voucher 的结构，单据换成本用例自建的应付单。
"""

from __future__ import annotations

from co.e2e.casespec.arap_ctl import ctl_step
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_from, keep_step, stash_step
from co.e2e.casespec.dry_bodies import PZ_KEY
from co.e2e.casespec.dry_more import VOUCHER, balance_steps, bill_steps
from co.e2e.casespec.dry_sql import AP_AUTO_KEYS
from co.e2e.casespec.dry_steps import DELETE, LOAD, VERIFY, dry, dry_doc, ref

AUTO = "/v1/arap/writeoff/auto"
_CANCEL = "/v1/arap/writeoff/cancel"
DROP = "/v1/arap/voucher/delete"
_CONFLICT = (409, "state_mismatch")
_GONE = (("deleted", "true"),)


def _auto_case() -> CaseSpec:
    keys, lines = ("dry_ap_r0", "dry_ap_i0", "dry_ap_n0"), ("dry_ap_pcl", "dry_ap_pinvl")
    same, moved = ("same:",) * 3, ("shift:-1:", "shift:-1:", "plus1:")
    steps = (
        stash_step("hx_remain", keys[0], "bag:" + lines[0]),
        stash_step("hx_aprbal", keys[1], "bag:" + lines[1]),
        stash_step("hx_aprows", keys[2], "bag:" + lines[1]),
        Step("custom", AUTO, (("builder", "dry_ap_plan"),), sql_name="dry_plan", id_from="none"),
    ) + balance_steps(keys, lines, same, "AP")
    run = (("ok", "true"), ("flag", "AP"), ("batches#len", "1"))
    fields = (("builder", "dry_ap_run"), ("side", "AP"), ("key", "dry_ap_cno"))
    steps += (Step("custom", AUTO, fields, checks=run, sql_name="dry_wo_exec", id_from="none"),)
    steps += balance_steps(keys, lines, moved, "AP")
    cancelled = (("ok", "true"), ("cancel_no", "$prefix:HXAP"), ("flag", "AP"))
    steps += (apply_step(_CANCEL, "dry_ap_cancel", "none", cancelled), keep_step("dry_ap_cdone", "bag:" + lines[1]))
    steps += balance_steps(keys, lines, same, "AP")
    title = (
        "应付自动核销：限定一行付款单和一张采购发票，先试算（plan、不写），再上限 1.00 执行恰好一批、两边余额各少 1；"
        "按该批核销号取消后复原"
    )
    tags = frozenset(("w:ap_payment", "w:purchase_invoice"))
    return CaseSpec("ap-writeoff-auto", title, AP_AUTO_KEYS, steps, True, optional=True, tags=tags, routes=(AUTO, _CANCEL))


def _voucher_case() -> CaseSpec:
    key = "dry_apb"
    src = "bag:" + key
    # making_system 为 AP：以应付登录、按 AP 导入直接成功，没有走 GL 兜底。
    made = (("ok", "true"), ("pz_id", "$prefix:AP"), ("flag", "AP"), ("type", "ap_bill"), ("making_system", "AP"))
    fields = (("builder", "dry_apb_voucher"), ("side", "AP"), ("key", PZ_KEY))
    steps = (
        ctl_step("AP"),
        Step("create", sql_name="edit_ap_bill_new", checks=(("type", "ap_bill"), ("code", "$nonempty"))),
        keep_step(key),
    ) + bill_steps("ap_bill", key) + (
        call_from(VERIFY, ref("ap_bill", ("action", "verify")), src),
        dry(VOUCHER, (), builder="dry_apb_voucher", mode="validate", detail="voucher"),
        Step("custom", VOUCHER, fields, checks=made, sql_name="dry_pz_make", id_from="none"),
        # 凭证来源列：coutsysname 为 AP、应付单的 coutsign 为 AR（多数账套如此，见 arap_extra.pz_src）。
        Step("custom", fields=(("side", "AP"), ("key", PZ_KEY), ("sign", "AR")), sql_name="pz_src", id_from="none"),
        apply_step(VOUCHER, "dry_apb_voucher", "none", (), _CONFLICT),
        dry(DROP, (), builder="dry_apb_drop"),
        apply_step(VOUCHER, "dry_apb_voucher", "none", (), _CONFLICT),
        apply_step(DROP, "dry_apb_drop", "none", (("ok", "true"), ("deleted", "true"), ("pz_id", "$prefix:AP"))),
        keep_step("dry_apb_pzdone", src),
        apply_step(DROP, "dry_apb_drop", "none", (), (404, "not_found")),
        call_from(VERIFY, ref("ap_bill", ("action", "unverify")), src),
        dry_doc(DELETE, "ap_bill", src),
        call_from(DELETE, ref("ap_bill"), src, _GONE),
        call_from(LOAD, ref("ap_bill"), src, (), (404, "not_found")),
    )
    title = (
        "应付制单：应付单审核后制单预演（只算计划）、制单、重复制单 409；取消制单预演（回滚，凭证仍在）；"
        "取消制单、再取消 404；弃审、删除"
    )
    # 新建凭证占总账「转」字编号（同 ar-voucher），另带 gl。
    tags = frozenset(("w:ap_bill", "gl", "new:gl:转"))
    return CaseSpec("ap-voucher", title, ("ap_master",), steps, True, tags=tags, routes=(VOUCHER, DROP, VERIFY, DELETE))


def ap_cases() -> list[CaseSpec]:
    return [_auto_case(), _voucher_case()]
