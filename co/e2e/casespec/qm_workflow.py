"""产品检验单审批闭环，以及只读状态。

wf-qm04-cycle 第一次同意后由自定义步骤 wf_qm04_rest 分支：仍有待办时照常跑弃审、退回、重新提交、同意、
不同意和历史（rest_steps）；没有待办（审批流只有一个环节一个审批人）时核对单据已通过，跳过其余步骤。
"""

from __future__ import annotations

from collections.abc import Callable

from co.e2e.casespec.assertutil import expected_history
from co.e2e.casespec.env import Env
from co.e2e.casespec.gate import stopped
from co.e2e.casespec.model import CaseSpec, Step, call, sql_step
from co.e2e.casespec.steps import step_call
from co.e2e.casespec.steps_sql import step_sql

_QM = (("type", "qm_product_check"), ("id", "$id"))
_OPINION = _QM + (("opinion", "回归"),)
REST_OP = "wf_qm04_rest"


def _open() -> tuple[Step, ...]:
    return (
        call("/v1/workflow/state", _QM, (("wf.status", "not_submitted"),)),
        # 首环节待审人数取决于账套的审批流定义，只要求至少一人。
        call("/v1/workflow/submit", _QM, (("wf.status", "in_approval"), ("wf.pending#len", "$gt0"))),
        call("/v1/workflow/submit", _QM, (), 409, "already_submitted"),
        call("/v1/workflow/approve", _QM, (), 409, "not_current_approver"),
        sql_step("shape", "1"),
        call("/v1/workflow/approve", _QM),
        Step("custom", sql_name=REST_OP),
    )


def _middle() -> tuple[Step, ...]:
    returned = ("wf.status", "returned")
    return (
        call("/v1/workflow/abandon", _QM),
        sql_step("shape", "1"),
        call("/v1/workflow/return", _OPINION, (returned,)),
        call("/v1/workflow/tasks", (("type", "qm_product_check"),), (("tasks#type5", "5"),)),
        call("/v1/workflow/resubmit", _QM),
        sql_step("shape", "1"),
        call("/v1/workflow/approve", _QM),
    )


def _close() -> tuple[Step, ...]:
    approved = (("wf.status", "approved"), ("wf.verifier", "$opname"))
    undone = (("wf.status", "in_approval"), ("wf.verify_state", "-1"))
    return (
        sql_step("shape", "1"),
        call("/v1/workflow/approve", _QM, approved),
        call("/v1/workflow/abandon", _QM, undone),
        sql_step("shape", "1"),
        call("/v1/workflow/disagree", _OPINION, (("wf.status", "not_approved"),)),
        call("/v1/workflow/history", _QM, (("history#actions", expected_history()),)),
    )


def rest_steps() -> tuple[Step, ...]:
    """第一次同意后仍有待办时接着跑的步骤。"""
    return _middle() + _close()


_RUN: dict[str, Callable[[Env, CaseSpec, Step], tuple[str, str]]] = {"call": step_call, "sql": step_sql}


def _walk(env: Env, spec: CaseSpec, steps: tuple[Step, ...]) -> tuple[str, str]:
    for index, step in enumerate(steps):
        if stopped(env.ctx):
            return "SKIP", f"后续步骤{index + 1} 收到中断，没有执行"
        status, detail = _RUN[step.kind](env, spec, step)
        if status != "PASS":
            return status, f"后续步骤{index + 1} {detail}"
    return "PASS", spec.title


def wf_qm04_rest(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    """有待办：照常跑 rest_steps；没有待办：单据应已通过，其余步骤跳过。"""
    status, detail = step_sql(env, spec, sql_step("pending", "$gt0"))
    if status == "PASS":
        return _walk(env, spec, rest_steps())
    if step_sql(env, spec, sql_step("pending", "0"))[0] != "PASS":
        return status, "待办数：" + detail
    status, detail = step_call(env, spec, call("/v1/workflow/state", _QM, (("wf.status", "approved"),)))
    if status != "PASS":
        return status, "同意后没有待办，单据应已通过：" + detail
    return "PASS", "审批流只有一个待审环节：同意一次即通过，跳过弃审、退回、不同意步骤"


QM_WF_OPS = {REST_OP: wf_qm04_rest}


def _cycle() -> CaseSpec:
    routes = tuple(dict.fromkeys(step.route for step in rest_steps() if step.route))
    return CaseSpec("wf-qm04-cycle", "产品检验单审批闭环", ("qm04_a",), _open(), True, routes=routes)


def _withdraw() -> CaseSpec:
    state = (("wf.status", "not_submitted"), ("instance#off", "off"))
    steps = (
        call("/v1/workflow/submit", _QM),
        call("/v1/workflow/withdraw", _QM),
        call("/v1/workflow/state", _QM, state),
        sql_step("running", "0"),
    )
    return CaseSpec("wf-qm04-withdraw", "产品检验单撤销提交", ("qm04_b",), steps, True)


def _readonly(name: str, title: str, need: str, kind: str, optional: bool = False) -> CaseSpec:
    fields = (("type", kind), ("id", "$id"))
    step = call("/v1/workflow/state", fields, (("wf.status", "$status"),))
    return CaseSpec(name, title, (need,), (step,), True, optional)


def _other_type() -> CaseSpec:
    fields = (("type", "other_in"), ("id", "1"))
    step = call("/v1/workflow/state", fields, (), 400, "bad_request")
    # 只探参数校验（400），不碰账套数据。
    return CaseSpec("wf-other-in-type", "其他入库不走审批流", (), (step,), True, tags=frozenset(("probe",)))


def wf_cases() -> list[CaseSpec]:
    return [
        _cycle(),
        _withdraw(),
        # 产品不良品处理单接口不能新建（只读和审批），从零造数的账套没有时记 N/A。
        _readonly("wf-qm06-state", "产品不良品处理单状态", "qm06_id", "qm_product_reject", optional=True),
        _readonly("wf-qm03-state", "来料检验单状态", "qm03_id", "qm_incoming_check"),
        _other_type(),
    ]
