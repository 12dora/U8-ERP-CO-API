"""客户收货地址 `archive-customer-address`：挑一个有收货地址的客户，快照它全部 CusDeliverAdd 行和 Customer 整行 →
customer_address 改名称、收货单位、默认地址、地址编码 → 每步整户地址行只允许出现预期的变化，响应的 before / after
和库里逐行一致 → 全部改回，用例结束时整户地址行与快照逐格一致，客户行（含修改人、日期、行版本）不变。

编码改动需要一条没有单据引用的地址：用例在测试账套上用 SQL 插一条临时地址，结束前自己删掉。
否定路径同用例：预演不改行、no-op、缺 expect 400、expect 不符 409、地址 / 客户不存在 404、
目标编码已存在 409 address_code_taken、被发货单引用的编码 409 address_in_use。
步骤 kind 为 core_cusaddr，挂在 arc_write.CORE_HANDLERS。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.arc_cus_bodies import (
    Dump,
    addr_body,
    cell_text,
    default_edits,
    default_ids,
    dump_diff,
    dump_problem,
    edited,
    load_row,
    other_text,
    parse_dump,
    reply_addr,
    reply_addr_dry,
    row_of,
    sequence,
)
from co.e2e.casespec.arc_cus_person_bodies import changed, expect, send
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_cus_sql import (
    addr_count_sql,
    addr_customer_sql,
    addr_delete_sql,
    addr_dump_sql,
    addr_insert_sql,
    addr_reset_sql,
    addr_used_sql,
    cus_row_sql,
)
from co.e2e.casespec.gl_voucher_sql import safe_code
from co.e2e.casespec.sqlexec import run_guarded

_NAME = "archive-customer-address"
_ROUTES = ("/v1/archives/customer_address",)
_OP = "customer_address"
_MISSING_CUS = "ZZNOCUS01"
_OK = (200, "")
_TEXT = {"name": ("cDeliverAdd", "E2E改名"), "unit": ("cDeliverUnit", "E2E改单位")}
_TEXT_LIMIT = 255


class _NotApplicable(Exception):
    """账套里没有这个用例需要的数据：记 N/A，不是失败。"""


@dataclass
class _State:
    code: str = ""
    snap: Dump = field(default_factory=dict)
    cur: Dump = field(default_factory=dict)
    cus: dict[str, str] = field(default_factory=dict)
    rid: str = ""
    temp: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dirty: bool = False


@dataclass(frozen=True)
class _Plan:
    label: str
    address: str
    fields: dict
    expect: dict | None
    edits: dict
    new_addr: str = ""


@dataclass(frozen=True)
class _Call:
    code: str
    address: str
    fields: dict
    expect: dict | None
    want: tuple[int, str]
    label: str


def _sql(env: Env, spec: CaseSpec, query: str) -> str:
    return first_cell(run_guarded(env, query, spec.name)).strip()


def _dump(env: Env, spec: CaseSpec, code: str) -> Dump:
    return parse_dump(sql_cells(run_guarded(env, addr_dump_sql(code), spec.name)))


def _free_code(env: Env, spec: CaseSpec, code: str, head: str) -> str:
    """该客户下还没有的编码，挑不到返回空串。"""
    for _ in range(3):
        cand = head + os.urandom(3).hex()
        if _sql(env, spec, addr_count_sql(code, cand)) == "0":
            return cand
    return ""


def _post(env: Env, body: dict, want: tuple[int, str], label: str) -> tuple[str, dict]:
    status, payload = send(env, _OP, body)
    return expect(status, payload, want, label), payload


def _addr(state: _State, rid: str = "") -> str:
    return cell_text(state.cur, rid or state.rid, "cAddCode")


def _bind(env: Env, spec: CaseSpec, state: _State) -> str:
    try:
        state.code = safe_code(_sql(env, spec, addr_customer_sql()))
    except ValueError:
        raise _NotApplicable("账套没有带收货地址的客户") from None
    state.snap = _dump(env, spec, state.code)
    state.cur = edited(state.snap, {})
    state.cus = load_row(sql_cells(run_guarded(env, cus_row_sql(state.code), spec.name)))
    for rid in sorted(state.snap, key=int):
        text = state.snap[rid].get("cAddCode", "")
        if 1 <= len(text.strip()) <= 30 and text == text.strip():
            state.rid = rid
            return ""
    raise _NotApplicable("客户的收货地址编码都不合规，路由无法定位")


def _text_of(state: _State, name: str) -> tuple[str, str, str]:
    col, tag = _TEXT[name]
    orig = cell_text(state.cur, state.rid, col)
    return col, orig, other_text(orig, tag, _TEXT_LIMIT)


def _dry_run(env: Env, spec: CaseSpec, state: _State) -> str:
    _col, orig, new = _text_of(state, "name")
    body = addr_body(state.code, _addr(state), {"name": new}, {"name": orig}, True)
    bad, payload = _post(env, body, _OK, "预演改名称")
    return bad or reply_addr_dry(payload, state.code, state.cur) or dump_problem(state.cur, _dump(env, spec, state.code))


def _apply(env: Env, spec: CaseSpec, state: _State, plan: _Plan) -> str:
    state.dirty = True
    body = addr_body(state.code, plan.address, plan.fields, plan.expect)
    bad, payload = _post(env, body, _OK, plan.label)
    if bad:
        return bad
    after = _dump(env, spec, state.code)
    bad = reply_addr(payload, (state.code, plan.new_addr), True, (state.cur, after))
    bad = bad or dump_problem(edited(state.cur, plan.edits), after)
    state.cur = after
    return f"{plan.label}：{bad}"[:200] if bad else ""


def _text_cycle(env: Env, spec: CaseSpec, state: _State, name: str) -> str:
    """名称（改名再改回）或收货单位（设值、清空、回原值）。"""
    col, orig, new = _text_of(state, name)
    for value in sequence(new, orig, name == "unit"):
        now = cell_text(state.cur, state.rid, col)
        plan = _Plan(f"{name} 设为 {value!r}", _addr(state), {name: value}, {name: now}, {state.rid: {col: value}})
        bad = _apply(env, spec, state, plan)
        if bad:
            return bad
    return ""


def _flag(state: _State, rid: str) -> bool:
    return cell_text(state.cur, rid, "bDefault") == "1"


def _default_plan(state: _State, rid: str, flag: bool) -> _Plan:
    label = f"默认地址设为 {flag}"
    edits = default_edits(state.cur, rid, flag)
    return _Plan(label, _addr(state, rid), {"default": flag}, {"default": _flag(state, rid)}, edits)


def _flip(env: Env, spec: CaseSpec, state: _State) -> str:
    ids = sorted(state.cur, key=int)
    held = default_ids(state.cur)
    if len(ids) < 2 or len(held) > 1:
        state.notes.append("默认地址切换（客户只有一条地址或默认地址不唯一）")
        return ""
    target = next((rid for rid in ids if rid not in held), ids[0])
    bad = _apply(env, spec, state, _default_plan(state, target, True))
    if bad:
        return bad
    # 改回：原来有默认地址就把它重新设为默认（同户其他行自动清零），原来没有就把这一行设为否。
    return _apply(env, spec, state, _default_plan(state, held[0], True) if held else _default_plan(state, target, False))


def _noop(env: Env, spec: CaseSpec, state: _State) -> str:
    now = cell_text(state.cur, state.rid, "cDeliverAdd")
    body = addr_body(state.code, _addr(state), {"name": now}, {"name": now})
    bad, payload = _post(env, body, _OK, "no-op")
    after = _dump(env, spec, state.code)
    return bad or reply_addr(payload, (state.code, ""), False, (state.cur, after)) or dump_problem(state.cur, after)


def _reject(env: Env, spec: CaseSpec, call: _Call, before: Dump | None) -> str:
    body = addr_body(call.code, call.address, call.fields, call.expect)
    bad, _payload = _post(env, body, call.want, call.label)
    if bad or before is None:
        return bad
    bad = dump_problem(before, _dump(env, spec, call.code))
    return f"{call.label}：{bad}"[:200] if bad else ""


def _rejects(env: Env, spec: CaseSpec, state: _State) -> str:
    _col, orig, new = _text_of(state, "name")
    here, addr = state.code, _addr(state)
    ghost = _free_code(env, spec, here, "ZZNOA")
    calls = [
        _Call(here, addr, {"name": new}, None, (400, ""), "缺 expect"),
        _Call(here, addr, {"name": new}, {"name": orig + "~不符"}, (409, "state_mismatch"), "expect 不符"),
        _Call(here, addr, {"default": True}, {}, (400, ""), "default 缺 expect"),
    ]
    if ghost:
        calls.append(_Call(here, ghost, {"name": new}, {"name": ""}, (404, "address_not_found"), "地址不存在"))
    for call in calls:
        bad = _reject(env, spec, call, state.cur)
        if bad:
            return bad
    missing = _Call(_MISSING_CUS, addr, {"name": new}, {"name": ""}, (404, ""), "客户不存在")
    return _reject(env, spec, missing, None)


def _make_temp(env: Env, spec: CaseSpec, state: _State) -> str:
    """插一条没有单据引用的临时地址（默认为否）；插不进去就跳过改编码的正向用例。"""
    code = _free_code(env, spec, state.code, "E2E")
    if not code:
        state.notes.append("改编码（挑不到空闲的临时编码）")
        return ""
    state.temp.append(code)
    try:
        run_guarded(env, addr_insert_sql(state.code, code), spec.name)
        placed = _sql(env, spec, addr_count_sql(state.code, code)) == "1"
    except RuntimeError:
        placed = False
    if not placed:
        state.notes.append("改编码（账套不允许插入临时地址行）")
        return ""
    state.dirty = True
    state.cur = _dump(env, spec, state.code)
    return ""


def _recode(env: Env, spec: CaseSpec, state: _State) -> str:
    first = state.temp[0] if state.temp else ""
    rid = row_of(state.cur, first) if first else ""
    second = _free_code(env, spec, state.code, "E2F") if rid else ""
    if not second:
        return ""
    state.temp.append(second)
    for old, new in ((first, second), (second, first)):
        plan = _Plan(f"地址编码 {old} 改成 {new}", old, {"code": new}, None, {rid: {"cAddCode": new}}, new)
        bad = _apply(env, spec, state, plan)
        if bad:
            return bad
    taken = _Call(state.code, first, {"code": _addr(state)}, None, (409, "address_code_taken"), "目标编码已存在")
    return _reject(env, spec, taken, state.cur)


def _in_use(env: Env, spec: CaseSpec, state: _State) -> str:
    """被发货单引用的地址不能改编码（可能是另一个客户的地址；被拒，不写库）。"""
    cell = first_cell(run_guarded(env, addr_used_sql(), spec.name))
    owner, _sep, addr = cell.partition("\t")
    try:
        owner = safe_code(owner.strip())
    except ValueError:
        state.notes.append("被发货单引用的地址编码改不了（账套没有被发货单引用的收货地址）")
        return ""
    fresh = _free_code(env, spec, owner, "E2G")
    if not fresh or not addr.strip():
        return ""
    call = _Call(owner, addr.strip(), {"code": fresh}, None, (409, "address_in_use"), "被单据引用的编码")
    return _reject(env, spec, call, _dump(env, spec, owner))


def _cycle(env: Env, spec: CaseSpec, state: _State) -> str:
    steps = (
        lambda: _bind(env, spec, state),
        lambda: _dry_run(env, spec, state),
        lambda: _text_cycle(env, spec, state, "name"),
        lambda: _text_cycle(env, spec, state, "unit"),
        lambda: _flip(env, spec, state),
        lambda: _noop(env, spec, state),
        lambda: _rejects(env, spec, state),
        lambda: _make_temp(env, spec, state),
        lambda: _recode(env, spec, state),
        lambda: _in_use(env, spec, state),
    )
    for run in steps:
        bad = run()
        if bad:
            return bad
    return ""


def _reset_rows(env: Env, spec: CaseSpec, state: _State, now: Dump) -> None:
    """路由还原不了时的兜底（测试账套上）：按行号把四列写回快照值，不让差异留在账套里。"""
    for rid, cols in state.snap.items():
        if rid in now:
            run_guarded(env, addr_reset_sql(state.code, rid, cols), spec.name)


def _restore(env: Env, spec: CaseSpec, state: _State) -> str:
    if not state.snap:
        return ""
    if state.temp:
        run_guarded(env, addr_delete_sql(state.code, tuple(state.temp)), spec.name)
    if not state.dirty:
        return ""
    now = _dump(env, spec, state.code)
    hits = dump_diff(state.snap, now)
    if hits:
        _reset_rows(env, spec, state, now)
        return f"还原后收货地址行仍有差异 {hits}"[:200]
    row = load_row(sql_cells(run_guarded(env, cus_row_sql(state.code), spec.name)))
    left = changed(state.cus, row, frozenset())
    return f"客户行不应被收货地址写入改动 {left}"[:200] if left else ""


def _guarded(step: Callable[[], str]) -> str:
    try:
        return step()
    except (RuntimeError, ValueError) as exc:
        return str(exc)[:200]


def _verdict(problem: str, undo: str, note: str, state: _State) -> tuple[str, str]:
    if problem or undo:
        return "FAIL", "；".join(item for item in (undo, problem) if item)[:240]
    if note:
        return "N/A", note
    skipped = "；跳过：" + "、".join(state.notes) if state.notes else ""
    return "PASS", f"customer {state.code} address{skipped}"[:240]


def step_cusaddr(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    state = _State()
    note = ""
    try:
        problem = _cycle(env, spec, state)
    except _NotApplicable as exc:
        problem, note = "", str(exc)
    except (RuntimeError, ValueError) as exc:
        problem = str(exc)[:200]
    undo = _guarded(lambda: _restore(env, spec, state))
    return _verdict(problem, undo, note, state)


CUSADDR_HANDLERS = {"core_cusaddr": step_cusaddr}


def cusaddr_cases() -> list[CaseSpec]:
    step = Step("core_cusaddr", id_from="none")
    title = "客户收货地址改名称、收货单位、默认地址和编码并还原"
    tags = frozenset({"arc:customer", "arc:write"})
    return [CaseSpec(_NAME, title, (), (step,), True, tags=tags, routes=_ROUTES)]
