"""生产订单修改（U8API MOrderLoad + MOrderUpdate，子件全部重送）。

新建一张按标准 BOM 展开子件的生产订单（沿用 moc_new 的挑单和正文）→ 改开工日期 400 → 改备注和完工日期 → 核对子件行数、
数量合计、关联列校验和（OpComponentId、条码、需求跟踪、用量、日期、UpperMoQty、损耗、累计量、替代料行数）都没变 →
数量翻倍 → 核对子件行数和校验和不变、每个子件数量按基本用量重算，并按 U8 的完整算法（固定用量、损耗率、换算率，
存货数量 / 件数小数位）核对 → 删除 → 回读表头、明细、子件都已删。
回读 SQL 在 mo_update_sql。接线：MOU_BUILDERS / has_mou / mou_sql / mou_samples 汇到 wiring 模块，
mou_cases() 进 catalog，MOU_CLEANUP（沿用 moc_compensate：弃审 + 删除新建的订单）进 runner。
已审核 / 已领料的修改用例在 mo_edit（回读 SQL 在 mo_edit_sql），保留替代料的用例在 mo_update_subs
（挑单与回读在 mo_update_subs_sql），都经这里的同一套接线登记。
"""

from __future__ import annotations

import datetime as dt

from co.e2e.casespec.env import Env
from co.e2e.casespec.mo_update_subs import MOS_BUILDERS, MOS_CLEANUP, mos_cases
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_made, pred_step, stash_step
from co.e2e.casespec.mo_create import moc_compensate
from co.e2e.casespec.mo_update_sql import MEMO_UPD
from co.e2e.casespec.mo_edit import MOE_BUILDERS, MOE_CLEANUP, moe_cases

_KIND = "production_order"
_UPDATE = "/v1/vouchers/update"
_DELETE = "/v1/vouchers/delete"
_NEEDS = ("moc_inv", "moc_dept", "moc_type")
_NAME = "mo-update-allocates"


# ---- 正文 ----


def _id(raw: str, what: str) -> int:
    if not raw.isdigit() or raw == "0" or int(raw) > 2147483647:
        raise ValueError(what)
    return int(raw)


def _edit(env: Env, **fields: object) -> dict:
    line = {"op": "update", "line_id": _id(env.bag.get("mou_line", ""), "行 id 不合法")}
    line.update(fields)
    return {"type": _KIND, "id": _id(env.focus, "单据 id 不合法"), "lines": [line]}


def mou_memo(env: Env) -> dict:
    # 表头 remark 作为各行备注；完工日期从开工 + 2 天改成 + 3 天。
    due = (dt.date.fromisoformat(env.today) + dt.timedelta(days=3)).isoformat()
    body = _edit(env, due_date=due)
    body["head"] = {"remark": MEMO_UPD}
    return body


def mou_double(env: Env) -> dict:
    # 新建时数量是 1（moc_new），改成 2。
    return _edit(env, qty=2)


def mou_start(env: Env) -> dict:
    # 开工日期不开放修改：桥 / API 400，不调用 U8。
    start = (dt.date.fromisoformat(env.today) + dt.timedelta(days=1)).isoformat()
    return _edit(env, start_date=start)


MOU_BUILDERS = {"mou_memo": mou_memo, "mou_double": mou_double, "mou_start": mou_start}
# 已审核 / 已领料的修改（mo_edit）的正文也从这里接线。
MOU_BUILDERS.update(MOE_BUILDERS)
# 保留替代料（mo_update_subs）的正文也从这里接线。
MOU_BUILDERS.update(MOS_BUILDERS)


# ---- 用例 ----


def _update_case() -> CaseSpec:
    made = (("type", _KIND), ("code", "$nonempty"), ("lines", "1"), ("allocates", "$gt0"), ("state.verified", "false"))
    saved = (("ok", "true"), ("changed", "1"), ("allocates", "$gt0"))
    ref = (("type", _KIND), ("id", "$id"))
    steps = (
        Step("create", sql_name="moc_new", checks=made),
        pred_step("moc_alloc", "gt0"),
        stash_step("mou_line", "mou_line"),
        stash_step("mou_qty", "mou_qty"),
        stash_step("mou_acnt", "mou_acnt"),
        stash_step("mou_asum", "mou_asum"),
        stash_step("mou_digest", "mou_digest"),
        apply_step(_UPDATE, "mou_start", "made", (), (400, "bad_request")),
        apply_step(_UPDATE, "mou_memo", "made", saved),
        pred_step("mou_memo", "eq:1"),
        pred_step("mou_acnt", "same:mou_acnt"),
        pred_step("mou_asum", "same:mou_asum"),
        pred_step("mou_digest", "same:mou_digest"),
        apply_step(_UPDATE, "mou_double", "made", saved),
        pred_step("mou_qty", "double:mou_qty"),
        pred_step("mou_acnt", "same:mou_acnt"),
        pred_step("mou_scaled", "eq:1"),
        pred_step("mou_digest", "same:mou_digest"),
        # 子件数量、件数按账套小数位舍入，且按 U8 的算法（固定用量、损耗率、换算率）重算；
        # 记下本单固定用量、带损耗、有换算率的子件数，看这次覆盖了哪些情形。
        pred_step("mou_prec", "eq:1"),
        pred_step("mou_formula", "eq:1"),
        stash_step("mou_nfixed", "mou_nfixed"),
        stash_step("mou_nscrap", "mou_nscrap"),
        stash_step("mou_naux", "mou_naux"),
        call_made(_DELETE, ref, (("deleted", "true"),)),
        pred_step("mou_gone", "eq:0"),
    )
    title = "生产订单修改：改备注和完工日期子件不变、数量翻倍子件按基本用量重算，然后删除"
    # bom：按 BOM 展开的生产订单与改 BOM 版本的用例不同时跑（同 mo-create-delete）。
    return CaseSpec(_NAME, title, _NEEDS, steps, True, optional=True, tags=frozenset(("bom",)))


MOU_CLEANUP = {_NAME: moc_compensate}
MOU_CLEANUP.update(MOE_CLEANUP)
MOU_CLEANUP.update(MOS_CLEANUP)


def mou_cases() -> list[CaseSpec]:
    return [_update_case(), *moe_cases(), *mos_cases()]
