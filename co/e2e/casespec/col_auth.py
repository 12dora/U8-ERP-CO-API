"""字段权限遮蔽：给 account.perm_operator_file 的非主管操作员临时加 AA_ColumnAuth 不可见行，结束删掉。

桥权限缓存没关（健康检查 perm_cache_s 不是 0）或没配该文件记 N/A，不写任何东西；缓存关着时不等，改完立刻生效。
Env.operator 仍是账套操作员，桥调用走 call_as。没有 AA_BusObject_base 对象行记 N/A（不插入）；
开关关着则 UPDATE 打开，结束按原值恢复。本人已有字段权限行或仍是账套主管也记 N/A。独占。
收尾 DELETE / 恢复开关走 restore_row（绕过 stop，尊重 cleanup_closed，仍过 sqlguard）。
col-auth-mask-sale-out：0303 iPrice、iUnitCost；col-auth-mask-purchase-in：24 iPrice、iorimoney、isum；
col-auth-mgmt-cogs：0303 iPrice → mgmt/sales cogs。步骤 kind 为 custom（COL_AUTH_OPS 并入 ops_run）。
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from co.e2e.cases import Operator, load_operator
from co.e2e.casespec.env import Env
from co.e2e.casespec.gate import stopped
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.perm_cache import NOT_OFF, is_off
from co.e2e.casespec.flip_util import Fail, brief, row, with_restore
from co.e2e.casespec.col_auth_sql import (
    delete_sql, existing_sql, insert_sql, switch_on_sql, switch_restore_sql, switch_sql,
)
from co.e2e.casespec.perm_check_io import call_as
from co.e2e.settings import conf

NO_PERM_OP = "未配置非主管测试操作员"
NO_SWITCH_ROW = "没有字段权限对象行"

LOAD = "/v1/vouchers/load"
LIST = "/v1/vouchers/list"
SEARCH = "/v1/vouchers/search"
SNAP = "/v1/perm/snapshot"
SALES = "/v1/reports/mgmt/sales"
_PICK = 10


@dataclass(frozen=True)
class Plan:
    key: str
    fields: tuple[str, ...]
    kind: str


PLANS = {
    "col_auth_sale_out": Plan("0303", ("iPrice", "iUnitCost"), "sale_out"),
    "col_auth_purchase_in": Plan("24", ("iPrice", "iorimoney", "isum"), "purchase_in"),
    "col_auth_mgmt_cogs": Plan("0303", ("iPrice",), ""),
}


def values_of(node: object, names: frozenset[str]) -> dict[str, list[object]]:
    """整个响应里键名（不分大小写）在 names（大写）里的全部取值：大写键名 → 值列表。"""
    found: dict[str, list[object]] = {}
    stack = [node]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            for key, value in cur.items():
                name = str(key).upper()
                if name in names:
                    found.setdefault(name, []).append(value)
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(cur, list):
            stack.extend(cur)
    return found


def shown(node: object, names: frozenset[str]) -> frozenset[str]:
    """names 里在响应中至少有一处非 null 值的字段（大写）。"""
    return frozenset(name for name, vals in values_of(node, names).items() if any(v is not None for v in vals))


def masked_of(payload: dict) -> frozenset[str]:
    got = payload.get("masked_fields")
    return frozenset(str(item).upper() for item in got) if isinstance(got, list) else frozenset()


def upper(fields: tuple[str, ...]) -> frozenset[str]:
    return frozenset(f.upper() for f in fields)


class _NoStop:
    """让 stopped(ctx) 为假。只包住临时字段权限行的收尾 DELETE，不改共享的 Run.stop。"""

    def __init__(self, ctx: object) -> None:
        object.__setattr__(self, "_ctx", ctx)
        self.stop = None

    def __getattr__(self, name: str):
        return getattr(self._ctx, name)


def _cleanup_closed(ctx: object) -> bool:
    flag = getattr(ctx, "cleanup_closed", None)
    return isinstance(flag, threading.Event) and flag.is_set()


def restore_row(env: Env, spec: CaseSpec, query: str, closed: str = "已进入快照还原，临时字段权限行留给还原") -> list[str] | str:
    """同 row()，但收尾语句绕过中断门禁。已进入快照还原则不发 SQL，返回问题说明。"""
    inner = env.ctx
    if _cleanup_closed(inner):
        return closed
    env.ctx = _NoStop(inner)
    try:
        return row(env, spec, query)
    finally:
        env.ctx = inner


def perm_operator(env: Env) -> Operator | None:
    """字段权限用例专用的非主管操作员；没配返回 None。口令进 needles。"""
    path = conf().perm_operator_file
    if not path:
        return None
    op = load_operator(Path(path))
    needles = env.ctx.needles
    if op.password not in needles:
        needles.append(op.password)
    return op


def send(env: Env, route: str, extra: dict) -> tuple[int, dict]:
    op = perm_operator(env)
    if op is None:
        raise Fail(NO_PERM_OP)
    return call_as(env, op, route, extra)


def ok(env: Env, route: str, extra: dict, what: str) -> dict:
    status, payload = send(env, route, extra)
    if status != 200:
        raise Fail(f"{what}应 200，实际 " + brief(status, payload))
    return payload


def columns_problem(payload: dict, plan: Plan) -> str:
    """snapshot 的 columns[对象号] 含全部插入的字段（对象号、字段名不分大小写，字段去「B;」前缀）。"""
    cols = payload.get("columns")
    if not isinstance(cols, dict):
        return "perm/snapshot 缺 columns"
    got: set[str] = set()
    for key, flds in cols.items():
        if str(key).strip().upper() == plan.key.upper() and isinstance(flds, list):
            got.update(str(f).rpartition(";")[2].strip().upper() for f in flds)
    missing = sorted(upper(plan.fields) - got)
    return f"perm/snapshot 的 columns[{plan.key}] 缺 {missing}" if missing else ""


def mask_problem(payload: dict, names: frozenset[str], need: frozenset[str], what: str) -> str:
    """names 在响应里全为 null；need（基线有值的字段）都列进 masked_fields。"""
    leaked = sorted(shown(payload, names))
    if leaked:
        return f"{what}仍返回 {leaked} 的值"
    missing = sorted(need - masked_of(payload))
    return f"{what}的 masked_fields 缺 {missing}" if missing else ""


class _Case:
    def __init__(self, env: Env, spec: CaseSpec, plan: Plan) -> None:
        self.env = env
        self.spec = spec
        self.plan = plan
        self.op = env.operator.code.strip()
        self.names = upper(plan.fields)
        self.switch_was: int | None = None

    def not_ready(self) -> str:
        """前提不满足时的 N/A 原因；满足时为空串。先核对桥权限缓存已关闭，再碰任何数据。"""
        if not is_off(self.env):
            return NOT_OFF
        actor = perm_operator(self.env)
        if actor is None:
            return NO_PERM_OP
        self.op = actor.code.strip()
        got = row(self.env, self.spec, switch_sql(self.plan.key))
        if len(got) != 2 or got[0] in ("", "0"):
            return f"本账套对象 {self.plan.key} {NO_SWITCH_ROW}"
        self.switch_was = 0 if got[1] in ("", "0") else 1
        status, snap = send(self.env, SNAP, {})
        if status != 200:
            raise ValueError(f"perm/snapshot 得 {status} {snap.get('code')}")
        if snap.get("supervisor") is True:
            return "测试操作员是账套主管，字段权限不生效"
        left = row(self.env, self.spec, existing_sql(self.op, self.plan.key, self.plan.fields))
        if left != ["0"]:
            return f"测试操作员在 {self.plan.key} 的这些字段上已有字段权限行（{left}，可能是上次中断留下的），不改动，请人工核对"
        return ""

    def ensure_switch(self) -> None:
        if self.switch_was != 0:
            return
        got = row(self.env, self.spec, switch_on_sql(self.plan.key))
        if not got or got[0] in ("", "0"):
            raise Fail(f"打开对象 {self.plan.key} 的字段权限开关未改到行")

    def apply(self) -> None:
        self.ensure_switch()
        self.insert()

    def insert(self) -> None:
        got = row(self.env, self.spec, insert_sql(self.op, self.plan.key, self.plan.fields))
        if got[:1] != ["OK"] or got[1:2] != [str(len(self.plan.fields))]:
            raise Fail(f"插入字段权限行失败（已回滚）：{'|'.join(got) or '没有结果'}"[:240])

    def delete(self) -> str:
        got = restore_row(self.env, self.spec, delete_sql(self.op, self.plan.key, self.plan.fields))
        if isinstance(got, str):
            return got
        if len(got) != 2 or got[1] != "0":
            return f"删除临时字段权限行后应剩 0 行，实际 {got}（留给快照还原）"
        return ""

    def restore_switch(self) -> str:
        if self.switch_was != 0:
            return ""
        got = restore_row(self.env, self.spec, switch_restore_sql(self.plan.key, 0),
                          "已进入快照还原，字段权限开关留给还原")
        if isinstance(got, str):
            return got
        return ""

    def snapshot_problem(self) -> str:
        return columns_problem(ok(self.env, SNAP, {}, "perm/snapshot "), self.plan)


def _undo(case: _Case, again: Callable[[], str]) -> str:
    """删临时行、恢复开关并确认剩 0 行。中断后不核对字段恢复。"""
    problem = case.delete()
    extra = case.restore_switch()
    if problem:
        return problem
    if extra:
        return extra
    if stopped(case.env.ctx):
        return ""
    return again()


def _run(case: _Case, check: Callable[[], str], again: Callable[[], str]) -> tuple[str, str]:
    """打开开关（若关着）→ 插入 → check → 删行 → 恢复开关 → again（缓存已关，不等）。"""

    def body() -> tuple[str, str]:
        case.apply()
        detail = check()
        problem = case.snapshot_problem()
        if problem:
            raise Fail(problem)
        return "PASS", detail

    return with_restore(body, (("删除临时字段权限行并确认字段恢复", lambda: _undo(case, again)),))


# ---- 单据：vouchers/load、list、search ----


def _load(case: _Case, doc_id: int) -> dict:
    return ok(case.env, LOAD, {"type": case.plan.kind, "id": doc_id}, f"读取 {case.plan.kind} {doc_id} ")


def _pick(case: _Case) -> tuple[int, frozenset[str]]:
    """列表前几张里第一张这些字段有值、又没有被遮蔽的单据：(id, 有值的字段)；没有时 id 为 0。"""
    listed = ok(case.env, LIST, {"type": case.plan.kind, "limit": _PICK}, f"{case.plan.kind} 列表 ")
    for item in listed.get("items") or []:
        doc_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(doc_id, int) or isinstance(doc_id, bool):
            continue
        payload = _load(case, doc_id)
        got = shown(payload, case.names)
        if got and not masked_of(payload) & case.names:
            return doc_id, got
    return 0, frozenset()


def _list_problem(case: _Case) -> str:
    for route, what in ((LIST, "列表"), (SEARCH, "查询")):
        payload = ok(case.env, route, {"type": case.plan.kind, "limit": 20}, f"{case.plan.kind} {what} ")
        leaked = sorted(shown(payload, case.names))
        if leaked:
            return f"{case.plan.kind} {what}仍返回 {leaked} 的值"
    return ""


def voucher_mask(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    case = _Case(env, spec, PLANS[step.sql_name])
    reason = case.not_ready()
    if reason:
        return "N/A", reason
    doc_id, need = _pick(case)
    if not doc_id:
        return "N/A", f"{case.plan.kind} 列表前 {_PICK} 张单据都没有 {list(case.plan.fields)} 的值（或已被遮蔽）"
    label = f"{case.plan.kind} {doc_id}"

    def check() -> str:
        problem = mask_problem(_load(case, doc_id), case.names, need, f"读取 {label} ") or _list_problem(case)
        if problem:
            raise Fail(problem)
        return f"{label}：{case.plan.key} 的 {sorted(need)} 置空并列进 masked_fields，列表、查询 200 无这些值，snapshot columns 一致"

    def again() -> str:
        payload = _load(case, doc_id)
        back = shown(payload, case.names)
        if need - back or masked_of(payload) & case.names:
            return f"删除字段权限行后 {label} 的 {sorted(need - back)} 仍没有值或仍在 masked_fields"
        return ""

    return _run(case, check, again)


# ---- 经营报表：mgmt/sales 的 cogs ----

_COGS = frozenset(("COGS",))
_QTY = frozenset(("QTY",))


def _sales(case: _Case) -> dict:
    month = int(str(case.env.today)[5:7])
    body = {"period_from": 1, "period_to": month, "group_by": ["customer", "inventory"]}
    return ok(case.env, SALES, body, "mgmt/sales ")


def _has_cogs(payload: dict) -> bool:
    return any(isinstance(i, dict) and i.get("cogs") not in (None, 0) for i in payload.get("items") or [])


def mgmt_cogs(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    case = _Case(env, spec, PLANS[step.sql_name])
    reason = case.not_ready()
    if reason:
        return "N/A", reason
    base = _sales(case)
    if base.get("sa_enabled") is False or not _has_cogs(base) or masked_of(base) & _COGS:
        return "N/A", "mgmt/sales 本年没有非零的销售成本（或已被遮蔽），无从核对"
    qty = bool(shown(base, _QTY))

    def check() -> str:
        payload = _sales(case)
        problem = mask_problem(payload, _COGS, _COGS, "mgmt/sales ")
        if not problem and qty and not shown(payload, _QTY):
            problem = "mgmt/sales 的 qty 也被置空（只应遮蔽金额）"
        if problem:
            raise Fail(problem)
        return "0303 iPrice 不可见时 mgmt/sales 的 cogs 全部置空并列进 masked_fields，qty 照常，snapshot columns 一致"

    def again() -> str:
        return "" if _has_cogs(_sales(case)) else "删除字段权限行后 mgmt/sales 的 cogs 仍没有值"

    return _run(case, check, again)


def _guarded(op: Callable[[Env, CaseSpec, Step], tuple[str, str]]) -> Callable[[Env, CaseSpec, Step], tuple[str, str]]:
    """改动之前（挑单、基线）的 Fail 记 FAIL；改动之后的由 with_restore 处理。"""

    def run(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
        try:
            return op(env, spec, step)
        except Fail as exc:
            return "FAIL", str(exc)[:240]

    return run


COL_AUTH_OPS = {
    "col_auth_sale_out": _guarded(voucher_mask),
    "col_auth_purchase_in": _guarded(voucher_mask),
    "col_auth_mgmt_cogs": _guarded(mgmt_cogs),
}


def col_auth_cases() -> list[CaseSpec]:
    def case(name: str, title: str, op: str, routes: tuple[str, ...]) -> CaseSpec:
        step = Step("custom", sql_name=op, id_from="none")
        return CaseSpec(name, title, (), (step,), True, optional=True, tags=frozenset(("perm", "col-auth")),
                        routes=routes, exclusive=True)

    return [
        case("col-auth-mask-sale-out", "字段权限：0303 单价、成本不可见时销售出库单读取置空并列进 masked_fields，结束删行",
             "col_auth_sale_out", (LOAD, LIST, SEARCH, SNAP)),
        case("col-auth-mask-purchase-in", "字段权限：24 单价、金额不可见时采购入库单读取置空并列进 masked_fields，结束删行",
             "col_auth_purchase_in", (LOAD, LIST, SEARCH, SNAP)),
        case("col-auth-mgmt-cogs", "字段权限：0303 单价不可见时经营报表销售成本置空、数量照常，结束删行",
             "col_auth_mgmt_cogs", (SALES, SNAP)),
    ]
