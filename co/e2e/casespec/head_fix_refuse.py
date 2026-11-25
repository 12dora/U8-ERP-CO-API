"""voucher-head-fix 用例的否定路径：expect 不符、参数不合法、地址 / 业务员 / 单据不存在，以及回滚用的 undo 行不在 expect 值、
表头业务员为空时改业务员。每条被拒的调用之后库里的快照必须原样。不连接服务器。"""

from __future__ import annotations

import os

from co.e2e.casespec.head_fix_bodies import hf_body, row_ids, same_problem, view_of
from co.e2e.casespec.head_fix_ctx import ID, Ctx, Trip, call, pick_one, post, same, snap, sql
from co.e2e.casespec.arc_cus_sql import addr_count_sql
from co.e2e.casespec.head_fix_sql import missing_id_sql, other_dept_person_sql, person_count_sql
from co.e2e.casespec.head_fix_refuse_sql import empty_person_pick_sql, not_reversible_pick_sql, unaudited_pick_sql
from co.e2e.casespec.gl_voucher_sql import safe_code

Calls = list[tuple[dict, tuple[int, str], str]]


def _free_addr(trip: Trip) -> str:
    cus = trip.cur["DL"][trip.dlid].get("ccuscode", "")
    try:
        safe_code(cus)
    except ValueError:
        return ""
    for _ in range(3):
        cand = "ZZNOA" + os.urandom(3).hex()
        if sql(trip.ctx, addr_count_sql(cus, cand)) == "0":
            return cand
    return ""


def _free_person(trip: Trip) -> str:
    for _ in range(3):
        cand = "ZZNOP" + os.urandom(3).hex()
        if sql(trip.ctx, person_count_sql(cand)) == "0":
            return cand
    return ""


def other_value(value: str) -> str:
    """与 value 不同、长度不增的值：改最后一个字符（空值给一个字符），不会超过字段长度上限。"""
    if not value:
        return "x"
    return value[:-1] + ("0" if value[-1] != "0" else "1")


def _bad_calls(trip: Trip) -> Calls:
    """会被拒绝的调用：(正文, 期望状态, 说明)。"""
    here, old = trip.dlid, trip.old
    both = {"addr_code": trip.pick.addr, "person_code": trip.pick.person}
    exp = {"addr_code": old["addr_code"], "person_code": old["person_code"]}
    calls: Calls = [
        (hf_body(here, both, {key: other_value(val) for key, val in exp.items()}), (409, "state_mismatch"), "expect 不符"),
        (hf_body(here, both, None), (400, ""), "缺 expect"),
        (hf_body(here, {"addr_code": both["addr_code"]}, exp), (400, ""), "expect 多余键"),
        (hf_body(here, {**both, "memo": "x"}, {**exp, "memo": ""}), (400, ""), "多余字段"),
        (hf_body(here, {}, {}), (400, ""), "fields 为空"),
        (hf_body(here, {"person_code": ""}, {"person_code": old["person_code"]}), (400, ""), "业务员空串"),
        ({**hf_body(here, both, exp), "type": "sale_out"}, (400, ""), "type 不是 dispatch"),
        (hf_body(here, both, exp, undo={"addr_code": {"Ar_Detail": [1]}}), (400, ""), "undo 里的表不属于该字段"),
        (hf_body(here, {"addr_code": both["addr_code"]}, {"addr_code": exp["addr_code"]}, undo={"person_code": {}}), (400, ""), "undo 的键不在 fields 里"),
    ]
    return calls + _lookup_calls(trip, exp)


def _lookup_calls(trip: Trip, exp: dict[str, str]) -> Calls:
    here, old, ctx = trip.dlid, trip.old, trip.ctx
    other = sql(ctx, other_dept_person_sql(here))
    wanted = (
        ("addr_code", _free_addr(trip), (409, "address_not_found"), "地址不存在"),
        ("person_code", _free_person(trip), (409, "person_invalid"), "业务员不存在"),
        ("person_code", other, (409, "person_department_mismatch"), "业务员不在发货单部门"),
    )
    calls: Calls = []
    for key, value, want, label in wanted:
        if value:
            calls.append((hf_body(here, {key: value}, {key: old[key]}), want, label))
        else:
            ctx.notes.append(label)
    gone = sql(ctx, missing_id_sql())
    if ID.fullmatch(gone or ""):
        body = hf_body(gone, {"addr_code": trip.pick.addr}, {"addr_code": exp["addr_code"]})
        calls.append((body, (404, "not_found"), "单据不存在"))
    return calls


