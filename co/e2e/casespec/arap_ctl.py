"""应收 / 应付单据用例的共用前置步骤：账套没有使用应收 / 应付款管理时记 N/A。

桥对收付款单表体科目、应收应付单表头科目要求本系统受控（code.cother = AR / AP，见桥 ArapCoRefs）。从没用过应收 / 应付的
账套（科目表里登录年度没有 cother = AR / AP 的末级科目）建这些单据只会 400「科目不是应付受控科目」，不是接口的问题。
收款单 / 付款单 / 应收单 / 应付单的新建、修改、预演、制单等用例都把 ctl_step 放在第一步。步骤 kind 为 "custom"
（ARAP_CTL_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arap_gen_sql import arap_ctl_sql
from co.e2e.casespec.sqlexec import run_guarded

OP = "arap_ctl"
# 系统 → (受控科目名称, 模块名称)。
SIDES = {"AR": ("应收", "应收款管理"), "AP": ("应付", "应付款管理")}
# 挑单键 → 系统。
NEED_SIDE = {"ar_master": "AR", "ap_master": "AP"}


def ctl_step(side: str) -> Step:
    if side not in SIDES:
        raise ValueError("side 只能是 AR 或 AP")
    return Step("custom", fields=(("side", side),), sql_name=OP, id_from="none")


def ctl_step_for(need: str) -> Step:
    return ctl_step(NEED_SIDE[need])


def na_text(side: str) -> str:
    ctl, module = SIDES[side]
    return f"账套没有{ctl}受控科目，未使用{module}"


def arap_ctl(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    side = dict(step.fields).get("side", "")
    if side not in SIDES:
        return "FAIL", "side 只能是 AR 或 AP"
    year = str(env.year or "").strip()
    if not year.isdigit():
        return "FAIL", "登录年度不对：" + year
    try:
        cell = first_cell(run_guarded(env, arap_ctl_sql(side, int(year)), spec.name)).strip()
    except (RuntimeError, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    if not cell.isdigit():
        return "FAIL", "受控科目个数读不出：" + (cell or "空")
    if int(cell) == 0:
        return "N/A", f"{na_text(side)}（{year} 年度科目表没有 cother={side} 的末级科目）"
    return "PASS", f"{year} 年度{SIDES[side][0]}受控末级科目 {cell} 个"


ARAP_CTL_OPS = {OP: arap_ctl}
