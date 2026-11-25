"""预演用例：只校验（validate）的路由、总账凭证操作、应收核销 / 自动核销 / 制单，以及预演的拒绝和结构化错误。

dry-gl-create：总账凭证新建预演（validate、validate_only、detail.voucher），按摘要查不到凭证。
dry-gl-ops：真建一张转账凭证 → 作废、审核预演 → 真作废 → 取消作废、删除预演 → 真删除；每步预演前后读取相同。
dry-archive-create：存货档案新建预演（validate、detail.archive），之后按编码数 0、读取 404。
dry-wf-submit：产品检验单提交审批预演（validate），审批状态前后相同。
dry-mo-create / dry-bom-create：生产订单、物料清单新建预演（validate），表头行数和最大主键不变（可选）。
dry-ar-writeoff：核销预演（rollback、detail.writeoff），余额和核销行不变、预演给出的核销号在核销记录查询里查不到
  → 真核销 1.00 → 取消核销预演
  （detail.writeoff_cancel），余额仍是核销后的 → 真取消 → 复原。失败时 dry_cleanup 按记下的核销号取消。
dry-ar-writeoff-auto：自动核销 dry_run 的 mode 是 plan，余额不变。
dry-ar-voucher：真建一张应收单 → 修改、审核预演 → 真审核 → 制单预演（validate、detail.voucher）→ 弃审预演 → 真弃审
  （成功说明没有真制出凭证）→ 删除预演 → 真删除。
dry-refuse：旧路由带 dry_run 400、预演带幂等键 400、读路由带 dry_run 400（field=dry_run）。都在登录前拒绝。
errors-structured：新建单据带未知字段 400（field 是该字段）、缺 lines 400（field=lines）。
"""

from __future__ import annotations

from co.e2e.casespec.arap_ctl import ctl_step
from co.e2e.casespec.gl_voucher_bodies import GL_DATA, arc_data
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_from, keep_step, pred_step, stash_step
from co.e2e.casespec.writeoff import AR_KEYS
from co.e2e.casespec.writeoff_auto import AUTO_KEYS
from co.e2e.casespec.writeoff_auto import ROUTE as AUTO
from co.e2e.casespec.dry_steps import CREATE, DELETE, LOAD, UPDATE, VERIFY, dry, dry_doc, dry_new, ref, refuse
from co.e2e.testdata import SIGN_TRANSFER

_GL = "/v1/gl/vouchers/"
_WRITEOFF = "/v1/arap/writeoff"
_CANCEL = "/v1/arap/writeoff/cancel"
REPORT = "/v1/reports/arap_writeoffs"
VOUCHER = "/v1/arap/voucher"
_OK = (("ok", "true"),)
_GONE = (("deleted", "true"),)


def _gl(op: str, body: str, checks: tuple = (), expect: tuple = (200, "")) -> Step:
    return Step("core_gl", _GL + op, (), expect[0], expect[1], checks, body)


def _gl_dry(op: str) -> Step:
    return Step("custom", _GL + op, (("op", op),), sql_name="dry_gl_dry", id_from="none")


def _gl_cases() -> list[CaseSpec]:
    made = (("ok", "true"), ("sign", "$td:" + SIGN_TRANSFER), ("no", "$gt0"))
    ops = (
        _gl("create", "create_transfer", made),
        _gl_dry("void"),
        _gl_dry("verify"),
        _gl("void", "key", _OK),
        _gl_dry("unvoid"),
        _gl_dry("delete"),
        _gl("delete", "key", _GONE),
        _gl("load", "key", (), (404, "not_found")),
        Step("core_glsql", sql_name="rows", code="0"),
    )
    new = (Step("custom", _GL + "create", sql_name="dry_gl_new", id_from="none"),)
    tags = frozenset(("gl", "new:gl:转"))
    routes = (_GL + "create", _GL + "void", _GL + "verify", _GL + "unvoid", _GL + "delete", _GL + "load")
    return [
        CaseSpec("dry-gl-create", "预演：总账凭证新建只校验（validate），按摘要查不到凭证", (), new, True,
                 data=GL_DATA, tags=frozenset(("gl",)), routes=(_GL + "create",)),
        CaseSpec("dry-gl-ops", "预演：总账凭证作废、审核、取消作废、删除都回滚，凭证前后读取相同", (), ops, True,
                 data=GL_DATA, tags=tags, routes=routes),
    ]


