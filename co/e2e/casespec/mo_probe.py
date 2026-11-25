"""生产订单新增探查`mo-create-probe`：非自制件、没有物料清单的自制件。

1. 非自制件（bSelf = 0）：桥在调用 U8 之前 400 bad_request「不是自制件」，库里没有带该存货的新订单。
2. 没有物料清单的自制件：交给 U8。U8 新增成功时核对表头、明细各 1 行、子件 0 行，随即删除并回读确认不留；
   U8 拒绝时应是 409 u8_rejected（原文记进 detail）。两种都算通过，detail 写明 U8 的实际行为，供文档定稿。
两种存货都挑不到记 N/A；只挑到一种就只跑那一种。部门、订单类别沿用生产订单新增挑单（moc_dept / moc_type）。
步骤 kind 为 "custom"，sql_name 是操作名（MO_PROBE_OPS 并入 ops_run）。新建的订单在本步内删除，删除失败记 FAIL 并留给快照还原。
"""

from __future__ import annotations

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.mo_create import _line
from co.e2e.casespec.mo_probe_sql import bomless_inv_sql, bought_inv_sql, max_mo_sql, mo_rows_sql, new_mo_sql, safe_inv
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

_KIND = "production_order"
_CREATE = "/v1/vouchers/create"
_DELETE = "/v1/vouchers/delete"
_NAME = "mo-create-probe"
MEMO = "u8co e2e 无物料清单"


class _Fail(Exception):
    pass


def _cell(env: Env, spec: CaseSpec, query: str) -> str:
    return first_cell(run_guarded(env, query, spec.name)).strip()


def _create(env: Env, inv: str) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update({"type": _KIND, "head": {"remark": MEMO}, "lines": [_line(env, inv, 2)]})
    return invoke(env.client, _CREATE, fields)


def _bought(env: Env, spec: CaseSpec, inv: str) -> str:
    before = _cell(env, spec, max_mo_sql())
    status, payload = _create(env, inv)
    if status == 200:
        made = _made(payload)
        note = (_drop(env, spec, made) or "已删除 " + made) if made else "拿不到新 id，留给快照还原"
        raise _Fail(f"非自制件 {inv} 新增应 400，实际成功（{note}）")
    if (status, payload.get("code")) != (400, "bad_request") or "自制" not in str(payload.get("message") or ""):
        raise _Fail(f"非自制件 {inv} 应 400「不是自制件」，实际 {status} {payload.get('code')} {payload.get('message')}"[:200])
    left = _cell(env, spec, new_mo_sql(before or "0", inv))
    if left != "0":
        raise _Fail(f"非自制件 {inv} 被拒后库里有 {left or '?'} 张新订单")
    return f"非自制件 {inv} 400"


def _made(payload: dict) -> str:
    raw = payload.get("id")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return str(raw)
    return ""


def _drop(env: Env, spec: CaseSpec, made: str) -> str:
    fields = auth_fields(env)
    fields.update({"type": _KIND, "id": int(made)})
    status, payload = invoke(env.client, _DELETE, fields)
    if status != 200 or payload.get("deleted") is not True:
        return f"删除 {made} 返回 {status} {payload.get('code')}，留给快照还原"
    gone = _cell(env, spec, mo_rows_sql(made))
    if gone != "0|0|0":
        return f"删除 {made} 后回读 {gone or '空'}，期望 0|0|0"
    return ""


def _bomless(env: Env, spec: CaseSpec, inv: str) -> str:
    status, payload = _create(env, inv)
    if status == 409 and payload.get("code") == "u8_rejected":
        return f"无物料清单的自制件 {inv}：U8 拒绝（{str(payload.get('message') or '')[:80]}）"
    if status != 200:
        raise _Fail(f"无物料清单的自制件 {inv} 应成功或 409 u8_rejected，实际 {status} {payload.get('code')} {payload.get('message')}"[:200])
    made = _made(payload)
    if not made:
        raise _Fail(f"无物料清单的自制件 {inv} 新增成功但响应没有 id，留给快照还原")
    rows = ""
    try:
        rows = _cell(env, spec, mo_rows_sql(made))
    finally:
        # 回读出错也先删掉新建的订单。
        problem = _drop(env, spec, made)
    if problem:
        raise _Fail(problem)
    if rows != "1|1|0":
        raise _Fail(f"无物料清单的订单 {made} 回读（表头|明细|子件）{rows or '空'}，期望 1|1|0")
    return f"无物料清单的自制件 {inv}：U8 新增成功、子件 0 行，已删除"


def _pick(env: Env, spec: CaseSpec, query: str) -> str:
    cell = _cell(env, spec, query)
    return safe_inv(cell) if cell else ""


def mo_probe(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    bought, bomless = _pick(env, spec, bought_inv_sql()), _pick(env, spec, bomless_inv_sql())
    if not bought and not bomless:
        return "N/A", "测试账套挑不到非自制件，也挑不到没有物料清单的自制件"
    notes = []
    try:
        if bought:
            notes.append(_bought(env, spec, bought))
        if bomless:
            notes.append(_bomless(env, spec, bomless))
    except _Fail as exc:
        return "FAIL", "；".join([*notes, str(exc)])[:240]
    return "PASS", "；".join(notes)[:240]


MO_PROBE_OPS = {"mo_probe": mo_probe}


def mo_probe_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name="mo_probe", id_from="none")
    title = "生产订单新增探查：非自制件 400；没有物料清单的自制件看 U8 接受（子件 0 行，随即删除）还是拒绝"
    # 同 mo-create-bad：可能真的建出订单（占单据号、按 BOM 展开），与改 BOM 版本的用例不同时跑。
    tags = frozenset(("bom", "new:production_order", "w:production_order", "stock"))
    needs = ("moc_inv", "moc_dept", "moc_type")
    return [CaseSpec(_NAME, title, needs, (step,), True, optional=True, tags=tags, routes=(_CREATE, _DELETE))]
