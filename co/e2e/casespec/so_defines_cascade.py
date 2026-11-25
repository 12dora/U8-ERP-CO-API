"""销售订单表头自定义项写入连带下游（voucher-so-defines 用例里 cascade=true 的一轮，由 so_defines 在普通几轮之后调用）。

数据驱动挑两张已审核、未锁定的订单（挑不到的记在说明里跳过）：
- pure：有发货单和发票、下游（发货单 / 退货单、销售出库单、销售发票）只来自这张订单；
- mixed：至少一张下游单据有外来明细（别的订单或没有来源订单）。
每张先快照订单表头、订单行和三类下游表头的整行，再走 vouchers/so_defines（fields 为 cdefine1 + cdefine2，
empty_values ["无"]，cascade=true）：

- 预演（pure）：200，current 是当前值，cascade 列出全部下游且都是 updated，before 的键是请求的两列、与快照一致；库不变、ufts 不变；
- expect 不符（pure）：409 state_mismatch，detail.current 的键恰好是 cdefine1、cdefine2、值是当前值，库不变；
- 真写：订单两列设成新值，只有订单两列、修改人类列，以及结论为 updated 的下游表头两列变了；
  下游的修改人、审核人等一格不动；有外来明细的下游单据 skipped / mixed_sources 且库里不变；
- 再写同样的值（pure）：changed=false，下游都是 unchanged，库不变（含行版本）。

还原用 SQL 把订单两列和修改人类列、下游表头两列按起始快照写回，整份快照（行版本列除外）与起始一致才算还原。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.head_fix_bodies import Snap, parse_snap
from co.e2e.casespec.so_defines_bodies import (
    PAIR,
    expect_status,
    hits_of,
    mismatch_problem,
    reply_write,
    send_sod,
    sod_body,
    untouched_problem,
    values_of,
)
from co.e2e.casespec.so_defines_cascade_bodies import Down, cascade_problem, cascade_write_problem, downs_of
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.so_defines_sql import restore_sql, writable
from co.e2e.casespec.so_defines_cascade_sql import DOWN_TYPES, cascade_pick_sql, cascade_snapshot_sql, down_restore_sql, mixed_sql
from co.e2e.casespec.sqlexec import run_guarded

_OK = (200, "")
_ID_MAX = 19
_KINDS = (
    ("pure", "下游只来自本订单、有发货单和发票的订单"),
    ("mixed", "下游单据有外来明细的订单"),
)
_UPDATED = ("updated", None)
_UNCHANGED = ("unchanged", None)
_MIXED = ("skipped", "mixed_sources")


@dataclass
class CascState:
    env: Env
    spec: CaseSpec
    notes: list[str]
    start: dict[str, Snap] = field(default_factory=dict)
    done: list[str] = field(default_factory=list)
    stamps: set[str] = field(default_factory=set)


@dataclass
class CascTrip:
    state: CascState
    so_id: str
    kind: str
    cur: Snap
    new: dict[str, str]
    mixed: set[Down]

    @property
    def now(self) -> dict[str, str | None]:
        return values_of(self.cur, self.so_id, PAIR)

    def want(self, plain: tuple[str, str | None]) -> dict[Down, tuple[str, str | None]]:
        """每张下游单据的期望结论：有外来明细的是 skipped / mixed_sources，其余是 plain。"""
        return {down: _MIXED if down in self.mixed else plain for down in downs_of(self.cur)}


def _snap(state: CascState, so_id: str) -> Snap:
    return parse_snap(sql_cells(run_guarded(state.env, cascade_snapshot_sql(so_id), state.spec.name)))


def _number(tag: str) -> str:
    return tag + os.urandom(3).hex().upper()


def _post(trip: CascTrip, body: dict, want: tuple[int, str], label: str) -> tuple[str, dict]:
    status, payload = send_sod(trip.state.env, body)
    return expect_status(status, payload, want, label), payload


def _same(trip: CascTrip, label: str) -> str:
    bad = untouched_problem(trip.cur, _snap(trip.state, trip.so_id), label)
    return f"{label}：{bad}"[:200] if bad else ""


def _pick(state: CascState, kind: str, used: tuple[str, ...]) -> str:
    cell = first_cell(run_guarded(state.env, cascade_pick_sql(kind, used), state.spec.name)).strip()
    return cell if cell.isdigit() and 0 < len(cell) <= _ID_MAX and cell[0] != "0" else ""


def _mixed_of(state: CascState, so_id: str) -> set[Down]:
    found: set[Down] = set()
    for cell in sql_cells(run_guarded(state.env, mixed_sql(so_id), state.spec.name)):
        tag, _sep, key = cell.partition("\t")
        if tag in DOWN_TYPES and key.strip().isdigit():
            found.add((tag, key.strip()))
    return found


# ---- 步骤 ----


def _dry(trip: CascTrip) -> str:
    body = sod_body(trip.so_id, trip.new, trip.now, cascade=True, dry=True)
    bad, payload = _post(trip, body, _OK, "连带下游预演")
    if bad:
        return bad
    if payload.get("dry_run") is not True:
        return "连带下游预演应 dry_run=true"
    wrong = reply_write(payload, True, trip.now) or cascade_problem(payload, trip.cur, trip.want(_UPDATED), PAIR)
    return f"连带下游预演：{wrong}"[:200] if wrong else _same(trip, "连带下游预演")


def _mismatch(trip: CascTrip) -> str:
    wrong = {**trip.now, "cdefine1": _number("NOPE")}
    body = sod_body(trip.so_id, trip.new, wrong, cascade=True)
    bad, payload = _post(trip, body, (409, "state_mismatch"), "连带下游 expect 不符")
    return bad or mismatch_problem(payload, trip.now) or _same(trip, "连带下游 expect 不符")


def _write(trip: CascTrip) -> str:
    bad, payload = _post(trip, sod_body(trip.so_id, trip.new, trip.now, cascade=True), _OK, "连带下游写入")
    if bad:
        return bad
    want = trip.want(_UPDATED)
    wrong = reply_write(payload, True, trip.new) or cascade_problem(payload, trip.cur, want, PAIR)
    if wrong:
        return f"连带下游写入：{wrong}"[:200]
    after = _snap(trip.state, trip.so_id)
    updated = {down for down, result in want.items() if result == _UPDATED}
    problem, stamps = cascade_write_problem(trip.cur, after, trip.so_id, trip.new, updated)
    trip.state.stamps.update(stamps)
    trip.cur = after
    return f"连带下游写入：{problem}"[:200] if problem else ""


def _again(trip: CascTrip) -> str:
    body = sod_body(trip.so_id, trip.new, dict(trip.new), cascade=True)
    bad, payload = _post(trip, body, _OK, "连带下游已是目标")
    if bad:
        return bad
    wrong = reply_write(payload, False, trip.new) or cascade_problem(payload, trip.cur, trip.want(_UNCHANGED), PAIR)
    return f"连带下游已是目标：{wrong}"[:200] if wrong else _same(trip, "连带下游已是目标")


def _plan(kind: str) -> list[Callable[[CascTrip], str]]:
    return [_dry, _mismatch, _write, _again] if kind == "pure" else [_write]


def _trip(state: CascState, so_id: str, kind: str) -> str:
    start = _snap(state, so_id)
    state.start[so_id] = start
    new = {"cdefine1": _number("E2P"), "cdefine2": _number("E2I")}
    trip = CascTrip(state, so_id, kind, start, new, _mixed_of(state, so_id))
    if kind == "mixed" and not trip.mixed:
        return "挑到的订单查不到有外来明细的下游单据"
    state.done.append("cascade-" + kind)
    for run in _plan(kind):
        bad = run(trip)
        if bad:
            return bad
    return ""


def cascade_cycle(state: CascState, used: tuple[str, ...]) -> str:
    """挑单并逐张走一轮；used 是普通几轮用过的订单 id（避开）。挑不到的记进 notes。"""
    for kind, label in _KINDS:
        found = _pick(state, kind, used + tuple(state.start))
        if not found:
            state.notes.append(f"没有{label}（连带下游）")
            continue
        bad = _trip(state, found, kind)
        if bad:
            return f"cascade-{kind}：{bad}"[:200]
    return ""


# ---- 还原 ----


def _repair(state: CascState, so_id: str, before: Snap, now: Snap) -> None:
    by_row: dict[tuple[str, str], dict[str, str]] = {}
    for tag, key, col in hits_of(before, now):
        if col in before.get(tag, {}).get(key, {}):
            by_row.setdefault((tag, key), {})[col] = before[tag][key][col]
    for (tag, key), cols in sorted(by_row.items()):
        if tag == "SO" and key == so_id:
            keep = {col: value for col, value in cols.items() if writable(col)}
            sql = restore_sql(so_id, keep) if keep else ""
        elif tag in DOWN_TYPES and set(PAIR) & set(cols):
            sql = down_restore_sql(tag, key, cols)
        else:
            sql = ""
        if sql:
            run_guarded(state.env, sql, state.spec.name)


def cascade_restore(state: CascState | None) -> str:
    """每张动过的订单：快照与起始一致（行版本列除外）才算还原；不一致先写回，再报仍有差异。"""
    if state is None:
        return ""
    left: list[str] = []
    for so_id, before in state.start.items():
        now = _snap(state, so_id)
        if not hits_of(before, now):
            continue
        _repair(state, so_id, before, now)
        hits = hits_of(before, _snap(state, so_id))
        if hits:
            left.append(f"订单 {so_id} {sorted(hits)[:3]}")
    return "连带下游还原后快照仍有差异 " + "；".join(left) if left else ""
