"""来料 / 产品检验单修改（vouchers/update，VoucherOperate update，U8 自己提交）。

qm03-update-split：照 qm01-create-delete 新建到货单 → 来料报检单 → 来料检验单（1 个，全部合格）→ 预演修改 200 dry_run、
  结论与数量不变、UFTS 不变 → 修改为不良 1、结论不合格、带不良原因 → 回读结论和三个数量、修改人是本操作员、报检单行累计检验数量
  不变 → 删检验单 → 删报检单 → 弃审并删除到货单。
qm03-update-verified-409：同样建到来料检验单 → 提交审批（workflow/submit）→ 修改 409 workflow_enabled、单据不变 →
  撤回（workflow/withdraw）→ 倒序删除。账套没有发布来料检验单审批流时记 N/A（挑单 qmu_wf3 挑不到）。
qm04-update-split：照 qm02-create-delete 新建产品报检单 → 产品检验单 → 修改为让步 1（合格 0）带原因 → 回读 → 倒序删除。
qm02-unverify：照 qm02-create-delete 新建产品报检单（1 个，U8 按选项自动审核）→ 单独审核 400 → 弃审 →
  再弃审 409 → 删报检单 → DeclaredQty 回到原值。产品报检单不开放修改（实测 U8 的 VoucherOperate update 不可用）。

中途失败的补偿沿用 qm_write 的倒序删除；verified 用例先尽力撤回审批。不良原因编码挑 Reason（质量管理类），挑不到 N/A。
接线：QMU_BUILDERS 进 wiring._BUILDER_SETS，QMU_PACKS / has_qmu / qmu_sql / qmu_samples 进 wiring，
qmu_cases() 进 catalog，QMU_CLEANUP 进 runner._CLEANUP。
"""

from __future__ import annotations

from co.client.u8co_client import U8CoError
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, apply_step, call_from, keep_step, pred_step, stash_step
from co.e2e.casespec.qm_write import (
    _ARR,
    _GONE,
    _ID,
    _NEEDS_QMI,
    _NEEDS_QMP,
    _OK,
    _Q1,
    _Q2,
    _Q3,
    _Q4,
    TPL76,
    _code,
    _focus,
    _made,
    _qm01_head_steps,
    _ref,
    _send,
    qmi_compensate,
    qmp_compensate,
)

_GEN = "/v1/vouchers/generate"
_UPD = "/v1/vouchers/update"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_SUBMIT = "/v1/workflow/submit"
_WITHDRAW = "/v1/workflow/withdraw"
_REASON = "qmu_rsn"
_APPROVER = "qmu_ylr"
_FLOW = "qmu_wf3"
_FRESH = "eq:合格|1.0000|0.0000|0.0000"
_DRY = (("dry_run", "true"),)


# ---- 正文 ----


def _update(env: Env, kind: str, head: dict, dry: bool = False) -> dict:
    body = {"type": kind, "id": _focus(env), "head": head}
    if dry:
        body["dry_run"] = True
    return body


def _split3(env: Env) -> dict:
    head = {"fRegQuantity": 0, "fDisQuantity": 1, "cChkConclusion": "不合格", "cReasonCode": _code(env, _REASON)}
    return head


def qmu_qm03_split(env: Env) -> dict:
    return _update(env, "qm_incoming_check", _split3(env))


def qmu_qm03_split_dry(env: Env) -> dict:
    return _update(env, "qm_incoming_check", _split3(env), dry=True)


def qmu_qm03_touch(env: Env) -> dict:
    return _update(env, "qm_incoming_check", {"cChkConclusion": "合格"})


def qmu_qm04_split(env: Env) -> dict:
    # 有让步数量时要让步接收核准人 CYIELDERCODE（实测「'让步接收核准人'不能为空」；桥按人员档案写姓名，日期缺省登录日期）。
    head = {"fRegQuantity": 0, "fConQuantiy": 1, "cChkConclusion": "合格", "cReasonCode": _code(env, _REASON),
            "cYielderCode": _code(env, _APPROVER)}
    return _update(env, "qm_product_check", head)


QMU_BUILDERS = {
    "qmu_qm03_split": qmu_qm03_split,
    "qmu_qm03_split_dry": qmu_qm03_split_dry,
    "qmu_qm03_touch": qmu_qm03_touch,
    "qmu_qm04_split": qmu_qm04_split,
}


