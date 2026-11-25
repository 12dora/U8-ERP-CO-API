"""voucher-head-fix 用例共用的上下文：挑到的样本、一轮往返的状态、SQL 与调用的小工具。不连接服务器。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.head_fix_bodies import Snap, parse_snap, same_problem, send_hf
from co.e2e.casespec.arc_cus_person_bodies import expect
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.head_fix_sql import snapshot_sql
from co.e2e.casespec.sqlexec import run_guarded

OK = (200, "")
ID = re.compile(r"^[1-9][0-9]{0,18}\Z")
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


class NotApplicable(Exception):
    """账套里没有这个用例需要的数据：记 N/A，不是失败。"""


@dataclass(frozen=True)
class Pick:
    dlid: str
    addr: str
    person: str


@dataclass
class Ctx:
    env: Env
    spec: CaseSpec
    notes: list[str] = field(default_factory=list)
    start: dict[str, Snap] = field(default_factory=dict)
    done: list[str] = field(default_factory=list)
    reports: list[str] = field(default_factory=list)
    strict: set[str] = field(default_factory=set)


@dataclass
class Trip:
    """一轮往返：一张发货单，kind 是样本类别（single、merged、merged_new、already_new、null）。"""

    ctx: Ctx
    pick: Pick
    kind: str
    cur: Snap
    old: dict[str, str]
    fields: dict[str, str]
    undo: dict = field(default_factory=dict)

    @property
    def dlid(self) -> str:
        return self.pick.dlid

    @property
    def want(self) -> dict[str, str]:
        """请求里带的键在表头当前的值（expect / 反向调用的字段）。"""
        return {name: self.old[name] for name in self.fields}

    @property
    def strict(self) -> bool:
        """地址原本是 NULL 的样本：NULL 必须还原成 NULL，不放宽归一化。"""
        return self.kind == "null"


def sql(ctx: Ctx, query: str) -> str:
    return first_cell(run_guarded(ctx.env, query, ctx.spec.name)).strip()


def snap(ctx: Ctx, dlid: str) -> Snap:
    return parse_snap(sql_cells(run_guarded(ctx.env, snapshot_sql(dlid), ctx.spec.name)))


def post(ctx: Ctx, body: dict, want: tuple[int, str], label: str) -> tuple[str, dict]:
    status, payload = send_hf(ctx.env, body)
    return expect(status, payload, want, label), payload


def call(trip: Trip, body: dict, want: tuple[int, str], label: str) -> str:
    bad, _payload = post(trip.ctx, body, want, label)
    return bad


def same(trip: Trip, label: str) -> str:
    """库里的快照应和 trip.cur 逐格一致（目标列按归一化比较，null 样本严格）。"""
    bad = same_problem(trip.cur, snap(trip.ctx, trip.dlid), not trip.strict)
    return f"{label}：{bad}"[:200] if bad else ""


def _clean(text: str, limit: int) -> bool:
    return 1 <= len(text) <= limit and text == text.strip() and _CTRL.search(text) is None


def parse_pick(cell: str) -> Pick | None:
    parts = cell.split("\t")
    if len(parts) != 3 or ID.fullmatch(parts[0].strip()) is None:
        return None
    dlid, addr, person = parts[0].strip(), parts[1].rstrip("\r"), parts[2].rstrip("\r")
    return Pick(dlid, addr, person) if _clean(addr, 30) and _clean(person, 20) else None


def pick_one(ctx: Ctx, queries: tuple[str, ...]) -> Pick | None:
    """按顺序试几条挑单 SQL（先严后宽），第一条有结果的算数。"""
    for query in queries:
        found = parse_pick(first_cell(run_guarded(ctx.env, query, ctx.spec.name)))
        if found is not None:
            return found
    return None
