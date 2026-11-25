"""应收 / 应付制单（arap/voucher）和取消制单（arap/voucher/delete）。

ar-voucher：自己挑的发货行（沿用 ar-invoice-audit 的挑单 arv_inv，同一个 src 标签，两条用例不同时跑）生成 1 个数量的销售发票
  → 复核 → 未应收审核就制单 409 → 应收审核 → 制单（pz_id 以 AR 开头、type / flag 回显）→ 凭证行对得上（至少 2 行、借贷平、
  借方合计 = 发票价税合计、每行带应收来源、第 1 行是往来明细科目的借方）、回写齐全（往来明细 cPZid / cGLSign / iGLno_id、
  发票表体线索号和凭证号）→ 再制单 409（凭证已生成）→ 取消制单 → 凭证行、往来明细、发票线索号都清干净 → 再取消 404
  → 弃审 → 弃复 → 删除（同 ar-invoice-audit，删除可能因已复核开票数量残留而 409，发票留给快照还原）。
  中途失败时 AV_CLEANUP 尽力取消制单、弃审、弃复。
ar-voucher-bad-flag：销售发票送 flag=AP、取消制单的 pz_id 与 flag 不一致，都是登录前 400。

回读 SQL 在 arap_voucher_sql（接线在 wiring 汇总模块）。
"""

from __future__ import annotations

import re

from co.client.u8co_client import U8CoError
from co.e2e.casespec.assertutil import EITHER
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_from, keep_step, pred_step, stash_step
from co.e2e.casespec.arap_voucher_sql import PZ_KEY, pz_of

ROUTE = "/v1/arap/voucher"
DROP = "/v1/arap/voucher/delete"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_KEY = "av_inv"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_INT_MAX = 2147483647
_CONFLICT = (409, "state_mismatch")
_BAD = (400, "bad_request")
_GONE = (404, "not_found")


def _invoice(env: Env) -> int:
    raw = env.bag.get(_KEY, "")
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError("缺发票 id")
    return int(raw)


def make_body(env: Env) -> dict:
    return {"flag": "AR", "type": "sale_invoice", "id": _invoice(env)}


def drop_body(env: Env) -> dict:
    return {"flag": "AR", "pz_id": pz_of(env.bag)}


def bad_flag_body(_env: Env) -> dict:
    return {"flag": "AP", "type": "sale_invoice", "id": 1}


def bad_drop_body(_env: Env) -> dict:
    return {"flag": "AP", "pz_id": "AR0000000000001"}


AV_BUILDERS = {
    "av_make": make_body,
    "av_drop": drop_body,
    "av_bad_flag": bad_flag_body,
    "av_bad_drop": bad_drop_body,
}


def _made_steps() -> tuple:
    src = "bag:" + _KEY
    # making_system 为 AR：以应收登录、按 AR 导入直接成功，没有走 GL 兜底。
    checks = (("ok", "true"), ("pz_id", "$prefix:AR"), ("flag", "AR"), ("type", "sale_invoice"), ("making_system", "AR"))
    return (
        apply_step(ROUTE, "av_make", "none", checks),
        stash_step("av_pz", PZ_KEY, src),
        # 凭证来源列 coutsysname / coutsign 都是 AR（见 arap_extra.pz_src，经 ops_run 接线）。
        Step("custom", fields=(("side", "AR"), ("key", PZ_KEY), ("sign", "AR")), sql_name="pz_src", id_from="none"),
        pred_step("av_glbad", "eq:0", src),
        pred_step("av_marks", "eq:0", src),
        apply_step(ROUTE, "av_make", "none", (), _CONFLICT),
        apply_step(DROP, "av_drop", "none", (("ok", "true"), ("deleted", "true"), ("pz_id", "$prefix:AR"))),
        pred_step("av_left", "eq:0", src),
        apply_step(DROP, "av_drop", "none", (), _GONE),
    )


def _ar_case() -> CaseSpec:
    kind = (("type", "sale_invoice"), ("id", "$id"))
    src = "bag:" + _KEY
    steps = (
        apply_step("/v1/vouchers/generate", "arv_inv_gen", "need", (("type", "sale_invoice"), ("code", "$nonempty"))),
        keep_step(_KEY),
        call_from(_VERIFY, kind + (("action", "verify"),), src),
        apply_step(ROUTE, "av_make", "none", (), _CONFLICT),
        call_from(_VERIFY, kind + (("action", "arap_verify"),), src),
    )
    steps += _made_steps()
    steps += (
        call_from(_VERIFY, kind + (("action", "arap_unverify"),), src),
        call_from(_VERIFY, kind + (("action", "unverify"),), src),
        call_from(_DELETE, kind, src, (("deleted", "true"),), (EITHER, "200|409:state_mismatch")),
    )
    title = (
        "应收制单：销售发票未应收审核 409；审核后制单，凭证借贷平、等于价税合计、带应收来源，回写齐全；重复制单 409；"
        "取消制单后凭证和回写清干净，再取消 404；弃审、弃复、删除（有残留时删除 409）"
    )
    needs = ("arv_inv", "arv_inv_line")
    # 与 ar-invoice-audit 同一个挑单，src 标签相同，两条不同时跑；新建凭证占总账「转」字编号，另带 gl。
    tags = frozenset(("src:dispatch:arv_inv", "w:sale_invoice", "gl", "new:gl:转"))
    return CaseSpec("ar-voucher", title, needs, steps, True, tags=tags, routes=(ROUTE, DROP, _VERIFY))


def _bad_case() -> CaseSpec:
    steps = (apply_step(ROUTE, "av_bad_flag", "none", (), _BAD), apply_step(DROP, "av_bad_drop", "none", (), _BAD))
    title = "销售发票不能按应付制单、取消制单的 flag 与外部业务号前缀不一致：登录前 400"
    return CaseSpec("ar-voucher-bad-flag", title, (), steps, tags=frozenset(("probe",)), routes=(ROUTE, DROP))


# ---- 补偿 ----


def _send(env: Env, route: str, fields: dict) -> str:
    # steps 经 wiring 引用本模块，放到调用时再引入，避免循环引用。
    from co.e2e.casespec.steps import auth_fields, invoke

    body = auth_fields(env)
    body.update(fields)
    label = fields.get("action") or route.rsplit("/", 1)[-1]
    try:
        status = invoke(env.client, route, body)[0]
    except (U8CoError, OSError, RuntimeError, ValueError) as exc:
        return f"{label} 出错：{str(exc)[:60]}"
    return f"{label} {status}"


def av_compensate(env: Env, _case: str) -> str:
    # 先取消制单（记下了外部业务号才做），再弃审、弃复；发票本身留给快照还原（同 ar-invoice-audit）。
    raw = env.bag.get(_KEY, "")
    if _ID.fullmatch(raw or "") is None:
        return "没有生成的发票"
    notes = []
    try:
        notes.append(_send(env, DROP, drop_body(env)))
    except ValueError:
        notes.append("没有外部业务号，不取消制单")
    for action in ("arap_unverify", "unverify"):
        notes.append(_send(env, _VERIFY, {"type": "sale_invoice", "id": int(raw), "action": action}))
    return f"sale_invoice {raw}：" + "，".join(notes) + "（未制单、未审核时 404 / 409 属正常）"


AV_CLEANUP = {"ar-voucher": av_compensate}


def av_cases() -> list[CaseSpec]:
    return [_ar_case(), _bad_case()]