# ---- 用例 ----


def _qm03_made() -> tuple:
    check = _made("qm_incoming_check", ("lines", "1"), ("state.verified", "false"))
    return (
        apply_step(_GEN, "qmi_qm03", _Q1, check),
        keep_step("qmi_qm03"),
        pred_step("qmu_q3_res", _FRESH, _Q3),
    )


def _qm03_drop() -> tuple:
    # 删检验单 → 报检单行累计归零 → 删报检单（桥先弃审）→ 弃审并删除到货单。
    return (
        call_from(_DELETE, _ref("qm_incoming_check"), _Q3, _GONE),
        pred_step("qmi_q1_sum", "eq:0.0000|0", _Q1),
        call_from(_DELETE, _ref("qm_incoming_inspect"), _Q1, _GONE + (("unverified", "true"),)),
        pred_step("qmi_q1_gone", "eq:0", _Q1),
        call_from(_VERIFY, _ref("arrival", ("action", "unverify")), _ARR, _OK),
        call_from(_DELETE, _ref("arrival"), _ARR, _GONE),
    )


def _tags(*extra: str) -> frozenset[str]:
    # 与 qm01-create-delete 同一组标签：同一张采购订单、同样动到货单，互斥。
    base = ("new:arrival", "new:purchase_return", "new:qm_incoming_inspect", "new:qm_incoming_check",
            "src:purchase_order:qmi_po", "stock", "w:qm_incoming_check")
    return frozenset(base + extra)


def _qm03_split_case() -> CaseSpec:
    saved = (("type", "qm_incoming_check"), ("state.verified", "false"), ("lines", "$gt0"))
    steps = _qm01_head_steps() + _qm03_made() + (
        stash_step("qmu_q3_ufts", "qmu_q3_ufts", _Q3),
        apply_step(_UPD, "qmu_qm03_split_dry", _Q3, _DRY),
        pred_step("qmu_q3_res", _FRESH, _Q3),
        pred_step("qmu_q3_ufts", "same:qmu_q3_ufts", _Q3),
        apply_step(_UPD, "qmu_qm03_split", _Q3, saved),
        pred_step("qmu_q3_res", "eq:不合格|0.0000|0.0000|1.0000", _Q3),
        pred_step("qmu_q3_mod", "opname", _Q3),
        pred_step("qmi_q1_sum", "eq:1.0000|1", _Q1),
    ) + _qm03_drop()
    title = "来料检验单修改：预演不落库，合格改不良带原因，回读数量、结论、修改人，报检单累计不变，倒序删除"
    return CaseSpec("qm03-update-split", title, _NEEDS_QMI + (_REASON,), steps, True, optional=True, tags=_tags(),
                    routes=(_GEN, _UPD, _VERIFY, _DELETE))


def _qm03_verified_case() -> CaseSpec:
    steps = _qm01_head_steps() + _qm03_made() + (
        call_from(_SUBMIT, _ref("qm_incoming_check"), _Q3, ()),
        apply_step(_UPD, "qmu_qm03_touch", _Q3, (), (409, "workflow_enabled")),
        pred_step("qmu_q3_res", _FRESH, _Q3),
        call_from(_WITHDRAW, _ref("qm_incoming_check"), _Q3, ()),
    ) + _qm03_drop()
    title = "来料检验单提交审批后修改 409 workflow_enabled、单据不变，撤回后倒序删除"
    return CaseSpec("qm03-update-verified-409", title, _NEEDS_QMI + (_FLOW,), steps, True, optional=True, tags=_tags("wf"),
                    routes=(_GEN, _UPD, _SUBMIT, _WITHDRAW, _VERIFY, _DELETE))


