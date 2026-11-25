"""采购发票应付审核、销售发票应收审核及弃审（vouchers/verify 的 arap_verify / arap_unverify）。

ap-invoice-audit：已采购复核、没有应付明细的采购发票 → 应付审核（cPBVVerifier = 本操作员姓名，每个发票行一条 Ap_Detail 登记行）
  → 再审核 409 → 弃审（审核人清空，登记行删掉）。
ap-invoice-audit-audited：已应付审核的发票再审核 409 state_mismatch（桥在调用 U8 之前拒绝）。
ap-invoice-audit-unreviewed：未采购复核的发票应付审核 409 state_mismatch（本账套没有这类发票时记 N/A）。
ar-invoice-audit：自己挑的发货行（arv_inv，避开发票生单、发票改单挑中的发货单）生成 1 个数量的销售发票
  → 复核 → 应收审核（cVerifier = 本操作员姓名，有 Ar_Detail 登记行）→ 弃审 → 弃复 → 删除（同 invoice-generate，
  删除可能因已复核开票数量残留而 409，发票留给快照还原）。不依赖别的用例写进 bag 的键，单跑、并行都能跑。
  U8 拒绝弃复应收已审的发票，所以弃审排在弃复之前；中途失败时 ARV_CLEANUP 尽力弃审、弃复。
ar-invoice-audit-bad-type：销售订单送 arap_verify，登录前 400。

挑单与回读 SQL 在 arap_verify_sql（接线在 wiring 汇总模块）。
"""

from __future__ import annotations

import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.assertutil import EITHER
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, apply_step, call, call_from, keep_step, pred_step, stash_step

_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_CONFLICT = (409, "state_mismatch")
_DONE = (("action", "arap_verify"), ("verified_by", "$opname"), ("state.arap_verified", "true"))
_UNDONE = (("action", "arap_unverify"), ("verified_by", ""), ("state.arap_verified", "false"))


def _ask(kind: str, action: str) -> tuple:
    return (("type", kind), ("id", "$id"), ("action", action))


def _pu_case() -> CaseSpec:
    src = "bag:arv_pu"
    steps = (
        # U8 应付审核按发票行各写一条登记行：先记下表体行数。
        stash_step("aud_pu_lines", "aud_pu_lines", src),
        # 没有表体行的发票会让「登记行数 = 表体行数」在 0 = 0 上空过：行数须大于 0，否则在审核前 FAIL。
        pred_step("aud_pu_lines", "gt0", src),
        call_from(_VERIFY, _ask("purchase_invoice", "arap_verify"), src, _DONE + (("state.gl_voucher", "false"),)),
        pred_step("aud_pu_auditor", "opname", src),
        pred_step("aud_pu_rows", "same:aud_pu_lines", src),
        call_from(_VERIFY, _ask("purchase_invoice", "arap_verify"), src, (), _CONFLICT),
        call_from(_VERIFY, _ask("purchase_invoice", "arap_unverify"), src, _UNDONE),
        pred_step("aud_pu_auditor", "empty", src),
        pred_step("aud_pu_rows", "eq:0", src),
    )
    title = "采购发票应付审核与弃审：审核人是本操作员、每个发票行一条应付登记行，弃审后都清掉；重复审核 409"
    return CaseSpec("ap-invoice-audit", title, ("arv_pu",), steps, True, optional=True, routes=(_VERIFY,))


def _refused(name: str, need: str, title: str) -> CaseSpec:
    step = call_from(_VERIFY, _ask("purchase_invoice", "arap_verify"), "bag:" + need, (), _CONFLICT)
    return CaseSpec(name, title, (need,), (step,), True, optional=True, routes=(_VERIFY,))


def _ar_case() -> CaseSpec:
    kind = (("type", "sale_invoice"), ("id", "$id"))
    src = "bag:aud_inv"
    steps = (
        apply_step("/v1/vouchers/generate", "arv_inv_gen", "need", (("type", "sale_invoice"), ("code", "$nonempty"))),
        keep_step("aud_inv"),
        call_from(_VERIFY, kind + (("action", "verify"),), src),
        call_from(_VERIFY, kind + (("action", "arap_verify"),), src, _DONE),
        pred_step("aud_sa_auditor", "opname", src),
        pred_step("aud_sa_rows", "gt0", src),
        call_from(_VERIFY, kind + (("action", "arap_verify"),), src, (), _CONFLICT),
        call_from(_VERIFY, kind + (("action", "arap_unverify"),), src, _UNDONE),
        pred_step("aud_sa_auditor", "empty", src),
        pred_step("aud_sa_rows", "eq:0", src),
        call_from(_VERIFY, kind + (("action", "unverify"),), src),
        call_from(_DELETE, kind, src, (("deleted", "true"),), (EITHER, "200|409:state_mismatch")),
    )
    title = "销售发票应收审核与弃审：生成、复核、应收审核、弃审、弃复、删除（有残留时删除 409）"
    # 发货单是生单回写的来源：挑单键不在 tags_data 的登记表里，手写 src:，挑单后换成发货单 id，和同一张发货单上的写互斥。
    needs = ("arv_inv", "arv_inv_line")
    tags = frozenset(("src:dispatch:arv_inv",))
    return CaseSpec("ar-invoice-audit", title, needs, steps, True, routes=(_VERIFY,), tags=tags)


def _bad_type_case() -> CaseSpec:
    step = call(_VERIFY, (("type", "sale_order"), ("id", "1"), ("action", "arap_verify")), (), 400, "bad_request")
    return CaseSpec("ar-invoice-audit-bad-type", "销售订单不能应收应付审核：400", (), (step,), tags=frozenset(("probe",)))


# ---- 补偿 ----


def _send(env: Env, kind: str, raw: str, action: str) -> str:
    # steps 经 wiring 引用本模块，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.steps import auth_fields, invoke

    fields = auth_fields(env)
    fields.update({"type": kind, "id": int(raw), "action": action})
    try:
        status = invoke(env.client, _VERIFY, fields)[0]
    except (U8CoError, OSError, RuntimeError, ValueError) as exc:
        return f"{action} 出错：{str(exc)[:60]}"
    return f"{action} {status}"


def _undo(env: Env, key: str, kind: str, actions: tuple[str, ...]) -> str:
    raw = env.bag.get(key, "")
    if _ID.fullmatch(raw or "") is None:
        return "没有挑中的单据"
    notes = [_send(env, kind, raw, action) for action in actions]
    return f"{kind} {raw}：" + "，".join(notes) + "（未审核时 409 属正常）"


def ap_audit_compensate(env: Env, _case: str) -> str:
    return _undo(env, "arv_pu", "purchase_invoice", ("arap_unverify",))


def ar_audit_compensate(env: Env, _case: str) -> str:
    # 先弃审再弃复；发票本身留给快照还原（同 invoice-generate）。
    return _undo(env, "aud_inv", "sale_invoice", ("arap_unverify", "unverify"))


ARV_CLEANUP = {"ap-invoice-audit": ap_audit_compensate, "ar-invoice-audit": ar_audit_compensate}


def arv_cases() -> list[CaseSpec]:
    return [
        _pu_case(),
        _refused("ap-invoice-audit-audited", "arv_pudone", "已应付审核的采购发票再审核：409"),
        _refused("ap-invoice-audit-unreviewed", "arv_puraw", "未采购复核的采购发票应付审核：409"),
        _ar_case(),
        _bad_type_case(),
    ]