def refusals(trip: Trip) -> str:
    for body, want, label in _bad_calls(trip):
        bad = call(trip, body, want, label)
        if bad:
            return bad
    return same(trip, "拒绝的调用之后")


def undo_mismatch(trip: Trip) -> str:
    """正向之后，合并发票被跳过、仍是旧值：把它列进回滚的 undo，它不等于 expect（新值）→ 409 state_mismatch，
    整笔回滚（表头也不动）。"""
    ids = row_ids(trip.cur, "SB")
    if len(ids) != 1:
        return ""
    undo = {"addr_code": {"SaleBillVouch": [int(ids[0])]}}
    body = hf_body(trip.dlid, trip.want, trip.fields, undo=undo)
    bad = call(trip, body, (409, "state_mismatch"), "undo 里的行不等于 expect")
    return bad or same(trip, "undo 行不符之后")


def empty_person(ctx: Ctx, skip: tuple[str, ...]) -> str:
    """表头业务员为空的发货单改业务员：409 person_empty，或因原业务员缺失而 409 not_reversible（两者都是「之后无法回滚」），
    不写库。账套里没有这样的发货单就跳过。"""
    found = pick_one(ctx, (empty_person_pick_sql(skip),))
    if found is None:
        ctx.notes.append("业务员为空的发货单（person_empty 拒绝）")
        return ""
    before = snap(ctx, found.dlid)
    body = hf_body(found.dlid, {"person_code": found.person}, {"person_code": ""})
    bad, payload = post(ctx, body, (409, ""), "表头业务员为空时改业务员")
    if not bad and payload.get("code") not in ("person_empty", "not_reversible"):
        bad = f"表头业务员为空时改业务员应 person_empty，实际 {payload.get('code')!r}"
    bad = bad or same_problem(before, snap(ctx, found.dlid), False)
    return f"person_empty：{bad}"[:200] if bad else ""


def not_reversible(ctx: Ctx, skip: tuple[str, ...]) -> str:
    """原值不可回滚的发货单（原收货地址不在档案里 / 原业务员无效）改那一项：409 not_reversible，不写库。
    账套里没有这样的发货单就跳过。"""
    for kind, key, label in (("addr", "addr_code", "原收货地址不在档案里"), ("person", "person_code", "原业务员无效")):
        found = pick_one(ctx, (not_reversible_pick_sql(kind, skip),))
        if found is None:
            ctx.notes.append(f"{label}的发货单（not_reversible 拒绝）")
            continue
        before = snap(ctx, found.dlid)
        new = found.addr if kind == "addr" else found.person
        body = hf_body(found.dlid, {key: new}, {key: view_of(before)[key]})
        bad, _payload = post(ctx, body, (409, "not_reversible"), f"{label}时改 {key}")
        bad = bad or same_problem(before, snap(ctx, found.dlid), False)
        if bad:
            return f"not_reversible：{bad}"[:200]
    return ""


def not_audited(ctx: Ctx, skip: tuple[str, ...]) -> str:
    """未审核的发货单：真写和预演都是 409 not_audited（先于 expect 等其它检查），不写库。账套里没有就跳过。"""
    found = pick_one(ctx, (unaudited_pick_sql(skip),))
    if found is None:
        ctx.notes.append("未审核的发货单（not_audited 拒绝）")
        return ""
    before = snap(ctx, found.dlid)
    fields = {"addr_code": found.addr, "person_code": found.person}
    for dry in (False, True):
        body = hf_body(found.dlid, fields, view_of(before), dry)
        bad, _payload = post(ctx, body, (409, "not_audited"), "未审核发货单" + ("预演" if dry else ""))
        bad = bad or same_problem(before, snap(ctx, found.dlid), False)
        if bad:
            return f"not_audited：{bad}"[:200]
    return ""
