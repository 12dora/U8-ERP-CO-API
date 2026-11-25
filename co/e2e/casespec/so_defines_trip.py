"""voucher-so-defines 用例的一轮往返状态和只在第一张订单上走的步骤：否定路径、别人锁定、区分大小写、单列写入。

由 so_defines 调用。订单、编码全部来自 SQL 查询结果，代码里不写任何真实编码。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.head_fix_bodies import Snap, parse_snap
from co.e2e.casespec.so_defines_bodies import (
    NONE_MARK,
    PAIR,
    SINGLE,
    Cols,
    expect_of,
    expect_status,
    hits_of,
    non_text,
    raw_of,
    reply_write,
    send_sod,
    sod_body,
    untouched_problem,
    values_of,
    write_problem,
)
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.so_defines_cascade import CascState
from co.e2e.casespec.so_defines_sql import locker_sql, missing_id_sql, snapshot_sql
from co.e2e.casespec.sqlexec import run_guarded

OK = (200, "")
_BAD = (400, "")
_BAD_REQUEST = (400, "bad_request")
_API_LIMIT = 120
# 冒充的别的操作员姓名（ASCII，不会与真实操作员同名）。
_OTHER = "E2E-OTHER"
# 用例写入的值的长度（前缀 + 6 位十六进制）。
WIDTH = 9


@dataclass
class Ctx:
    env: Env
    spec: CaseSpec
    notes: list[str] = field(default_factory=list)
    done: list[str] = field(default_factory=list)
    start: dict[str, Snap] = field(default_factory=dict)
    stamps: set[str] = field(default_factory=set)
    cols: Cols = field(default_factory=dict)
    casc: CascState | None = None


@dataclass
class Trip:
    """一轮往返：一张订单，kind 是样本类别；cur 是上一步之后库里的快照，new 是两列的目标值，body / reply 是真写那次的请求和响应。"""

    ctx: Ctx
    so_id: str
    kind: str
    cur: Snap
    new: dict[str, str]
    swap: bool
    body: dict = field(default_factory=dict)
    reply: dict = field(default_factory=dict)

    @property
    def expect(self) -> dict:
        return expect_of(self.cur, self.so_id, self.swap)

    @property
    def now(self) -> dict[str, str | None]:
        """库里当前的 cdefine1、cdefine2。"""
        return values_of(self.cur, self.so_id)


def snap(ctx: Ctx, so_id: str) -> Snap:
    return parse_snap(sql_cells(run_guarded(ctx.env, snapshot_sql(so_id), ctx.spec.name)))


def number(tag: str) -> str:
    return tag + os.urandom(3).hex().upper()


def post(trip: Trip, body: dict, want: tuple[int, str], label: str) -> tuple[str, dict]:
    status, payload = send_sod(trip.ctx.env, body)
    return expect_status(status, payload, want, label), payload


def same(trip: Trip, label: str) -> str:
    """库里的快照应和 trip.cur 一模一样（含行版本列）。"""
    bad = untouched_problem(trip.cur, snap(trip.ctx, trip.so_id), label)
    return f"{label}：{bad}"[:200] if bad else ""


def _calls(trip: Trip) -> list[tuple[dict, tuple[int, str], str, str]]:
    """(正文, 期望状态, 说明, 期望的 field；空串不核对)。"""
    so_id, new, expect = trip.so_id, trip.new, trip.expect
    one = sod_body(so_id, new, expect)
    calls = [
        (sod_body(so_id, {"cmemo": new["cdefine1"]}, {"cmemo": None}), _BAD, "不认识的键 cmemo", ""),
        (sod_body(so_id, {"cdefine17": new["cdefine1"]}, {"cdefine17": None}), _BAD, "cdefine17", ""),
        (sod_body(so_id, {**new, "cdefine1": NONE_MARK}, expect), _BAD, "值等于 empty_values 的「无」", ""),
        (sod_body(so_id, {**new, "cdefine1": "   "}, expect), _BAD, "值空白", ""),
        (sod_body(so_id, {**new, "cdefine1": ""}, expect), _BAD, "值空串", ""),
        ({**one, "type": "dispatch"}, _BAD, "type 不是 sale_order", ""),
    ]
    limit = trip.ctx.cols.get("cdefine1", (True, 0))[1]
    wide, where = ("X" * (limit + 1), "fields.cdefine1") if 0 < limit < _API_LIMIT else ("X" * (_API_LIMIT + 1), "")
    calls.append((sod_body(so_id, {**new, "cdefine1": wide}, expect), _BAD_REQUEST, "值超长", where))
    key = non_text(trip.ctx.cols)
    if key:
        calls.append((sod_body(so_id, {key: new["cdefine1"]}, {key: None}), _BAD_REQUEST, f"非文本列 {key}", "fields." + key))
    else:
        trip.ctx.notes.append("非文本列（cdefine1–16 都是文本列）")
    gone = first_cell(run_guarded(trip.ctx.env, missing_id_sql(), trip.ctx.spec.name)).strip()
    if gone.isdigit() and gone != "0":
        calls.append((sod_body(gone, new, expect), (404, "not_found"), "订单不存在", ""))
    return calls


def invalid(trip: Trip) -> str:
    """参数不合法一律 400（列的问题带 field=fields.cdefineN）、不存在的订单 404；每条之后库里都不变。"""
    for body, want, label, where in _calls(trip):
        bad, payload = post(trip, body, want, label)
        if bad:
            return bad
        if where and payload.get("field") != where:
            return f"{label}的 field={payload.get('field')!r} 期望 {where!r}"[:200]
    return same(trip, "否定路径")


def locked(trip: Trip) -> str:
    """别的操作员锁定（SQL 把锁定人写成别人）：409 state_mismatch，detail.reason 是 locked、detail.locker 是锁定人，库里除行版本外不变；之后按起始值写回锁定人。"""
    env, name = trip.ctx.env, trip.ctx.spec.name
    raw = raw_of(trip.cur, trip.so_id, "clocker")
    if first_cell(run_guarded(env, locker_sql(trip.so_id, _OTHER), name)).strip() != "1":
        return "把锁定人写成别人应改 1 行"
    try:
        bad, payload = post(trip, sod_body(trip.so_id, trip.new, trip.expect), (409, "state_mismatch"), "别人锁定时写入")
    finally:
        run_guarded(env, locker_sql(trip.so_id, raw), name)
    detail = payload.get("detail")
    detail = detail if isinstance(detail, dict) else {}
    who = str(detail.get("locker") or "").strip()
    now = snap(trip.ctx, trip.so_id)
    hits = hits_of(trip.cur, now)
    trip.cur = now  # 锁定人的两次写让行版本变了，之后的比对以这份为准
    if bad:
        return bad
    if detail.get("reason") != "locked":
        return f"409 的 detail.reason={detail.get('reason')!r} 期望 'locked'"
    if who != _OTHER:
        return f"409 的 detail.locker={who!r} 期望 {_OTHER!r}"
    return f"别人锁定时写入后库里有变化 {sorted(hits)[:3]}"[:200] if hits else ""


def _changed(trip: Trip, body: dict, wants: dict[str, str], label: str) -> str:
    """真写一次：200 changed=true，只有 wants 里的列和修改人类列变了，响应 current 恰好是请求的列。"""
    bad, payload = post(trip, body, OK, label)
    if bad:
        return bad
    after = snap(trip.ctx, trip.so_id)
    problem, stamps = write_problem(trip.cur, after, wants)
    trip.ctx.stamps.update(stamps)
    trip.cur = after
    wrong = reply_write(payload, True, body["fields"])
    return f"{label}：{wrong or problem}"[:200] if wrong or problem else ""


def case_change(trip: Trip) -> str:
    """大小写不同算真改：cdefine1 改成小写写法，cdefine2 照旧；只有 cdefine1 和修改人类列变了。"""
    lower = trip.new["cdefine1"].lower()
    body = sod_body(trip.so_id, {**trip.new, "cdefine1": lower}, dict(trip.new))
    bad = _changed(trip, body, {"cdefine1": lower}, "大小写不同")
    trip.new = {**trip.new, "cdefine1": lower}
    return bad


def single(trip: Trip) -> str:
    """只写 cdefine3：响应 current 只有 cdefine3，cdefine1、cdefine2 不动。列不是文本列或太窄时记在说明里跳过。"""
    text, limit = trip.ctx.cols.get(SINGLE, (False, 0))
    raw = raw_of(trip.cur, trip.so_id, SINGLE)
    if not text or 0 < limit < WIDTH:
        trip.ctx.notes.append(f"单列写入（{SINGLE} 不是文本列或列宽不到 {WIDTH}）")
        return ""
    if raw is not None and ("\\" in raw or len(raw) > _API_LIMIT):
        trip.ctx.notes.append(f"单列写入（{SINGLE} 的当前值读不全）")
        return ""
    value = number("E2S")
    before = values_of(trip.cur, trip.so_id, PAIR)
    bad = _changed(trip, sod_body(trip.so_id, {SINGLE: value}, {SINGLE: raw}), {SINGLE: value}, "单列写入")
    if not bad and values_of(trip.cur, trip.so_id, PAIR) != before:
        return "单列写入后 cdefine1 / cdefine2 变了"
    return bad