def _archive_case() -> CaseSpec:
    arc = (("archive", "inventory"),)
    steps = (
        Step("core_arcpick", fields=arc, sql_name="free"),
        Step("custom", "/v1/archives/create", arc, sql_name="dry_arc_dry", id_from="none"),
        Step("core_arcsql", fields=arc, sql_name="count", code="0"),
        Step("core_arc", "/v1/archives/get", arc, 404, "not_found", (), "key"),
    )
    title = "预演：存货档案新建只校验（validate），之后编码仍不存在"
    return CaseSpec("dry-archive-create", title, (), steps, True, data=arc_data("inventory"),
                    tags=frozenset(("arc:inventory", "arc:write")), routes=("/v1/archives/create", "/v1/archives/get"))


def _validate_cases() -> list[CaseSpec]:
    submit = (dry("/v1/workflow/submit", ref("qm_product_check"), "need", mode="validate",
                  watch="wf.qm_product_check:$id"),)
    mo = (dry_new(CREATE, "production_order", "moc_new", mode="validate"),)
    bom = (dry_new(CREATE, "bom", "bomw_new", mode="validate"),)
    return [
        CaseSpec("dry-wf-submit", "预演：产品检验单提交审批只校验（validate），审批状态不变", ("qm04_b",), submit, True,
                 tags=frozenset(("wf",)), routes=("/v1/workflow/submit", "/v1/workflow/state")),
        CaseSpec("dry-mo-create", "预演：生产订单新建只校验（validate），没有新订单", ("moc_inv", "moc_dept", "moc_type"),
                 mo, True, optional=True, tags=frozenset(("bom",))),
        CaseSpec("dry-bom-create", "预演：物料清单新建只校验（validate），没有新版本",
                 ("bomw_parent", "bomw_c1", "bomw_c2", "bomw_c3", "bomw_ver"), bom, True, optional=True,
                 tags=frozenset(("bom",))),
    ]


def balance_steps(keys: tuple[str, str, str], lines: tuple[str, str], pred: tuple[str, str, str], side: str = "AR") -> tuple:
    """收付款单行余额、发票行余额、发票行核销行条数三个回读（writeoff_sql）；pred 是各自的前缀
    （same: / shift:-1: / plus1:），keys 是 bag 里记下的原值。"""
    names = ("hx_remain", "hx_arbal", "hx_arrows") if side == "AR" else ("hx_remain", "hx_aprbal", "hx_aprows")
    srcs = ("bag:" + lines[0], "bag:" + lines[1], "bag:" + lines[1])
    return tuple(pred_step(name, head + key, src) for name, head, key, src in zip(names, pred, keys, srcs))


def _writeoff_case() -> CaseSpec:
    keys, lines = ("dry_hx_r0", "dry_hx_i0", "dry_hx_n0"), ("hx_rcl", "hx_invl")
    same, moved = ("same:",) * 3, ("shift:-1:", "shift:-1:", "plus1:")
    steps = (
        stash_step("hx_remain", keys[0], "bag:" + lines[0]),
        stash_step("hx_arbal", keys[1], "bag:" + lines[1]),
        stash_step("hx_arrows", keys[2], "bag:" + lines[1]),
        dry(_WRITEOFF, (), builder="hx_ar_one", detail="writeoff"),
        Step("custom", REPORT, (("side", "AR"),), sql_name="dry_hx_unlisted", id_from="none"),
    ) + balance_steps(keys, lines, same)
    wrote = (("ok", "true"), ("cancel_no", "$prefix:HXAR"))
    fields = (("builder", "hx_ar_one"), ("side", "AR"), ("key", "dry_hx_cno"))
    steps += (Step("custom", _WRITEOFF, fields, checks=wrote, sql_name="dry_wo_exec", id_from="none"),)
    steps += balance_steps(keys, lines, moved)
    steps += (dry(_CANCEL, (), builder="dry_hx_cancel", detail="writeoff_cancel"),) + balance_steps(keys, lines, moved)
    steps += (apply_step(_CANCEL, "dry_hx_cancel", "none", _OK), keep_step("dry_hx_cdone", "bag:" + lines[1]))
    steps += balance_steps(keys, lines, same)
    title = "预演：应收核销、取消核销都回滚（余额、核销行不变）；真核销后再真取消复原"
    tags = frozenset(("w:ar_receipt", "w:sale_invoice"))
    return CaseSpec("dry-ar-writeoff", title, AR_KEYS, steps, True, tags=tags, routes=(_WRITEOFF, _CANCEL, REPORT))


