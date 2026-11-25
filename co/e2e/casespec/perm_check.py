"""权限核对：桥 perm/evaluate、perm/snapshot 给出的权限，与账套库 SQL 独立算出的应有权限（perm_oracle）逐项一致。

perm-oracle-all-ops：枚举本账套有授权的全部启用操作员（操作员档案），评估操作员逐个 evaluate，数据权限（各对象全部 /
  编码个数与摘要）、字段权限（columns，对象 → 不可见字段，集合相等）、objects_on、data_admin 与账套库一致；功能权限读不到 UFSYSTEM，只核「有授权的操作员 functions 非空」。
perm-supervisor-all：evaluate 为账套主管的操作员，各对象都是全部、functions 非空；测试操作员能否读操作员档案（只有主管能读）
  与 evaluate 的 supervisor 一致。
perm-empty-ops：账套库里出现过（数据权限行、人员对照）但没有本账套授权的操作员，evaluate 得 404 或 supervisor false、
  functions 为空、数据权限与账套库一致（受控对象没有编码就是一个都看不到）；不存在的操作员 404。
perm-wh-control：打开了仓库数据权限的账套，逐个操作员核对仓库的编码；开关关着记 N/A。
perm-snapshot-self：测试操作员（和评估操作员）自己的 snapshot 与账套库一致，并与评估操作员对它的 evaluate 完全相同（含指纹）。
perm-eval-denied：不在 permEvaluateOperators 里的测试操作员（即使是账套主管）调 evaluate 得 403。
perm-read-subset：测试操作员（和评估操作员）每个受控对象的档案列表，每一行都在自己 snapshot 的编码里。

全部只读。没有配置评估操作员（account.perm_evaluator_file）的通道，需要它的用例记 N/A。报告里只有操作员编码、对象名和个数。
步骤 kind 为 "custom"，sql_name 是操作名（PERM_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.cases import Operator
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.perm_check_io import (
    ARC_LIST,
    EVAL,
    NO_EVALUATOR,
    SNAP,
    NoEvaluator,
    call_as,
    col_rows,
    enabled,
    evaluate,
    evaluator,
    lane_facts,
    op_facts,
    operators,
    snapshot,
)
from co.e2e.casespec.perm_check_read import read_subset, stray_codes
from co.e2e.perm_oracle import (
    ALL,
    MISSING,
    USED,
    LaneFacts,
    actual,
    compare_columns,
    compare_self,
    expected,
    expected_columns,
    functions_of,
    judge,
    one_problem,
    roles_of,
    roles_problem,
    summary,
)

_NO_SUCH = "zz-e2e-nosuch"
_UFSYS = "功能权限只核不变式（UFSYSTEM 回归账户不可读）"


def _need(env: Env) -> Operator:
    ev = evaluator(env)
    if ev is None:
        raise NoEvaluator(NO_EVALUATOR)
    return ev


def _check(env: Env, spec: CaseSpec, lane: LaneFacts, payload: dict, who: tuple[str, list[str] | None]) -> list[str]:
    """who：(操作员编码, 操作员档案里的角色；不在档案里为 None)。"""
    op, listed = who
    roles = roles_of(payload, listed)[0]
    supervisor = payload.get("supervisor") is True
    facts = None if supervisor else op_facts(env, spec, lane, op, roles)
    problems = judge(payload, lane, facts, listed is not None)
    cols = [] if supervisor else col_rows(env, spec, op, roles)
    problems += compare_columns(expected_columns(cols, supervisor), payload)
    extra = roles_problem(payload, listed)
    return problems + ([extra] if extra else [])


def all_ops(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    ev = _need(env)
    lane = lane_facts(env, spec)
    ops = enabled(operators(env, ev))
    bad: dict[str, list[str]] = {}
    supers = 0
    for op, roles in ops.items():
        payload = evaluate(env, ev, op)
        supers += payload.get("supervisor") is True
        problems = _check(env, spec, lane, payload, (op, roles))
        if problems:
            bad[op] = problems
    if bad:
        return "FAIL", summary(bad, len(ops))
    on = sorted(obj for obj in lane.on if obj in USED)
    return "PASS", f"{len(ops)} 个启用操作员（主管 {supers} 个）与账套库一致；读取用到的受控开关 {on or '无'}；{_UFSYS}"


def _super_problems(payload: dict) -> list[str]:
    problems = [f"{obj} 应为全部" for obj, got in actual(payload).items() if got not in (ALL, MISSING)]
    if not functions_of(payload):
        problems.append("functions 为空")
    return problems


def _self_super(env: Env, ev: Operator) -> str:
    status, _payload = call_as(env, env.operator, ARC_LIST, {"archive": "operator", "limit": 1})
    if status not in (200, 403):
        raise ValueError(f"测试操作员读操作员档案得 {status}")
    flag = evaluate(env, ev, env.operator.code).get("supervisor") is True
    if flag != (status == 200):
        return f"测试操作员读操作员档案得 {status}，evaluate 的 supervisor 却是 {flag}"
    return ""


def supervisor_all(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    ev = _need(env)
    ops = enabled(operators(env, ev))
    bad: dict[str, list[str]] = {}
    supers = 0
    for op in ops:
        payload = evaluate(env, ev, op)
        if payload.get("supervisor") is True:
            supers += 1
            problems = _super_problems(payload)
            if problems:
                bad[op] = problems
    mine = _self_super(env, ev)
    if bad or mine:
        return "FAIL", (mine + "　" + summary(bad, supers)).strip()
    if supers == 0:
        return "N/A", f"{len(ops)} 个启用操作员里没有账套主管；测试操作员读档案与 supervisor 一致"
    return "PASS", f"{supers}/{len(ops)} 个账套主管各对象都是全部、functions 非空；测试操作员读档案与 supervisor 一致"


def _empty_one(env: Env, spec: CaseSpec, lane: LaneFacts, ev: Operator, op: str) -> tuple[bool, list[str]]:
    status, payload = call_as(env, ev, EVAL, {"subject": op})
    if status == 404:
        return False, []
    if status != 200:
        return False, [f"evaluate 得 {status} {payload.get('code')}"]
    problems = _check(env, spec, lane, payload, (op, None))
    if payload.get("supervisor") is not False or functions_of(payload):
        problems.insert(0, "没有本账套授权，却不是 supervisor false、functions 为空")
    return True, problems


def empty_ops(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    ev = _need(env)
    lane = lane_facts(env, spec)
    listed = {str(item.get("code") or "").strip().upper() for item in operators(env, ev)}
    status, payload = call_as(env, ev, EVAL, {"subject": _NO_SUCH})
    if status != 404:
        return "FAIL", f"不存在的操作员 evaluate 应 404，实际 {status} {payload.get('code')}"
    bad: dict[str, list[str]] = {}
    seen = 0
    candidates, skipped = stray_codes(env, spec, listed)
    for op in candidates:
        found, problems = _empty_one(env, spec, lane, ev, op)
        seen += found
        if problems:
            bad[op] = problems
    if bad:
        return "FAIL", summary(bad, len(candidates))
    tail = f"（另有 {skipped} 个编码含其他字符，未核）" if skipped else ""
    return "PASS", f"不存在的操作员 404；{seen}/{len(candidates)} 个无本账套授权的操作员 functions 为空、数据权限与账套库一致" + tail


def _wh_want(env: Env, spec: CaseSpec, lane: LaneFacts, payload: dict, op_roles: tuple[str, list[str]]) -> object:
    if payload.get("supervisor") is True:
        return ALL
    roles = roles_of(payload, op_roles[1])[0]
    return expected(lane, op_facts(env, spec, lane, op_roles[0], roles), False)["warehouse"]


def warehouse(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    lane = lane_facts(env, spec)
    if "warehouse" not in lane.on:
        return "N/A", "本账套仓库数据权限没有打开"
    ev = _need(env)
    ops = enabled(operators(env, ev))
    bad: dict[str, list[str]] = {}
    ctl = 0
    for op, roles in ops.items():
        payload = evaluate(env, ev, op)
        want = _wh_want(env, spec, lane, payload, (op, roles))
        ctl += want != ALL
        problem = one_problem("warehouse", want, actual(payload)["warehouse"])
        if problem:
            bad[op] = [problem]
    if bad:
        return "FAIL", summary(bad, len(ops))
    return "PASS", f"{len(ops)} 个启用操作员的仓库权限与账套库一致，其中 {ctl} 个受控"


def _selves(env: Env, ev: Operator | None) -> list[Operator]:
    found = [env.operator]
    if ev is not None and ev.code.upper() != env.operator.code.upper():
        found.append(ev)
    return found


def _listed_roles(env: Env, ev: Operator | None) -> dict[str, list[str]]:
    try:
        return {code.upper(): roles for code, roles in enabled(operators(env, ev)).items()}
    except ValueError:
        return {}


def snapshot_self(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    ev = evaluator(env)
    lane = lane_facts(env, spec)
    roles = _listed_roles(env, ev)
    bad: dict[str, list[str]] = {}
    for op in _selves(env, ev):
        snap = snapshot(env, op)
        problems = _check(env, spec, lane, snap, (op.code, roles.get(op.code.upper())))
        if ev is not None:
            problems += compare_self(snap, evaluate(env, ev, op.code))
        if problems:
            bad[op.code] = problems
    if bad:
        return "FAIL", summary(bad, len(_selves(env, ev)))
    tail = "，与 evaluate 完全相同（含指纹）" if ev is not None else "；" + NO_EVALUATOR + "，没有比对 evaluate"
    return "PASS", f"{len(_selves(env, ev))} 个操作员自己的 snapshot 与账套库一致" + tail


def eval_denied(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    ev = _need(env)
    if ev.code.upper() == env.operator.code.upper():
        return "N/A", "评估操作员就是测试操作员，没有名单外的调用方"
    for subject in (ev.code, env.operator.code):
        status, payload = call_as(env, env.operator, EVAL, {"subject": subject})
        if status != 403:
            return "FAIL", f"名单外的测试操作员 evaluate {subject} 应 403，实际 {status} {payload.get('code')}"
    return "PASS", "名单外的测试操作员（不论是否账套主管）evaluate 得 403"


def read_subset_op(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return read_subset(env, _selves(env, evaluator(env)))


def _guarded(op):
    """没有配置评估操作员时记 N/A。"""

    def run(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
        try:
            return op(env, spec, step)
        except NoEvaluator:
            return "N/A", NO_EVALUATOR

    return run


PERM_OPS = {
    "perm_all_ops": _guarded(all_ops),
    "perm_supervisor_all": _guarded(supervisor_all),
    "perm_empty_ops": _guarded(empty_ops),
    "perm_warehouse": _guarded(warehouse),
    "perm_snapshot_self": _guarded(snapshot_self),
    "perm_eval_denied": _guarded(eval_denied),
    "perm_read_subset": _guarded(read_subset_op),
}
_ARC_TAGS = frozenset(("perm", "arc-read:customer", "arc-read:vendor", "arc-read:inventory", "arc-read:warehouse",
                       "arc-read:department", "arc-read:person", "arc-read:project", "arc-read:sale_type",
                       "arc-read:purchase_type", "arc-read:rd_style", "arc-read:voucher_sign"))


def perm_cases() -> list[CaseSpec]:
    def case(name: str, title: str, op: str, routes: tuple[str, ...], tags: frozenset[str] = frozenset(("perm",))) -> CaseSpec:
        step = Step("custom", sql_name=op, id_from="none")
        return CaseSpec(name, title, (), (step,), True, optional=True, tags=tags, routes=routes)

    return [
        case("perm-oracle-all-ops", "全部启用操作员的 evaluate 与账套库独立算出的数据权限一致", "perm_all_ops",
             (EVAL, ARC_LIST)),
        case("perm-supervisor-all", "账套主管各对象都是全部，主管标志与操作员档案的主管闸门一致", "perm_supervisor_all",
             (EVAL, ARC_LIST)),
        case("perm-empty-ops", "无本账套授权的操作员 functions 为空、受控对象按账套库（没有编码就看不到）", "perm_empty_ops",
             (EVAL, ARC_LIST)),
        case("perm-wh-control", "仓库数据权限打开时逐个操作员的仓库编码与账套库一致", "perm_warehouse", (EVAL, ARC_LIST)),
        case("perm-snapshot-self", "自己的 snapshot 与账套库一致，并与 evaluate 完全相同", "perm_snapshot_self",
             (SNAP, EVAL, ARC_LIST)),
        case("perm-eval-denied", "名单外的调用方 evaluate 得 403", "perm_eval_denied", (EVAL,)),
        case("perm-read-subset", "受控对象的档案列表每一行都在自己 snapshot 的编码里", "perm_read_subset",
             (SNAP, ARC_LIST), _ARC_TAGS),
    ]
