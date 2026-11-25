"""客户开票单位新增、设默认、删除 `customer-invoice-company-cycle`：挑一个开票单位数据一致的客户 C（1 到 6 行、恰一个默认、
客户表头等于默认）和另一个还不是它开票单位的客户 D，快照 C 的全部开票单位行和客户行 →
预演新增不改行 → 新增 D（非默认）：表头不动、其余行不变 → 重放 no-op → 要求 default 的重复新增 409 invoice_company_taken →
设默认 D（expect 不符 409、默认行不能删 409）→ 客户表头 cInvoiceCompany 随默认移到 D、旧默认清 0、只有一个默认 →
对 D 重复设默认 no-op → 不存在的开票单位 / 客户 / 缺 expect 被拒 → 设回原默认、表头回到原值 → 删除 D → 再删得 404。
用例结束时开票单位行和客户表头与快照一致（修改人、修改日期、行版本除外）。

中途失败时按 SQL 删掉 D 行、把默认和表头写回快照里的默认开票单位。步骤 kind 为 core_cusinv，挂在 arc_write.CORE_HANDLERS。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.arc_cus_bodies import load_row, with_flags
from co.e2e.casespec.arc_cus_person_bodies import changed, expect, send, shown
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.arc_cus_invoice_sql import (
    delete_pair_sql,
    header_sql,
    missing_sql,
    pick_sql,
    reset_default_sql,
)
from co.e2e.casespec.arc_cus_invoice_sql import rows_sql
from co.e2e.casespec.gl_voucher_sql import safe_code
from co.e2e.casespec.sqlexec import run_guarded

_NAME = "customer-invoice-company-cycle"
_ADD, _DEL, _SET = (
    "customer_invoice_company_add",
    "customer_invoice_company_delete",
    "customer_invoice_company",
)
_ROUTES = tuple("/v1/archives/" + op for op in (_ADD, _DEL, _SET))
_OK = (200, "")
# 写入允许变的客户列：修改人、修改日期、行版本。
_STAMP = frozenset({"cModifyPerson", "dModifyDate", "pubufts", "ufts"})
Rows = list[tuple[str, int]]


class _NotApplicable(Exception):
    """账套里没有这个用例需要的数据：记 N/A，不是失败。"""


@dataclass
class _State:
    code: str = ""
    other: str = ""
    missing: str = ""
    snap: Rows = field(default_factory=list)
    cur: Rows = field(default_factory=list)
    head: dict[str, str] = field(default_factory=dict)
    origin: str = ""
    dirty: bool = False


def _rows(env: Env, spec: CaseSpec, code: str) -> Rows:
    cells = sql_cells(run_guarded(env, rows_sql(code), spec.name))
    pairs = [cell.split("\t") for cell in cells]
    return sorted(
        (item[0].strip().upper(), int(item[1])) for item in pairs if len(item) == 2
    )


def _header(env: Env, spec: CaseSpec, code: str) -> dict[str, str]:
    return load_row(sql_cells(run_guarded(env, header_sql(code), spec.name)))


def _view(items: object) -> Rows:
    """响应里的 before / after → 排序后的 (开票单位编码, 默认标志)。"""
    out: Rows = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, dict):
            out.append(
                (shown(item.get("code")).upper(), 1 if item.get("bdefault") else 0)
            )
    return sorted(out)


def _pair(state: _State, company: str) -> str:
    return f"{state.code}:{company}"


def _body(
    state: _State,
    company: str,
    flag: bool | None = None,
    dry: bool = False,
    **extra: object,
) -> dict:
    body: dict = {"code": _pair(state, company)}
    if flag is not None:
        body["expect"] = {"bdefault": flag}
    body.update(extra)
    return with_flags(body, None, dry, "cusinv")


def _call(
    env: Env, op: str, body: dict, want: tuple[int, str], label: str
) -> tuple[str, dict]:
    status, payload = send(env, op, body)
    return expect(status, payload, want, label), payload


def _reply(payload: dict, code: str, flag: bool, rows: tuple[Rows, Rows]) -> str:
    """响应：ok、code、changed，before / after 与写前写后的库快照一致。"""
    if payload.get("ok") is not True or shown(payload.get("code")) != code:
        return f"响应 ok/code 不对：{payload.get('ok')!r} {payload.get('code')!r}"[:200]
    if payload.get("changed") is not flag:
        return f"响应 changed={payload.get('changed')!r} 期望 {flag!r}"[:200]
    for key, want in (("before", rows[0]), ("after", rows[1])):
        if _view(payload.get(key)) != want:
            return f"响应 {key}={_view(payload.get(key))!r} 期望 {want!r}"[:200]
    return ""


def _check_db(env: Env, spec: CaseSpec, state: _State, want: Rows, label: str) -> str:
    """库里开票单位行应等于 want，客户表头应是 want 里的默认开票单位，客户行其它列不变。"""
    got = _rows(env, spec, state.code)
    if got != want:
        return f"{label}：开票单位行 {got!r} 期望 {want!r}"[:200]
    row = _header(env, spec, state.code)
    wanted = next(code for code, flag in want if flag == 1)
    if shown(row.get("cInvoiceCompany")).upper() != wanted.upper():
        return (
            f"{label}：客户表头开票单位 {row.get('cInvoiceCompany')!r} 期望 {wanted!r}"[
                :200
            ]
        )
    left = changed(state.head, row, _STAMP | {"cInvoiceCompany"})
    return f"{label}：客户行其它列被改动 {left}"[:200] if left else ""


def _defaults(rows: Rows, company: str) -> Rows:
    """把 company 设为唯一默认后的行（company 不在行里时原样返回）。"""
    return sorted((code, 1 if code == company.upper() else 0) for code, _flag in rows)


def _bind(env: Env, spec: CaseSpec, state: _State) -> str:
    code, _sep, other = first_cell(run_guarded(env, pick_sql(), spec.name)).partition(
        "\t"
    )
    try:
        state.code, state.other = safe_code(code.strip()), safe_code(other.strip())
        state.missing = safe_code(
            first_cell(run_guarded(env, missing_sql(), spec.name)).strip()
        )
    except ValueError:
        raise _NotApplicable(
            "账套里没有开票单位数据一致的客户，或挑不到另一个客户"
        ) from None
    state.snap = state.cur = _rows(env, spec, state.code)
    state.head = _header(env, spec, state.code)
    state.origin = next(item for item, flag in state.snap if flag == 1)
    if shown(state.head.get("cInvoiceCompany")).upper() != state.origin:
        raise _NotApplicable("客户表头的开票单位不等于默认行")
    return ""


def _dry_run(env: Env, spec: CaseSpec, state: _State) -> str:
    bad, payload = _call(
        env, _ADD, _body(state, state.other, None, True, default=True), _OK, "预演新增"
    )
    if bad:
        return bad
    if payload.get("dry_run") is not True or payload.get("mode") != "validate":
        return f"预演应 dry_run/validate，实际 {payload.get('dry_run')!r}/{payload.get('mode')!r}"[
            :200
        ]
    if payload.get("changed") is not True:
        return f"预演应按计划返回 changed=true，实际 {payload.get('changed')!r}"[:200]
    return _check_db(env, spec, state, state.snap, "预演新增")


def _add(env: Env, spec: CaseSpec, state: _State) -> str:
    bad, payload = _call(env, _ADD, _body(state, state.other), _OK, "新增开票单位")
    if bad:
        return bad
    state.dirty = True
    want = sorted([*state.cur, (state.other.upper(), 0)])
    bad = _reply(payload, _pair(state, state.other), True, (state.cur, want))
    state.cur = want
    return bad or _check_db(env, spec, state, want, "新增开票单位")


def _replay(env: Env, spec: CaseSpec, state: _State) -> str:
    bad, payload = _call(env, _ADD, _body(state, state.other), _OK, "重复新增")
    bad = bad or _reply(
        payload, _pair(state, state.other), False, (state.cur, state.cur)
    )
    return bad or _check_db(env, spec, state, state.cur, "重复新增")


def _reject(
    env: Env,
    spec: CaseSpec,
    state: _State,
    call: tuple[str, dict, tuple[int, str], str],
) -> str:
    """被拒的调用：状态码和错误码对，开票单位行和客户表头不变。"""
    bad, _payload = _call(env, *call)
    return bad or _check_db(env, spec, state, state.cur, call[3])


def _add_set_rejects(state: _State) -> tuple:
    other, missing = state.other, state.missing
    return (
        (_ADD, _body(state, other, None, False, default=True), (409, "invoice_company_taken"), "重复新增要求默认"),
        (_ADD, _body(state, missing), (409, "invoice_company_invalid"), "新增不存在的开票单位"),
        (_SET, _body(state, other, True), (409, "state_mismatch"), "设默认 expect 不符"),
        (_SET, _body(state, missing, False), (409, "invoice_company_invalid"), "设默认不是客户档案的开票单位"),
        (_SET, {**_body(state, other, False), "code": state.code}, (400, ""), "编码缺开票单位段"),
        (_SET, {**_body(state, other, False), "code": f"{missing}:{other}"}, (404, "not_found"), "客户不存在"),
    )


def _delete_rejects(state: _State) -> tuple:
    return (
        (_DEL, _body(state, state.missing, False), (404, "invoice_company_not_found"), "删除不存在的行"),
        (_DEL, _body(state, state.origin, False), (409, "state_mismatch"), "删除 expect 不符"),
        (_DEL, _body(state, state.origin, True), (409, "invoice_company_is_default"), "删除默认行"),
        (_DEL, _body(state, state.other), (400, ""), "删除缺 expect"),
    )


def _rejects(env: Env, spec: CaseSpec, state: _State) -> str:
    for call in (*_add_set_rejects(state), *_delete_rejects(state)):
        bad = _reject(env, spec, state, call)
        if bad:
            return bad
    return ""


def _make_default(env: Env, spec: CaseSpec, state: _State) -> str:
    bad, payload = _call(env, _SET, _body(state, state.other, False), _OK, "设默认")
    if bad:
        return bad
    want = _defaults(state.cur, state.other)
    bad = _reply(payload, _pair(state, state.other), True, (state.cur, want))
    state.cur = want
    return bad or _check_db(env, spec, state, want, "设默认")


def _default_again(env: Env, spec: CaseSpec, state: _State) -> str:
    bad, payload = _call(env, _SET, _body(state, state.other, True), _OK, "重复设默认")
    bad = bad or _reply(
        payload, _pair(state, state.other), False, (state.cur, state.cur)
    )
    return bad or _check_db(env, spec, state, state.cur, "重复设默认")


def _delete_default(env: Env, spec: CaseSpec, state: _State) -> str:
    call = (
        _DEL,
        _body(state, state.other, True),
        (409, "invoice_company_is_default"),
        "删除新默认行",
    )
    return _reject(env, spec, state, call)


def _set_back(env: Env, spec: CaseSpec, state: _State) -> str:
    bad, payload = _call(
        env, _SET, _body(state, state.origin, False), _OK, "设回原默认"
    )
    if bad:
        return bad
    want = _defaults(state.cur, state.origin)
    bad = _reply(payload, _pair(state, state.origin), True, (state.cur, want))
    state.cur = want
    return bad or _check_db(env, spec, state, want, "设回原默认")


def _delete(env: Env, spec: CaseSpec, state: _State) -> str:
    bad, payload = _call(
        env, _DEL, _body(state, state.other, False), _OK, "删除开票单位"
    )
    if bad:
        return bad
    want = sorted(item for item in state.cur if item[0] != state.other.upper())
    bad = _reply(payload, _pair(state, state.other), True, (state.cur, want))
    state.cur = want
    return bad or _check_db(env, spec, state, want, "删除开票单位")


def _delete_again(env: Env, spec: CaseSpec, state: _State) -> str:
    call = (
        _DEL,
        _body(state, state.other, False),
        (404, "invoice_company_not_found"),
        "重复删除",
    )
    return _reject(env, spec, state, call)


def _cycle(env: Env, spec: CaseSpec, state: _State) -> str:
    steps = (_bind, _dry_run, _add, _replay, _rejects, _make_default, _default_again, _delete_default,
             _set_back, _delete, _delete_again)  # fmt: skip
    for run in steps:
        bad = run(env, spec, state)
        if bad:
            return bad
    return (
        ""
        if state.cur == state.snap
        else f"结束时开票单位行 {state.cur!r} 与快照 {state.snap!r} 不同"[:200]
    )


def _restore(env: Env, spec: CaseSpec, state: _State) -> str:
    """路由已把状态改回时什么都不做；否则按 SQL 删掉 D 行、写回默认和表头（测试账套上）。"""
    if not state.dirty:
        return ""
    now = _rows(env, spec, state.code)
    if (
        now == state.snap
        and shown(_header(env, spec, state.code).get("cInvoiceCompany")).upper()
        == state.origin
    ):
        return ""
    if any(code == state.other.upper() for code, _flag in now):
        run_guarded(env, delete_pair_sql(state.code, state.other), spec.name)
    run_guarded(env, reset_default_sql(state.code, state.origin), spec.name)
    left = _rows(env, spec, state.code)
    return f"还原后开票单位行仍有差异 {left!r}"[:200] if left != state.snap else ""


def _guarded(step: Callable[[], str]) -> str:
    try:
        return step()
    except (RuntimeError, ValueError) as exc:
        return str(exc)[:200]


def step_cusinv(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    del step
    state, note = _State(), ""
    try:
        problem = _cycle(env, spec, state)
    except _NotApplicable as exc:
        problem, note = "", str(exc)
    except (RuntimeError, ValueError, KeyError, StopIteration) as exc:
        problem = str(exc)[:200]
    undo = _guarded(lambda: _restore(env, spec, state))
    if problem or undo:
        return "FAIL", "；".join(item for item in (problem, undo[:100]) if item)[:240]
    if note:
        return "N/A", note
    return "PASS", f"customer {state.code} invoice company add/default/delete"[:240]


CUSINV_HANDLERS = {"core_cusinv": step_cusinv}


def cusinv_cases() -> list[CaseSpec]:
    step = Step("core_cusinv", id_from="none")
    title = "客户开票单位新增、设默认（表头随默认移动）、被拒的写入、删除并还原"
    tags = frozenset({"arc:customer", "arc:customer_invoice_company", "arc:write"})
    return [CaseSpec(_NAME, title, (), (step,), True, tags=tags, routes=_ROUTES)]
