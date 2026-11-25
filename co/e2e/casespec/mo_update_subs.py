"""生产订单修改保留替代料（mo-update-substitutes，可选）。

挑一个标准 BOM 子件带替代料的自制件（mo_update_subs_sql.MOS_PACKS），新建生产订单（数量 1，U8 按 BOM 展开子件和替代料）
→ 记下新增时 U8 带出的替代料行数（mos_created，只记录）→ 一行都没有时夹具照 BOM 给一个子件插一行（mos_seed），
保证修改路径总被走到 → 核对每行替代料都是 BOM 上该子件的替代料 → 记下替代料行数、内容校验和、子件最大 AllocateId → 只改备注和完工日期
（mou_memo）→ 替代料行数、校验和不变，全部挂在新子件上 → 数量改成 2（mou_double）→ 同样核对（替代系数不随数量缩放）
→ 删除，回读已删。挑不到这类 BOM 的通道记 N/A。
接线：MOS_BUILDERS / MOS_CLEANUP / mos_cases 经 mo_update 汇总，MOS_PACKS 进 wiring 挑单，回读并进 mo_update_sql。
"""

from __future__ import annotations

import datetime as dt

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step, apply_step, call_made, pred_step, stash_step
from co.e2e.casespec.mo_create import MEMO, moc_compensate

_KIND = "production_order"
_UPDATE = "/v1/vouchers/update"
_DELETE = "/v1/vouchers/delete"
_NEEDS = ("mos_inv", "mos_dept", "mos_type")
NAME = "mo-update-substitutes"


def _day(env: Env, shift: int) -> str:
    return (dt.date.fromisoformat(env.today) + dt.timedelta(days=shift)).isoformat()


def mos_new(env: Env) -> dict:
    # 同 mo_create.moc_new，用 mos_* 挑单；BOM 有效期不含开工、完工日期时不建单。
    start, end = env.bag.get("mos_bom_from", ""), env.bag.get("mos_bom_to", "")
    if not (start and end and start <= env.today <= _day(env, 2) <= end):
        raise ValueError(f"挑到的标准 BOM 有效期 {start or '?'}～{end or '?'} 不含 {env.today}")
    line = {
        "inv_code": env.bag["mos_inv"],
        "qty": 1,
        "start_date": env.today,
        "due_date": _day(env, 2),
        "mo_type": env.bag["mos_type"],
        "dept_code": env.bag["mos_dept"],
    }
    return {"type": _KIND, "head": {"remark": MEMO}, "lines": [line]}


MOS_BUILDERS = {"mos_new": mos_new}


def _kept() -> tuple[Step, ...]:
    # 修改后：替代料行数、内容不变，全部在新子件上；再记下新的最大 AllocateId 给下一次修改比。
    return (
        pred_step("mos_scnt", "same:mos_scnt"),
        pred_step("mos_digest", "same:mos_digest"),
        pred_step("mos_moved", "eq:1"),
        stash_step("mos_amax", "mos_amax"),
    )


def _case() -> CaseSpec:
    made = (("type", _KIND), ("code", "$nonempty"), ("lines", "1"), ("allocates", "$gt0"), ("state.verified", "false"))
    saved = (("ok", "true"), ("changed", "1"), ("allocates", "$gt0"))
    ref = (("type", _KIND), ("id", "$id"))
    steps = (
        Step("create", sql_name="mos_new", checks=made),
        stash_step("mos_created", "mos_created"),
        stash_step("mos_seed", "mos_seeded"),
        pred_step("mos_scnt", "gt0"),
        pred_step("mos_bom_match", "eq:1"),
        stash_step("mou_line", "mou_line"),
        stash_step("mos_scnt", "mos_scnt"),
        stash_step("mos_digest", "mos_digest"),
        stash_step("mos_amax", "mos_amax"),
        apply_step(_UPDATE, "mou_memo", "made", saved),
        *_kept(),
        apply_step(_UPDATE, "mou_double", "made", saved),
        pred_step("mou_scaled", "eq:1"),
        *_kept(),
        call_made(_DELETE, ref, (("deleted", "true"),)),
        pred_step("mou_gone", "eq:0"),
    )
    title = "生产订单修改保留替代料：改备注、改数量后替代料原样挂在新子件上，然后删除"
    return CaseSpec(NAME, title, _NEEDS, steps, True, optional=True, tags=frozenset(("bom",)))


MOS_CLEANUP = {NAME: moc_compensate}


def mos_cases() -> list[CaseSpec]:
    return [_case()]
