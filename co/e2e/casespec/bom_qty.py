"""物料清单用量精度与表头修改（`bom-load-qty`）：自建一个版本，核对写入的用量、损耗率、CreateUser，
只送行的修改保留表头，改生效日期时子件生效日期跟随，最后删除。

1. 新增：母件、子件沿用 bom_write 的挑单（bomw_parent / bomw_c1 / bomw_c2），远期生效日期与 bom-lifecycle、bom-create-bad 错开。
   行 10 用量分子带 6 位小数（文档：最多 6 位小数）、分母 3、子件损耗率 2.5；行 20 用量 1；挑得到固定换算率的子件
   时加行 30 看 U8 是否自动填辅计量单位（桥不送辅计量）。SQL 核对用量原样写入、FVFlag=1、子件生效日期 = 版本生效日期；
   vouchers/load 读出的用量与 SQL 相同。CreateUser 是不是操作员编码（文档待实测）只记进 detail。
2. 只送行（不带 head）改行 10 的用量和损耗率：表头版本说明、母件损耗率、生效日期保持原值（桥总是送表头）。
   行 30 若带辅计量，先改它的用量应 409「带辅计量」。
3. 只改表头 eff_date：版本生效日期和各行子件生效日期都跟到新日期，用量不变。
4. 删除并回读三张表没有残留。任何一步失败都先删掉新建的版本（删不掉留给快照还原）。
固定用量（FVFlag≠1）的行 API 造不出来（新增固定送变动用量），不在本用例。detail 记 iStrsQuanDecDgt 和观测到的值。
步骤 kind 为 "custom"，sql_name 是操作名（BOM_MORE_OPS 并入 ops_run）。
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.bom_qty_sql import (
    MEMO,
    aux_pick_sql,
    digits_sql,
    gone_sql,
    head_sql,
    line_sql,
    max_bom_sql,
    orphan_sql,
)
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

_KIND = "bom"
_CREATE = "/v1/vouchers/create"
_UPDATE = "/v1/vouchers/update"
_LOAD = "/v1/vouchers/load"
_DELETE = "/v1/vouchers/delete"
_NAME = "bom-load-qty"
_OP = "bom_load_qty"
# 生效日期：登录日期之后，与 bom-lifecycle（3000）、bom-create-bad（3007）错开。
_AHEAD_NEW = 3021
_AHEAD_EFF = 3028
_QTY_N = Decimal("1.234567")
_QTY_D = Decimal("3")
_SCRAP = Decimal("2.5")
_QTY_N2 = Decimal("2.5")
_SCRAP2 = Decimal("5")
_AUX_SEQ = 30


class _Fail(Exception):
    pass


def _eff(env: Env, days: int) -> str:
    return (dt.date.fromisoformat(env.today) + dt.timedelta(days=days)).isoformat()


def _cell(env: Env, spec: CaseSpec, query: str) -> str:
    return first_cell(run_guarded(env, query, spec.name)).strip()


def _parts(env: Env, spec: CaseSpec, query: str, count: int, what: str) -> list[str]:
    cell = _cell(env, spec, query)
    parts = cell.split("|")
    if not cell or len(parts) != count:
        raise _Fail(f"{what}回读 {cell or '空'}，期望 {count} 列")
    return parts


def _dec(text: str, what: str) -> Decimal:
    try:
        return Decimal(str(text).strip())
    except (InvalidOperation, ValueError) as exc:
        raise _Fail(f"{what} 不是数字：{text}") from exc


def _send(env: Env, route: str, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, route, fields)


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {str(payload.get('message') or '')[:80]}"


# ---- 新增与删除 ----


def _aux_pick(env: Env, spec: CaseSpec) -> str:
    used = {env.bag.get(key, "") for key in ("bomw_parent", "bomw_c1", "bomw_c2")}
    for code in sql_cells(run_guarded(env, aux_pick_sql(), spec.name)):
        if code and code not in used:
            return code
    return ""


def new_body(env: Env, aux: str) -> dict:
    lines = [
        {"inv_code": env.bag["bomw_c1"], "base_qty_n": float(_QTY_N), "base_qty_d": float(_QTY_D),
         "comp_scrap": float(_SCRAP), "sort_seq": 10},
        {"inv_code": env.bag["bomw_c2"], "base_qty_n": 1, "base_qty_d": 1, "sort_seq": 20},
    ]
    if aux:
        lines.append({"inv_code": aux, "base_qty_n": 1, "base_qty_d": 1, "sort_seq": _AUX_SEQ})
    head = {"inv_code": env.bag["bomw_parent"], "version_desc": MEMO, "eff_date": _eff(env, _AHEAD_NEW)}
    return {"type": _KIND, "head": head, "lines": lines}


def _made(payload: dict) -> str:
    raw = payload.get("id")
    if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
        return str(raw)
    return ""


def _drop(env: Env, spec: CaseSpec, made: str) -> str:
    """删除新建的版本并回读；返回问题说明，成功为空串。"""
    try:
        status, payload = _send(env, _DELETE, {"type": _KIND, "id": int(made)})
        if status != 200 or payload.get("deleted") is not True:
            return f"删除物料清单 {made} 返回 {_brief(status, payload)}，留给快照还原"
        left = _cell(env, spec, gone_sql(made))
    except (RuntimeError, ValueError, OSError) as exc:
        return f"删除物料清单 {made} 出错（{str(exc)[:60]}），留给快照还原"
    return "" if left == "0" else f"删除物料清单 {made} 后回读残留 {left or '?'} 行"


def _orphan(env: Env, spec: CaseSpec, before: str) -> str:
    try:
        found = _cell(env, spec, orphan_sql(before or "0", _eff(env, _AHEAD_NEW)))
    except (RuntimeError, ValueError) as exc:
        return "查找残留版本出错：" + str(exc)[:60]
    if found in ("", "0"):
        return "库里没有新建的版本"
    return _drop(env, spec, found) or "已删除残留版本 " + found


# ---- 核对 ----


def _line(env: Env, spec: CaseSpec, made: str, seq: int) -> list[str]:
    return _parts(env, spec, line_sql(made, seq), 6, f"行 {seq} ")


def _head(env: Env, spec: CaseSpec, made: str) -> list[str]:
    return _parts(env, spec, head_sql(made), 7, "表头")


def _expect_line(row: list[str], seq: int, want: tuple[Decimal, Decimal, Decimal, str]) -> None:
    qty_n, qty_d, scrap, eff = want
    got = (_dec(row[0], "用量分子"), _dec(row[1], "用量分母"), _dec(row[2], "损耗率"), row[5])
    if got != (qty_n, qty_d, scrap, eff):
        raise _Fail(f"行 {seq} 回读 {row[0]}/{row[1]}/{row[2]}/{row[5]}，期望 {qty_n}/{qty_d}/{scrap}/{eff}")


def _expect_head(head: list[str], eff: str, lines: int) -> None:
    status, _creator, _modifier, got_eff, scrap, desc, count = head
    if status != "1" or got_eff != eff or _dec(scrap, "母件损耗率") != 0 or desc != "1" or count != str(lines):
        raise _Fail(f"表头回读 状态 {status} 生效 {got_eff} 母件损耗 {scrap} 说明保持 {desc} 行数 {count}，"
                    f"期望 1 / {eff} / 0 / 1 / {lines}")


def _load_qty(env: Env, made: str, sql_qty: str) -> None:
    status, payload = _send(env, _LOAD, {"type": _KIND, "id": int(made)})
    if status != 200:
        raise _Fail("读取物料清单返回 " + _brief(status, payload))
    lines = payload.get("lines") if isinstance(payload.get("lines"), list) else []
    got = [line.get("base_qty_n") for line in lines if isinstance(line, dict) and str(line.get("sort_seq")) == "10"]
    if len(got) != 1 or _dec(str(got[0]), "读取用量") != _dec(sql_qty, "回读用量"):
        raise _Fail(f"vouchers/load 行 10 用量 {got}，SQL {sql_qty}")


def _created(env: Env, spec: CaseSpec, made: str, aux: str) -> tuple[list[str], bool]:
    eff = _eff(env, _AHEAD_NEW)
    head = _head(env, spec, made)
    _expect_head(head, eff, 3 if aux else 2)
    row10 = _line(env, spec, made, 10)
    _expect_line(row10, 10, (_QTY_N, _QTY_D, _SCRAP, eff))
    if row10[3] != "1":
        raise _Fail("行 10 FVFlag 回读 " + row10[3] + "，期望 1（变动用量）")
    _expect_line(_line(env, spec, made, 20), 20, (Decimal(1), Decimal(1), Decimal(0), eff))
    _load_qty(env, made, row10[0])
    creator = head[1]
    if not creator:
        raise _Fail("CreateUser 为空")
    same = creator.lower() == str(env.operator.code).strip().lower()
    notes = [f"6 位小数用量原样写入 {row10[0]}", "CreateUser 是操作员编码" if same else f"CreateUser={creator}（不是操作员编码）"]
    filled = False
    if aux:
        unit = _line(env, spec, made, _AUX_SEQ)[4]
        filled = bool(unit)
        notes.append(f"辅计量候选 {aux}：U8 " + (f"填了辅计量单位 {unit}" if filled else "未填辅计量单位"))
    return notes, filled


def _aux_refused(env: Env, made: str) -> str:
    body = {"type": _KIND, "id": int(made), "lines": [{"op": "update", "sort_seq": _AUX_SEQ, "base_qty_n": 2}]}
    status, payload = _send(env, _UPDATE, body)
    if (status, payload.get("code")) != (409, "state_mismatch") or "辅计量" not in str(payload.get("message") or ""):
        raise _Fail("辅计量行改用量应 409「带辅计量」，实际 " + _brief(status, payload))
    return "辅计量行改用量 409"


def _edit_lines(env: Env, spec: CaseSpec, made: str, aux_filled: bool, count: int) -> list[str]:
    notes = [_aux_refused(env, made)] if aux_filled else []
    change = {"op": "update", "sort_seq": 10, "base_qty_n": float(_QTY_N2), "comp_scrap": float(_SCRAP2)}
    status, payload = _send(env, _UPDATE, {"type": _KIND, "id": int(made), "lines": [change]})
    if status != 200:
        raise _Fail("只送行的修改应成功，实际 " + _brief(status, payload))
    eff = _eff(env, _AHEAD_NEW)
    head = _head(env, spec, made)
    _expect_head(head, eff, count)
    _expect_line(_line(env, spec, made, 10), 10, (_QTY_N2, _QTY_D, _SCRAP2, eff))
    notes.append(f"只送行：表头保持，ModifyUser={head[2] or '空'}")
    return notes


def _edit_eff(env: Env, spec: CaseSpec, made: str, count: int) -> list[str]:
    eff = _eff(env, _AHEAD_EFF)
    status, payload = _send(env, _UPDATE, {"type": _KIND, "id": int(made), "head": {"eff_date": eff}})
    if status != 200:
        raise _Fail("改生效日期应成功，实际 " + _brief(status, payload))
    _expect_head(_head(env, spec, made), eff, count)
    _expect_line(_line(env, spec, made, 10), 10, (_QTY_N2, _QTY_D, _SCRAP2, eff))
    _expect_line(_line(env, spec, made, 20), 20, (Decimal(1), Decimal(1), Decimal(0), eff))
    return ["改生效日期：子件生效日期跟到 " + eff]


def _cycle(env: Env, spec: CaseSpec, made: str, aux: str) -> list[str]:
    count = 3 if aux else 2
    notes, filled = _created(env, spec, made, aux)
    notes += _edit_lines(env, spec, made, filled, count)
    notes += _edit_eff(env, spec, made, count)
    return notes


def bom_load_qty(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    for key in ("bomw_parent", "bomw_c1", "bomw_c2"):
        if not env.bag.get(key):
            return "N/A", "测试账套挑不到可做物料清单的母件和子件"
    digits = _cell(env, spec, digits_sql())
    aux = _aux_pick(env, spec)
    before = _cell(env, spec, max_bom_sql())
    status, payload = _send(env, _CREATE, new_body(env, aux))
    made = _made(payload) if status == 200 else ""
    if not made:
        return "FAIL", f"新增应成功并返回 id，实际 {_brief(status, payload)}；{_orphan(env, spec, before)}"[:240]
    notes = ["iStrsQuanDecDgt=" + (digits or "?")]
    try:
        notes += _cycle(env, spec, made, aux)
    except (_Fail, RuntimeError, ValueError) as exc:
        problem = _drop(env, spec, made)
        return "FAIL", "；".join([*notes, str(exc), problem or "已删除 " + made])[:240]
    problem = _drop(env, spec, made)
    if problem:
        return "FAIL", "；".join([*notes, problem])[:240]
    return "PASS", "；".join([*notes, "已删除"])[:300]


BOM_MORE_OPS = {_OP: bom_load_qty}


def bom_more_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name=_OP, id_from="none")
    title = "物料清单用量精度：6 位小数用量与损耗率原样写入、CreateUser、只送行时表头保持、改生效日期子件跟随，随即删除"
    # 与 bom-lifecycle、bom-create-bad 共用 bom_write 挑单的母件（need 键相同即互斥），新增版本占同一母件的版本号。
    tags = frozenset(("bom", "new:bom", "w:bom"))
    needs = ("bomw_parent", "bomw_c1", "bomw_c2")
    routes = (_CREATE, _UPDATE, _LOAD, _DELETE)
    return [CaseSpec(_NAME, title, needs, (step,), True, optional=True, tags=tags, routes=routes)]