def _qm04_split_case() -> CaseSpec:
    inspect = _made("qm_product_inspect", ("lines", "1"), ("source_type", "production_order"), ("state.verified", "true"))
    check = _made("qm_product_check", ("lines", "1"), ("state.verified", "false"))
    steps = TPL76 + (
        stash_step("qmp_decl", "qmp_decl", "need"),
        apply_step(_GEN, "qmp_qm02", "need", inspect),
        keep_step("qmp_qm02"),
        stash_step("qmp_q2l", "qmp_q2l", _Q2),
        apply_step(_GEN, "qmp_qm04", _Q2, check),
        keep_step("qmp_qm04"),
        pred_step("qmu_q4_res", _FRESH, _Q4),
        apply_step(_UPD, "qmu_qm04_split", _Q4, (("type", "qm_product_check"), ("state.verified", "false"))),
        pred_step("qmu_q4_res", "eq:合格|0.0000|1.0000|0.0000", _Q4),
        pred_step("qmu_q4_mod", "opname", _Q4),
        pred_step("qmp_q2_sum", "eq:1.0000|1", _Q2),
        call_from(_DELETE, _ref("qm_product_check"), _Q4, _GONE),
        call_from(_DELETE, _ref("qm_product_inspect"), _Q2, _GONE),
        pred_step("qmp_q2_gone", "eq:0", _Q2),
        pred_step("qmp_decl", "same:qmp_decl", "need"),
    )
    title = "产品检验单修改：合格改让步带原因和让步接收核准人，回读数量、结论、修改人，报检单累计不变，倒序删除"
    tags = frozenset(("new:qm_product_inspect", "new:qm_product_check", "src:production_order:qmp_mo", "stock",
                      "w:qm_product_check"))
    return CaseSpec("qm04-update-split", title, _NEEDS_QMP + (_REASON, _APPROVER), steps, True, optional=True, tags=tags,
                    routes=(_GEN, _UPD, _DELETE))


def _qm02_unverify_case() -> CaseSpec:
    inspect = _made("qm_product_inspect", ("lines", "1"), ("source_type", "production_order"), ("state.verified", "true"))
    unverified = (("ok", "true"), ("state.verified", "false"))
    steps = TPL76 + (
        stash_step("qmp_decl", "qmp_decl", "need"),
        apply_step(_GEN, "qmp_qm02", "need", inspect),
        keep_step("qmp_qm02"),
        pred_step("qmp_decl", "shift:1:qmp_decl", "need"),
        # 产品报检单只开放弃审：审核在 API 层 400；弃审后再弃审 409。
        call_from(_VERIFY, _ref("qm_product_inspect", ("action", "verify")), _Q2, (), (400, "bad_request")),
        call_from(_VERIFY, _ref("qm_product_inspect", ("action", "unverify")), _Q2, unverified),
        call_from(_VERIFY, _ref("qm_product_inspect", ("action", "unverify")), _Q2, (), (409, "state_mismatch")),
        call_from(_DELETE, _ref("qm_product_inspect"), _Q2, _GONE),
        pred_step("qmp_q2_gone", "eq:0", _Q2),
        pred_step("qmp_decl", "same:qmp_decl", "need"),
    )
    title = "产品报检单单独弃审：审核 400，弃审后再弃审 409，删除后生产订单累计报检数量复原"
    tags = frozenset(("new:qm_product_inspect", "src:production_order:qmp_mo", "stock", "w:qm_product_inspect"))
    return CaseSpec("qm02-unverify", title, _NEEDS_QMP, steps, True, optional=True, tags=tags, routes=(_GEN, _VERIFY, _DELETE))


# ---- 补偿 ----


def _withdraw_then(env: Env, case: str) -> str:
    # 提交了审批的检验单删不掉：先尽力撤回，再按 qm_write 倒序删除。
    raw = env.bag.get("qmi_qm03", "")
    note = ""
    if _ID.fullmatch(raw or "") is not None:
        try:
            _send(env, _WITHDRAW, {"type": "qm_incoming_check", "id": int(raw)})
        except (U8CoError, OSError, RuntimeError, ValueError) as exc:
            note = "撤回出错：" + str(exc)[:40] + "；"
    return note + qmi_compensate(env, case)


QMU_CLEANUP = {
    "qm03-update-split": qmi_compensate,
    "qm03-update-verified-409": _withdraw_then,
    "qm04-update-split": qmp_compensate,
    "qm02-unverify": qmp_compensate,
}


def qmu_cases() -> list[CaseSpec]:
    return [_qm03_split_case(), _qm03_verified_case(), _qm04_split_case(), _qm02_unverify_case()]