def _auto_case() -> CaseSpec:
    keys, lines = ("dry_hxa_r0", "dry_hxa_i0", "dry_hxa_n0"), ("hxa_rcl", "hxa_invl")
    steps = (
        stash_step("hx_remain", keys[0], "bag:" + lines[0]),
        stash_step("hx_arbal", keys[1], "bag:" + lines[1]),
        stash_step("hx_arrows", keys[2], "bag:" + lines[1]),
        Step("custom", AUTO, (("builder", "dry_hxa_plan"),), sql_name="dry_plan", id_from="none"),
    ) + balance_steps(keys, lines, ("same:",) * 3)
    title = "预演：应收自动核销 dry_run 是试算（mode=plan），余额不变"
    tags = frozenset(("w:ar_receipt", "w:sale_invoice"))
    return CaseSpec("dry-ar-writeoff-auto", title, AUTO_KEYS, steps, True, tags=tags, routes=(AUTO,))


def bill_steps(kind: str, key: str) -> tuple:
    """应收单 / 应付单真建之后、真审核之前：修改和审核的预演（修改正文同各 <类型>-edit 用例）。"""
    src = "bag:" + key
    line = kind + "_edit_line"
    return (
        stash_step(line, line, src),
        dry_doc(UPDATE, kind, src, builder="edit_" + kind + "_upd"),
        dry_doc(VERIFY, kind, src, ("action", "verify")),
    )


def _voucher_case() -> CaseSpec:
    key = "dry_arb"
    src = "bag:" + key
    steps = (
        ctl_step("AR"),
        Step("create", sql_name="edit_ar_bill_new", checks=(("type", "ar_bill"), ("code", "$nonempty"))),
        keep_step(key),
    ) + bill_steps("ar_bill", key) + (
        call_from(VERIFY, ref("ar_bill", ("action", "verify")), src),
        dry(VOUCHER, (), builder="dry_arb_voucher", mode="validate", detail="voucher"),
        dry_doc(VERIFY, "ar_bill", src, ("action", "unverify")),
        call_from(VERIFY, ref("ar_bill", ("action", "unverify")), src),
        dry_doc(DELETE, "ar_bill", src),
        call_from(DELETE, ref("ar_bill"), src, _GONE),
        call_from(LOAD, ref("ar_bill"), src, (), (404, "not_found")),
    )
    title = "预演：应收单修改、审核、制单（只算计划）、弃审、删除，都不写入；之后真弃审成功（没有凭证）、删除"
    return CaseSpec("dry-ar-voucher", title, ("ar_master",), steps, True, tags=frozenset(("w:ar_bill",)),
                    routes=(VOUCHER, VERIFY, DELETE))


def _refuse_cases() -> list[CaseSpec]:
    legacy = (("id", "1"), ("action", "verify"))
    steps = (
        refuse("/v1/sale-orders/verify", legacy, dry="1", text="旧路由"),
        refuse("/v1/dispatches/verify", legacy, dry="1", text="旧路由"),
        refuse(CREATE, (), builder="dry_probe_other_in", dry="1", key="1", text="预演"),
        refuse(LOAD, (("type", "sale_order"), ("id", "1")), dry="1", field="dry_run"),
    )
    errors = (
        # 带 dry_run：万一桥以后不在登录前拒绝，也只是一次预演，不会在不排程的 probe 用例里真写。
        refuse(CREATE, (("zz_dry_unknown", "1"),), builder="dry_probe_other_in", text="未知字段", field="zz_dry_unknown",
               dry="1"),
        refuse(CREATE, (), builder="dry_probe_no_lines", field="lines", dry="1"),
    )
    probe = frozenset(("probe",))
    return [
        CaseSpec("dry-refuse", "预演的拒绝：旧路由、带幂等键、读路由都 400，登录前拒绝", (), steps, tags=probe,
                 routes=("/v1/sale-orders/verify", "/v1/dispatches/verify", CREATE, LOAD)),
        CaseSpec("errors-structured", "结构化错误：未知字段、缺字段的 400 带 field", (), errors, tags=probe, routes=(CREATE,)),
    ]


MORE_PLANS = {"dry-ar-voucher": (("dry_arb", "ar_bill", True),)}


def dry_more_cases() -> list[CaseSpec]:
    rows = _gl_cases() + [_archive_case()] + _validate_cases()
    return rows + [_writeoff_case(), _auto_case(), _voucher_case()] + _refuse_cases()
