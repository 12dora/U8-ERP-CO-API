"""客户专管业务员：现有客户快照 → customer_person 改成另一个业务员 → 只允许 cCusPPerson、
符合规则时的 cCusDepart、Customer_Auth 部门行和修改人/日期/行版本变化，再改回去。

否定路径同用例：expect 不符 409、未知业务员 409、客户不存在 404；health 列出 read_only_exceptions 时 archives/update 只改 spec_operator 得 403（没有则跳过）；dry_run 不改行。
用例自己按快照差异还原；会话快照是最后兜底。步骤 kind 为 core_pperson，挂在 arc_write.CORE_HANDLERS。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from co.client.u8co_client import U8CoError
from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.arc_cus_person_bodies import (
    cell,
    changed,
    check_auth,
    expect,
    listed_exceptions,
    parse_auth,
    parse_row,
    person_body,
    reply_dry,
    reply_write,
    send,
    update_body,
    volatile_set,
)
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_cus_person_sql import (
    clear_person_sql,
    customer_auth_sql,
    customer_row_sql,
    customer_with_person_sql,
    delete_auth_sql,
    dept_usable_sql,
    other_person_sql,
    person_dept_sql,
    person_one_sql,
    restore_auth_sql,
    restore_dept_sql,
    volatile_sql,
)
from co.e2e.casespec.gl_voucher_sql import safe_code
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.testdata import DataMissing
from co.e2e.testdata import value as td

_NAME = "archive-customer-pperson"
_DATA = ("archives.customer.existing",)
_ROUTES = ("/v1/archives/customer_person", "/v1/archives/update", "/v1/health")
_MISSING = "ZZNOCUS01"
_UNKNOWN = "ZZNOSUCH"
_OK = (200, "")
_PERSON = "customer_person"


@dataclass
class _State:
    code: str
    snap: dict[str, str] = field(default_factory=dict)
    volatile: frozenset[str] = field(default_factory=frozenset)
    orig_person: str = ""
    orig_dept: str = ""
    orig_person_dept: str = ""
    other: str = ""
    other_dept: str = ""
    follow: bool = False
    auth: list[tuple[str, str, str]] = field(default_factory=list)
    dirty: bool = False


def _sql(env: Env, spec: CaseSpec, query: str) -> str:
    return first_cell(run_guarded(env, query, spec.name)).strip()


def _row(env: Env, spec: CaseSpec, code: str) -> dict[str, str]:
    return parse_row(sql_cells(run_guarded(env, customer_row_sql(code), spec.name)))


def _auth(env: Env, spec: CaseSpec, code: str) -> list[tuple[str, str, str]]:
    return parse_auth(sql_cells(run_guarded(env, customer_auth_sql(code), spec.name)))


def _health(env: Env) -> dict:
    try:
        payload = env.client.health()
    except (OSError, RuntimeError, ValueError, U8CoError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _post(env: Env, op: str, body: dict, want: tuple[int, str], label: str) -> tuple[str, dict]:
    status, payload = send(env, op, body)
    return expect(status, payload, want, label), payload


def _listed_other(current: str) -> str:
    try:
        code = safe_code(td("archives.customer.spec_operator_other"))
    except (DataMissing, ValueError):
        return ""
    return code if code != current else ""


def _load(env: Env, spec: CaseSpec, state: _State) -> str:
    names = [item.strip() for item in sql_cells(run_guarded(env, volatile_sql(), spec.name))]
    state.volatile = volatile_set(names)
    try:
        state.snap = _row(env, spec, state.code)
    except ValueError as exc:
        return str(exc)[:200]
    if not state.snap:
        return f"没有客户 {state.code}"
    state.orig_person = cell(state.snap, "cCusPPerson")
    state.orig_dept = cell(state.snap, "cCusDepart")
    return ""


def _picked_customer(env: Env, spec: CaseSpec) -> str:
    try:
        return safe_code(_sql(env, spec, customer_with_person_sql()))
    except ValueError:
        return ""


def _bind(env: Env, spec: CaseSpec, state: _State) -> str:
    bad = _load(env, spec, state)
    if not bad and state.orig_person:
        return ""
    picked = _picked_customer(env, spec)
    if not picked or picked == state.code:
        return bad  # 账套里没有已填专管的客户：就用原值为空的这一户，还原时用 SQL 清回空
    state.code = picked
    return _load(env, spec, state)


def _pick(env: Env, spec: CaseSpec, state: _State) -> str:
    listed = _listed_other(state.orig_person)
    if listed and _sql(env, spec, person_one_sql(listed)) == listed:
        state.other = listed
        return ""
    try:
        state.other = safe_code(_sql(env, spec, other_person_sql(state.orig_person)))
    except ValueError:
        return "账套没有另一个专管业务员"
    if state.other == state.orig_person:
        return "账套没有另一个专管业务员"
    return ""


def _depts(env: Env, spec: CaseSpec, state: _State) -> str:
    state.orig_person_dept = _sql(env, spec, person_dept_sql(state.orig_person)) if state.orig_person else ""
    state.other_dept = _sql(env, spec, person_dept_sql(state.other))
    leaf = _sql(env, spec, dept_usable_sql(state.other_dept)) if state.other_dept else ""
    aligned = state.orig_dept == "" or state.orig_dept == state.orig_person_dept
    moved = state.other_dept != "" and state.other_dept != state.orig_dept
    state.follow = bool(leaf == state.other_dept and aligned and moved)
    state.auth = _auth(env, spec, state.code)
    return ""


def _dry_run(env: Env, spec: CaseSpec, state: _State) -> str:
    body = person_body(state.code, state.other, state.orig_person, True)
    bad, payload = _post(env, _PERSON, body, _OK, "预演改专管")
    if bad:
        return bad
    bad = reply_dry(payload, state.code, state.orig_person)
    if bad:
        return bad
    hits = changed(state.snap, _row(env, spec, state.code), frozenset())
    return f"预演不应改行 {hits}"[:200] if hits else ""


def _write(env: Env, spec: CaseSpec, state: _State) -> str:
    del spec
    state.dirty = True
    key = "e2e-pperson-" + os.urandom(8).hex()
    body = person_body(state.code, state.other, state.orig_person, False, key)
    bad, payload = _post(env, _PERSON, body, _OK, "改专管业务员")
    want = state.other_dept if state.follow else state.orig_dept
    return bad or reply_write(
        payload, state.code, state.orig_person, state.other, (state.orig_dept, want)
    )


def _check_write(env: Env, spec: CaseSpec, state: _State) -> str:
    after = _row(env, spec, state.code)
    hits = changed(state.snap, after, state.volatile)
    names = {item.lower() for item in hits}
    moved = cell(after, "cCusDepart") != state.orig_dept
    allowed = {"ccuspperson"} | ({"ccusdepart"} if moved else set())
    label = "cCusPPerson" + (" 和 cCusDepart" if moved else "")
    if names != allowed:
        return f"专管更新后应只改 {label}，实际 {hits or '无变化'}"[:200]
    if cell(after, "cCusPPerson") != state.other:
        return f"cCusPPerson={cell(after, 'cCusPPerson')!r} 期望 {state.other!r}"
    want = state.other_dept if state.follow else state.orig_dept
    if cell(after, "cCusDepart") != want:
        return f"cCusDepart={cell(after, 'cCusDepart')!r} 期望 {want!r}"
    return check_auth(state.auth, _auth(env, spec, state.code), state.other_dept, moved)


def _mismatch(env: Env, spec: CaseSpec, state: _State) -> str:
    del spec
    state.dirty = True
    # 写入后当前值是 other：用原值当 expect；原值为空时 person 不能为空，换成 other + 不存在的 expect。
    body = person_body(state.code, state.orig_person or state.other, state.orig_person or _UNKNOWN)
    bad, _payload = _post(env, _PERSON, body, (409, "state_mismatch"), "expect 不符")
    return bad


def _unknown(env: Env, spec: CaseSpec, state: _State) -> str:
    del spec
    state.dirty = True
    body = person_body(state.code, _UNKNOWN, state.other)
    bad, _payload = _post(env, _PERSON, body, (409, "u8_rejected"), "未知业务员")
    return bad


def _missing(env: Env, spec: CaseSpec, state: _State) -> str:
    del spec
    body = person_body(_MISSING, state.other)  # 业务员用本账套挑到的真实人员，只让客户不存在
    bad, _payload = _post(env, _PERSON, body, (404, "not_found"), "不存在的客户")
    return bad


def _update_blocked(env: Env, spec: CaseSpec, state: _State) -> str:
    del spec
    if not listed_exceptions(_health(env)):
        return ""
    state.dirty = True
    body = update_body(state.code, {"spec_operator": state.other})
    bad, _payload = _post(env, "update", body, (403, "account_read_only"), "archives/update")
    return bad


def _undo_auth(env: Env, spec: CaseSpec, state: _State) -> str:
    now = _auth(env, spec, state.code)
    before = {row[0]: row for row in state.auth}
    for auto_id, priv, typ in now:
        if typ != "0":
            continue
        old = before.get(auto_id)
        if old is None:
            run_guarded(env, delete_auth_sql(state.code, auto_id), spec.name)
        elif old[1] != priv:
            run_guarded(env, restore_auth_sql(state.code, auto_id, old[1]), spec.name)
    now = _auth(env, spec, state.code)
    return "" if now == state.auth else "还原后 Customer_Auth 仍有变化"


def _restore(env: Env, spec: CaseSpec, state: _State) -> str:
    if not state.snap or not state.dirty:
        return ""
    after = _row(env, spec, state.code)
    current, orig = cell(after, "cCusPPerson"), cell(state.snap, "cCusPPerson")
    if current != orig and not orig:
        run_guarded(env, clear_person_sql(state.code), spec.name)  # 接口不能清空专管，测试账套上用 SQL 还原
        after = _row(env, spec, state.code)
    elif current != orig:
        body = person_body(state.code, orig, current)
        bad, _payload = _post(env, _PERSON, body, _OK, "还原专管业务员")
        if bad:
            return bad
        after = _row(env, spec, state.code)
    want = cell(state.snap, "cCusDepart")
    if cell(after, "cCusDepart") != want:
        run_guarded(env, restore_dept_sql(state.code, want), spec.name)
        after = _row(env, spec, state.code)
    bad = _undo_auth(env, spec, state)
    if bad:
        return bad
    hits = changed(state.snap, after, state.volatile)
    return f"还原后仍有变化 {hits}"[:200] if hits else ""


def _cycle(env: Env, spec: CaseSpec, state: _State) -> str:
    return (
        _bind(env, spec, state)
        or _pick(env, spec, state)
        or _depts(env, spec, state)
        or _dry_run(env, spec, state)
        or _write(env, spec, state)
        or _check_write(env, spec, state)
        or _mismatch(env, spec, state)
        or _unknown(env, spec, state)
        or _missing(env, spec, state)
        or _update_blocked(env, spec, state)
    )


def _finish(problem: str, undo: str, code: str) -> tuple[str, str]:
    if problem:
        return "FAIL", ((undo + "；" if undo else "") + problem)[:240]
    if undo:
        return "FAIL", ("还原失败：" + undo)[:240]
    return "PASS", f"customer {code} person"


def step_pperson(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    try:
        state = _State(code=safe_code(td("archives.customer.existing")))
    except (DataMissing, ValueError) as exc:
        return "FAIL", str(exc)[:200]
    try:
        problem = _cycle(env, spec, state)
    except (RuntimeError, ValueError) as exc:
        problem = str(exc)[:200]
    try:
        undo = _restore(env, spec, state)
    except (RuntimeError, ValueError) as exc:
        undo = str(exc)[:200]
    return _finish(problem, undo, state.code)


PPERSON_HANDLERS = {"core_pperson": step_pperson}


def pperson_cases() -> list[CaseSpec]:
    step = Step("core_pperson", id_from="none")
    title = "客户专管业务员按规则改专管和分管部门并还原"
    tags = frozenset({"arc:customer"})
    return [CaseSpec(_NAME, title, (), (step,), True, data=_DATA, tags=tags, routes=_ROUTES)]
