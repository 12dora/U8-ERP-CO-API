"""「临时改选项 / 造数据，跑完原样恢复」类用例的公共部分（openings_pu_unpost、exch_floating、credit_flip、
price_list、so_lock_other 共用）。

恢复设计同 openings_live：改动之后的全部步骤包进 with_restore——步骤抛出的任何异常都转成 FAIL，finally 里依次执行
每一项恢复（各自返回问题说明，空串表示正常），恢复本身出错也不再抛，原因写进判定；任一项恢复有问题整条记 FAIL，
快照还原兜底。运行被中断（Run.stop）后 SQL 通道拒绝再发语句，这时改动留给快照还原。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke
from co.e2e.casespec.tier2 import tier2_refused

_ROWCOUNT = re.compile(r"^-?[0-9]{1,12}\Z")
_LITERAL_BAD = re.compile(r"[\x00-\x1f]")

Body = Callable[[], tuple[str, str]]
Undo = Callable[[], str]


class Fail(Exception):
    """步骤里断言失败：with_restore 把它转成 FAIL，照常恢复。"""


def send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, route, fields)


def brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def not_test(status: int, payload: dict) -> bool:
    return tier2_refused(status, payload)


def ok(env: Env, route: str, body: dict, what: str) -> dict:
    status, payload = send(env, route, body)
    if status != 200:
        raise Fail(f"{what}应 200，实际 " + brief(status, payload))
    return payload


def refused(env: Env, route: str, body: dict, want: tuple[int, str, str], what: str) -> None:
    """应被拒：状态码、错误码相同，message 含 want[2]（空串不查原文）。"""
    status, payload = send(env, route, body)
    code, text = want[1], want[2]
    if status != want[0] or payload.get("code") != code or text not in str(payload.get("message") or ""):
        raise Fail(f"{what}应 {want[0]} {code}「{text}」，实际 " + brief(status, payload))


def row(env: Env, spec: CaseSpec, query: str) -> list[str]:
    """第一行 ROW: 按 | 拆开、去空格；没有行时为空列表。"""
    cell = first_cell(run_guarded(env, query, spec.name))
    return [part.strip() for part in cell.split("|")] if cell else []


def rows(env: Env, spec: CaseSpec, query: str) -> list[list[str]]:
    return [[part.strip() for part in cell.split("|")] for cell in sql_cells(run_guarded(env, query, spec.name))]


def rowcount(env: Env, spec: CaseSpec, query: str) -> int:
    """写语句末尾 SELECT N'ROW:' + @@ROWCOUNT 的改动行数；读不到为 -1。"""
    got = row(env, spec, query)
    return int(got[0]) if got and _ROWCOUNT.fullmatch(got[0]) else -1


def dec(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def same_num(got: object, want: object) -> bool:
    """数值相等（差 < 0.000001）；want 为 None 时要求 got 也是 None。"""
    if want is None:
        return got is None
    a, b = dec(got), dec(want)
    return a is not None and b is not None and abs(a - b) < Decimal("0.000001")


def nliteral(text: str, max_len: int) -> str:
    """N'...' 字面量：单引号成对转义；控制字符、空串、超长拒绝。"""
    if not text or len(text) > max_len or _LITERAL_BAD.search(text):
        raise ValueError("文本不能拼进 SQL")
    return "N'" + text.replace("'", "''") + "'"


def attempt(body: Body) -> tuple[str, str]:
    try:
        return body()
    except Fail as exc:
        return "FAIL", str(exc)[:240]
    except Exception as exc:  # 异常类型不定（桥调用、SQL 通道、断言），一律记失败，后面照常恢复
        return "FAIL", f"{type(exc).__name__}：{exc}"[:240]


def _safe(undo: Undo, what: str) -> str:
    try:
        return undo()
    except Exception as exc:  # 恢复阶段不能再抛，原因写进判定
        return f"{what}出错（留给快照还原）：{type(exc).__name__} {exc}"[:200]


def with_restore(body: Body, undos: tuple[tuple[str, Undo], ...]) -> tuple[str, str]:
    """跑 body；无论成败依次执行 undos（名称, 动作）。有恢复问题时整条 FAIL，并带上 body 的失败原因。"""
    result: tuple[str, str] = ("FAIL", "步骤没有跑完")
    notes: list[str] = []
    try:
        result = attempt(body)
    finally:
        for what, undo in undos:
            note = _safe(undo, what)
            if note:
                notes.append(note)
    if not notes:
        return result
    head = result[1] + "；" if result[0] == "FAIL" else ""
    return "FAIL", (head + "；".join(notes))[:300]
