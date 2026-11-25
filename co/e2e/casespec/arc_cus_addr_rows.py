"""客户收货地址新增与删除 `archive-customer-address-rows`：挑一个有收货地址的客户，快照它全部 CusDeliverAdd 行和 Customer 整行 →
customer_address_add 新增一条地址（核对新行各列、GUID、其余行不变）→ 重放同一请求 no-op → 编码被占用 / 名称被占用 / 参数不合法 / 客户不存在被拒 →
再新增一条默认地址（同户其余地址的默认标志清零）→ customer_address_delete 的各种拒绝（缺 expect、expect 不符、地址或客户不存在、
默认地址且有其他地址、被发货单引用）→ 把默认标志移回原处 → 带 expect 删除两条新地址 → 再删一次得 404。
用例结束时整户地址行与快照逐格一致，客户行（含修改人、日期、行版本）不变。

预演不改行。临时地址编码在客户下选未占用的；中途失败时按 SQL 删掉临时行、按快照写回四个可改列。
步骤 kind 为 core_cusrows，挂在 arc_write.CORE_HANDLERS。
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
    parse_dump,
    reply_addr,
    reply_addr_dry,
)
from co.e2e.casespec.arc_cus_addr_rows_bodies import (
    Call,
    add_body,
    add_calls,
    added_problem,
    clean_codes,
    delete_body,
    delete_calls,
    expect_of,
    reply_rows,
    without,
)
from co.e2e.casespec.arc_cus_person_bodies import changed, expect, send
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_cus_sql import (
    addr_count_sql,
    addr_customer_sql,
    addr_delete_sql,
    addr_dump_sql,
    addr_reset_sql,
    cus_row_sql,
)
from co.e2e.casespec.arc_cus_addr_rows_sql import addr_used_free_sql
from co.e2e.casespec.gl_voucher_sql import safe_code
from co.e2e.casespec.sqlexec import run_guarded

_NAME = "archive-customer-address-rows"
_ROUTES = (
    "/v1/archives/customer_address_add",
    "/v1/archives/customer_address_delete",
    "/v1/archives/customer_address",
)
_ADD, _DEL, _MOVE = "customer_address_add", "customer_address_delete", "customer_address"
_OK = (200, "")


class _NotApplicable(Exception):
    """账套里没有这个用例需要的数据：记 N/A，不是失败。"""


@dataclass
class _State:
    code: str = ""
    snap: Dump = field(default_factory=dict)
    cur: Dump = field(default_factory=dict)
    cus: dict[str, str] = field(default_factory=dict)
    addr: dict[str, str] = field(default_factory=dict)
    names: dict[str, str] = field(default_factory=dict)
    temp: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    dirty: bool = False


def _sql(env: Env, spec: CaseSpec, query: str) -> str:
    return first_cell(run_guarded(env, query, spec.name)).strip()


def _dump(env: Env, spec: CaseSpec, code: str) -> Dump:
    return parse_dump(sql_cells(run_guarded(env, addr_dump_sql(code), spec.name)))


def _free_code(env: Env, spec: CaseSpec, state: _State, head: str) -> str:
    for _ in range(3):
        cand = head + os.urandom(3).hex()
        if _sql(env, spec, addr_count_sql(state.code, cand)) == "0":
            return cand
    return ""


def _unused_name(state: _State, tag: str) -> str:
    taken = {cell_text(state.snap, rid, "cDeliverAdd") for rid in state.snap}
    for _ in range(3):
        cand = tag + os.urandom(2).hex()
        if cand not in taken:
            return cand
    return tag + os.urandom(6).hex()


def _post(env: Env, call: Call) -> tuple[str, dict]:
    status, payload = send(env, call.op, call.body)
    return expect(status, payload, call.want, call.label), payload


def _bind(env: Env, spec: CaseSpec, state: _State) -> str:
    try:
        state.code = safe_code(_sql(env, spec, addr_customer_sql()))
    except ValueError:
        raise _NotApplicable("账套没有带收货地址的客户") from None
    state.snap = _dump(env, spec, state.code)
    state.cur = edited(state.snap, {})
    state.cus = load_row(sql_cells(run_guarded(env, cus_row_sql(state.code), spec.name)))
    for key, head in (("a", "E2H"), ("b", "E2I"), ("g", "E2G")):
        state.addr[key] = _free_code(env, spec, state, head)
        state.names[key] = _unused_name(state, "E2E新增" + key)
    if not all(state.addr.values()):
        raise _NotApplicable("挑不到空闲的临时地址编码")
    if len(default_ids(state.snap)) > 1:
        raise _NotApplicable("客户的默认地址不唯一，默认标志移不回原样")
    if not clean_codes(state.snap):
        raise _NotApplicable("客户的收货地址编码不合规，路由无法定位")
    return ""


def _rid(state: _State, key: str) -> str:
    for rid, cols in state.cur.items():
        if cols.get("cAddCode", "").strip() == state.addr[key]:
            return rid
    return ""


def _check_rows(env: Env, spec: CaseSpec, state: _State, label: str) -> str:
    """库里的整户地址行应和 state.cur 逐格一致。"""
    bad = dump_problem(state.cur, _dump(env, spec, state.code))
    return f"{label}：{bad}"[:200] if bad else ""


def _reject(env: Env, spec: CaseSpec, state: _State, call: Call) -> str:
    """被拒的调用：状态码和错误码对，库里整户地址行不变（owner 非空时核对的是那个客户）。"""
    owner = call.owner or state.code
    before = _dump(env, spec, owner)
    bad, _payload = _post(env, call)
    bad = bad or dump_problem(before, _dump(env, spec, owner))
    return f"{call.label}：{bad}"[:200] if bad else ""


def _dry_run(env: Env, spec: CaseSpec, state: _State) -> str:
    fields = {"name": state.names["a"], "unit": "E2E新增单位"}
    bad, payload = _post(env, Call(_ADD, add_body(state.code, state.addr["a"], fields, True), _OK, "预演新增"))
    bad = bad or reply_addr_dry(payload, state.code, state.cur)
    return bad or _check_rows(env, spec, state, "预演新增")


def _add_row(env: Env, spec: CaseSpec, state: _State, key: str, fields: dict) -> str:
    addr = state.addr[key]
    label = f"新增地址 {key}"
    bad, payload = _post(env, Call(_ADD, add_body(state.code, addr, fields), _OK, label))
    if bad:
        return bad
    state.dirty = True
    state.temp.append(addr)
    after = _dump(env, spec, state.code)
    want = {"name": fields["name"], "unit": fields.get("unit", ""), "default": bool(fields.get("default", False))}
    bad = reply_rows(payload, (state.code, addr), True, (state.cur, after))
    bad = bad or added_problem(state.cur, after, (state.code, addr), want)
    state.cur = after
    return f"{label}：{bad}"[:200] if bad else ""


def _add_a(env: Env, spec: CaseSpec, state: _State) -> str:
    return _add_row(env, spec, state, "a", {"name": state.names["a"], "unit": "E2E新增单位"})


def _re_add(env: Env, spec: CaseSpec, state: _State) -> str:
    """同编码同名称同收货单位同默认标志：幂等，不写库。"""
    fields = {"name": state.names["a"], "unit": "E2E新增单位"}
    bad, payload = _post(env, Call(_ADD, add_body(state.code, state.addr["a"], fields), _OK, "重复新增"))
    after = _dump(env, spec, state.code)
    bad = bad or reply_rows(payload, (state.code, state.addr["a"]), False, (state.cur, after))
    return bad or dump_problem(state.cur, after)


def _dry_delete(env: Env, spec: CaseSpec, state: _State) -> str:
    """预演删除刚新增的那条（没有引用、不是默认地址）：通过校验，不改行。"""
    body = delete_body(state.code, state.addr["a"], expect_of(state.cur, _rid(state, "a")), True)
    bad, payload = _post(env, Call(_DEL, body, _OK, "预演删除"))
    bad = bad or reply_addr_dry(payload, state.code, state.cur)
    return bad or _check_rows(env, spec, state, "预演删除")


def _add_rejects(env: Env, spec: CaseSpec, state: _State) -> str:
    for call in add_calls(state.code, state.addr, state.names):
        bad = _reject(env, spec, state, call)
        if bad:
            return bad
    return ""


def _add_default(env: Env, spec: CaseSpec, state: _State) -> str:
    """再新增一条默认地址：同户其余地址的默认标志清零（added_problem 核对）。"""
    return _add_row(env, spec, state, "b", {"name": state.names["b"], "default": True})


def _delete_rejects(env: Env, spec: CaseSpec, state: _State) -> str:
    for call in delete_calls(state.code, state.addr, expect_of(state.cur, _rid(state, "b"))):
        bad = _reject(env, spec, state, call)
        if bad:
            return bad
    return ""


def _in_use(env: Env, spec: CaseSpec, state: _State) -> str:
    """被发货单引用的地址不能删（可能是另一个客户的地址；被拒，不写库）。"""
    owner, _sep, addr = first_cell(run_guarded(env, addr_used_free_sql(), spec.name)).partition("\t")
    try:
        owner = safe_code(owner.strip())
    except ValueError:
        state.notes.append("删除被发货单引用的地址（账套没有被发货单引用的收货地址）")
        return ""
    dump = _dump(env, spec, owner)
    rid = next((key for key, cols in dump.items() if cols.get("cAddCode", "") == addr.strip()), "")
    exp = expect_of(dump, rid) if rid else {}
    if not rid or (exp["default"] and len(dump) > 1):
        state.notes.append("删除被发货单引用的地址（引用的是默认地址，拒绝原因不唯一）")
        return ""
    call = Call(_DEL, delete_body(owner, addr.strip(), exp), (409, "address_in_use"), "被单据引用的地址", owner)
    return _reject(env, spec, state, call)


def _move_default(env: Env, spec: CaseSpec, state: _State) -> str:
    """把默认标志移回原处：原来有默认地址就把它重设为默认，原来没有就把新地址的默认取消（customer_address）。"""
    held = default_ids(state.snap)
    rid = held[0] if held else _rid(state, "b")
    addr = state.cur[rid].get("cAddCode", "")
    flag = bool(held)
    fields, now = {"default": flag}, {"default": cell_text(state.cur, rid, "bDefault") == "1"}
    bad, payload = _post(env, Call(_MOVE, addr_body(state.code, addr, fields, now), _OK, "移回默认地址"))
    if bad:
        return bad
    after = _dump(env, spec, state.code)
    bad = reply_addr(payload, (state.code, ""), True, (state.cur, after))
    bad = bad or dump_problem(edited(state.cur, default_edits(state.cur, rid, flag)), after)
    state.cur = after
    return f"移回默认地址：{bad}"[:200] if bad else ""


def _delete_row(env: Env, spec: CaseSpec, state: _State, key: str) -> str:
    rid = _rid(state, key)
    addr = state.addr[key]
    label = f"删除地址 {key}"
    if not rid:
        return f"{label}：快照里找不到这条地址"
    body = delete_body(state.code, addr, expect_of(state.cur, rid))
    bad, payload = _post(env, Call(_DEL, body, _OK, label))
    if bad:
        return bad
    after = _dump(env, spec, state.code)
    bad = reply_rows(payload, (state.code, addr), True, (state.cur, after))
    bad = bad or dump_problem(without(state.cur, rid), after)
    state.temp.remove(addr)
    state.cur = after
    return f"{label}：{bad}"[:200] if bad else ""


def _delete_b(env: Env, spec: CaseSpec, state: _State) -> str:
    return _delete_row(env, spec, state, "b")


def _delete_a(env: Env, spec: CaseSpec, state: _State) -> str:
    return _delete_row(env, spec, state, "a")


def _delete_again(env: Env, spec: CaseSpec, state: _State) -> str:
    """已经删掉的地址再删：404 address_not_found，不写库。"""
    body = delete_body(state.code, state.addr["b"], {"name": state.names["b"], "unit": "", "default": False})
    return _reject(env, spec, state, Call(_DEL, body, (404, "address_not_found"), "重复删除"))


def _cycle(env: Env, spec: CaseSpec, state: _State) -> str:
    steps = (
        _bind,
        _dry_run,
        _add_a,
        _dry_delete,
        _re_add,
        _add_rejects,
        _add_default,
        _delete_rejects,
        _in_use,
        _move_default,
        _delete_b,
        _delete_again,
        _delete_a,
    )
    for run in steps:
        bad = run(env, spec, state)
        if bad:
            return bad
    return dump_problem(state.snap, state.cur)


def _reset_rows(env: Env, spec: CaseSpec, state: _State, now: Dump) -> None:
    """路由还原不了时的兜底（测试账套上）：按行号把四列写回快照值。"""
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
        return "FAIL", "；".join(item for item in (problem, undo[:100]) if item)[:240]
    if note:
        return "N/A", note
    skipped = "；跳过：" + "、".join(state.notes) if state.notes else ""
    return "PASS", f"customer {state.code} address add/delete{skipped}"[:240]


def step_cusrows(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    state = _State()
    note = ""
    try:
        problem = _cycle(env, spec, state)
    except _NotApplicable as exc:
        problem, note = "", str(exc)
    except (RuntimeError, ValueError, KeyError) as exc:
        problem = str(exc)[:200]
    undo = _guarded(lambda: _restore(env, spec, state))
    return _verdict(problem, undo, note, state)


CUSROWS_HANDLERS = {"core_cusrows": step_cusrows}


def cusrows_cases() -> list[CaseSpec]:
    step = Step("core_cusrows", id_from="none")
    title = "客户收货地址新增、重放、被拒的新增与删除、带 expect 删除并还原"
    tags = frozenset({"arc:customer", "arc:customer_address", "arc:write"})
    return [CaseSpec(_NAME, title, (), (step,), True, tags=tags, routes=_ROUTES)]
