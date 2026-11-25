"""发货单参照销售订单生单的表体扩展自定义项 cbdefineN（vouchers/generate type=dispatch，DispatchLists_extradefine）。

dispatch-gen-line-bdefine：按列目录查 DispatchLists_extradefine 的 cbdefine5 / 6 / 7（账套没有的列跳过，三列都没有记 N/A），
  按列类型给两行各编一个不同的值（文本 E2E + 随机 + 行标、截到列宽；数值 3 / 4；日期今天 / 昨天）→
  挑一个不管货位的仓库里可用量 >= 4 的可销售简单存货，自建 2 行销售订单（各 3 个、含税单价 1.5）并审核 →
  拒绝：账套没有的一列 cbdefineN（编号最大的，没有时跳过）→ 400，消息含「扩展自定义项」，挂这两条订单行的发货单行数仍为 0 →
  预演：同一正文带 dry_run → 200、dry_run=true，发货单行数仍为 0；docs 里两条发货单行（按 iSOsID）各读回自己的值 →
  正式（带幂等键，每行发 2 个，部分数量）→ 200、lines=2；库里 DispatchLists_extradefine（按 iDLsID）和 vouchers/load 的行上各读回自己的值，没有串行；
  行金额同 U8 界面：账套报价含税（SA.bSalePrice）时价税合计 = round(2 × 1.5, 2) = 3.00 = 无税金额 + 税额（按无税单价算是 2.99），含税单价仍为 1.5，订单行、发货单行 bsaleprice 等于账套选项
  （不含税时只核价税合计 = 无税金额 + 税额）→
  同一幂等键、同一正文重放 → 200、同一张发货单，挂订单行的发货单行数仍为 2 →
  删除发货单、弃审删除订单。
清理：先按用例自己的订单行对账（响应 504、没给 id 或预演误落库的发货单也找出来并删除），清理之后按订单行和记下的
iDLsID 核对发货单行、扩展表行都已没有。
请求里明确给出这些列：U8 生单是否自己把订单行的 cbdefineN 抄到发货单行未经验证（u8-notes），用例不依赖。
步骤 kind 为 "custom"，sql_name 是操作名（DISPATCH_BDEFINE_OPS 并入 ops_run）。挑不到数据记 N/A。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _MEMO, _SALE_TAGS, _Fail, _NotApplicable, _Run
from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.dispatch_add_sql import pick_sql
from co.e2e.casespec.dispatch_line_bdefine_sql import (
    WANTED,
    absent_col_sql,
    amount_problem,
    amounts_sql,
    flag_col_sql,
    flags_sql,
    option_on,
    sale_price_option_sql,
    cols_sql,
    disp_by_lines_sql,
    disp_count_sql,
    ext_col,
    ext_lines_sql,
    gone_sql,
    kind_of,
    so_lines_sql,
)

_GEN = "/v1/vouchers/generate"
_CREATE = "/v1/vouchers/create"
_VERIFY = "/v1/vouchers/verify"
_DELETE = "/v1/vouchers/delete"
_LOAD = "/v1/vouchers/load"
_QTY = 2
_LINES = 2
# 订单每行 3 个、发货 2 个（部分）。含税单价 1.5、税率 13%：U8 界面 价税合计 = round(2 × 1.5, 2) = 3.00；
# 按无税单价 1.327434 算是 2.65 + 0.34 = 2.99，差一分（行上 bsaleprice=0 时 U8 就这样算；桥按账套选项写 bsaleprice）。
_SO_QTY = 3
_PRICE = 1.5


@dataclass
class _Col:
    name: str
    kind: str
    values: tuple[str, str]


@dataclass
class _State:
    """清理对账用：用例自己的订单行、已登记删除的发货单、见过的发货单行 iDLsID。"""

    so_lines: list[str] = field(default_factory=list)
    dls: set[int] = field(default_factory=set)
    dl_lines: list[str] = field(default_factory=list)


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _values(kind: str, width: int, today: str) -> tuple[str, str]:
    """两行各一个不同的值。"""
    if kind == "number":
        return "3", "4"
    if kind == "date":
        return today, (date.fromisoformat(today) - timedelta(days=1)).isoformat()
    base = ("E2E" + os.urandom(2).hex().upper())[: max(width - 1, 0)] if width > 0 else "E2E" + os.urandom(2).hex().upper()
    return base + "A", base + "B"


def _cols(run: _Run) -> list[_Col]:
    """账套有的 cbdefine5–7 和两行的值；类型不认识或文本列宽为 0 的跳过。"""
    cols: list[_Col] = []
    for row in run.cells(cols_sql()):
        parts = row.strip().split("|")
        if len(parts) != 3 or not ext_col(parts[0]) or not kind_of(parts[1]):
            continue
        kind = kind_of(parts[1])
        width = int(parts[2]) if parts[2].lstrip("-").isdigit() else 0
        if kind == "text" and width == 0:
            continue
        cols.append(_Col(ext_col(parts[0]), kind, _values(kind, width, run.env.today)))
    return cols


def _same(kind: str, want: str, got: object) -> bool:
    text = "" if got is None else str(got).strip()
    if kind == "date":
        return text[:10] == want
    if kind == "number":
        try:
            return Decimal(text) == Decimal(want)
        except InvalidOperation:
            return False
    return text.lower() == want.lower()


def _check_row(where: str, row: dict, cols: list[_Col], index: int) -> None:
    low = {str(key).lower(): value for key, value in row.items()}
    for col in cols:
        if not _same(col.kind, col.values[index], low.get(col.name)):
            raise _Fail(f"{where} {col.name} 应为 {col.values[index]}，实际 {low.get(col.name)}")


def _order(run: _Run, parts: list[str]) -> int:
    cus, st, dep, person, inv = (safe_lot(part) for part in parts[:5])
    head = {
        "cCusCode": cus, "cSTCode": st, "cDepCode": dep, "cPersonCode": person, "cBusType": "普通销售",
        "cexch_name": "人民币", "iExchRate": 1, "iTaxRate": 13, "dDate": run.env.today, "cMemo": _MEMO,
    }
    pre = (date.fromisoformat(run.env.today) + timedelta(days=3)).isoformat()
    line = {"cInvCode": inv, "iQuantity": _SO_QTY, "iTaxUnitPrice": _PRICE, "iTaxRate": 13, "dPreDate": pre}
    so = run.made(_CREATE, {"type": "sale_order", "head": head, "lines": [dict(line), dict(line)]}, "新建 2 行销售订单")
    run.drop("sale_order", so)
    run.ok(_VERIFY, {"type": "sale_order", "id": so, "action": "verify"}, "审核销售订单")
    run.unverify("sale_order", so)
    return so


def _body(so: int, so_lines: list[str], wh: str, today: str, cols: list[_Col]) -> dict:
    lines = []
    for index, src in enumerate(so_lines):
        row: dict = {"source_line_id": int(src), "quantity": _QTY, "cWhCode": wh}
        row.update({col.name: col.values[index] for col in cols})
        lines.append(row)
    return {"type": "dispatch", "id": so, "head": {"cWhCode": wh, "dDate": today}, "lines": lines}


def _no_dispatch(run: _Run, state: _State, what: str) -> None:
    if run.cell(disp_count_sql(state.so_lines)) != "0":
        raise _Fail(f"{what}后不应有挂这两条订单行的发货单行")


def _refuse(run: _Run, body: dict, state: _State) -> str:
    col = ext_col(run.cell(absent_col_sql()))
    if not col:
        return "cbdefine1–99 都有，未做缺列拒绝"
    lines = [dict(body["lines"][0], **{col: "X"}), body["lines"][1]]
    status, payload = run.send(_GEN, {**body, "lines": lines})
    if status != 400 or "扩展自定义项" not in str(payload.get("message")):
        raise _Fail(f"缺列 {col} 应 400「账套没有扩展自定义项」，实际 {_brief(status, payload)}")
    _no_dispatch(run, state, "被拒")
    return f"缺列 {col} 400"


def _by_src(rows: list, key: str) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for row in rows:
        if isinstance(row, dict):
            low = {str(k).lower(): v for k, v in row.items()}
            found[str(low.get(key))] = row
    return found


def _check_lines(where: str, rows: list, key: str, keys: list[str], cols: list[_Col]) -> None:
    """rows 按 key（isosid / idlsid）对上 keys 的顺序，第 i 条读回第 i 组值。"""
    found = _by_src(rows, key)
    for index, want in enumerate(keys):
        if want not in found:
            raise _Fail(f"{where}没有 {key}={want} 的行")
        _check_row(f"{where} {key}={want}", found[want], cols, index)


def _dry(run: _Run, body: dict, state: _State, cols: list[_Col]) -> None:
    payload = run.ok(_GEN, {**body, "dry_run": True}, "预演参照订单生成发货单")
    if payload.get("dry_run") is not True:
        raise _Fail("预演响应应带 dry_run=true")
    _no_dispatch(run, state, "预演")
    for doc in payload.get("docs") or []:
        if isinstance(doc, dict) and doc.get("type") == "dispatch" and doc.get("lines"):
            _check_lines("预演 docs 的发货单行", doc["lines"], "isosid", state.so_lines, cols)
            return
    raise _Fail("预演 docs 里没有发货单表体")


def _made(run: _Run, body: dict, state: _State) -> int:
    payload = run.ok(_GEN, body, "参照订单生成发货单（表体扩展自定义项，带幂等键）")
    dl = payload.get("id")
    if type(dl) is not int or dl <= 0:
        raise _Fail("响应没有新发货单 id")
    run.drop("dispatch", dl)
    state.dls.add(dl)
    if payload.get("lines") != _LINES:
        raise _Fail(f"响应 lines 应为 {_LINES}，实际 {payload.get('lines')}")
    return dl


def _stored(run: _Run, dl: int, state: _State, cols: list[_Col]) -> list[str]:
    """库里每条发货单行按来源订单行读回自己的值；返回按订单行顺序的 iDLsID。"""
    rows = [row.strip().split("|") for row in run.cells(ext_lines_sql(str(dl), [(c.name, c.kind) for c in cols]))]
    state.dl_lines.extend(row[0] for row in rows if row and row[0].isdigit())
    by_src = {row[1]: row for row in rows if len(row) == 2 + len(cols)}
    if len(rows) != _LINES or sorted(by_src) != sorted(state.so_lines):
        raise _Fail(f"发货单应有 {_LINES} 行且来源为订单行 {state.so_lines}，实际 {rows}")
    for index, src in enumerate(state.so_lines):
        for pos, col in enumerate(cols):
            got = by_src[src][2 + pos]
            if not _same(col.kind, col.values[index], got):
                raise _Fail(f"库里订单行 {src} 的 {col.name} 应为 {col.values[index]}，实际 {got or '空'}")
    return [by_src[src][0] for src in state.so_lines]


def _amounts(run: _Run, dl: int, taxed: bool) -> None:
    """部分数量的发货单行同 U8 界面：价税合计 = 无税金额 + 税额，账套报价含税时价税合计 = round(数量 × 含税单价, 2)；
    含税单价仍是订单的。"""
    rows = run.cells(amounts_sql("dispatch", str(dl)))
    problem = amount_problem(rows, taxed)
    if problem:
        raise _Fail(f"发货单 {dl} 行金额：{problem}")
    for row in rows:
        price = row.strip().split("|")[2]
        if Decimal(price) != Decimal(str(_PRICE)):
            raise _Fail(f"发货单 {dl} 行含税单价应为订单的 {_PRICE}，实际 {price}")


def sale_taxed(run: _Run) -> bool:
    """账套销售选项「销售报价是否含税」（SA.bSalePrice）。"""
    return option_on(run.cell(sale_price_option_sql()))


def check_taxed(run: _Run, taxed: bool, docs: list[tuple[str, int]]) -> str:
    """桥新增 / 生成的每一行 bsaleprice 应等于账套选项（含税 1、不含税 0，同 U8 界面；用例自建的来源都是新行）；返回说明。"""
    want = "1" if taxed else "0"
    checked = [] if taxed else ["账套报价不含税"]
    for kind, doc in docs:
        if run.cell(flag_col_sql(kind)) != "1":
            checked.append(f"{kind} 表没有 bsaleprice")
            continue
        rows = run.cells(flags_sql(kind, str(doc)))
        if not rows:
            raise _Fail(f"{kind} {doc} 没有明细行")
        for row in rows:
            parts = row.strip().split("|")
            if len(parts) != 2 or parts[1] != want:
                raise _Fail(f"{kind} {doc} 行 {parts[0]} 的 bsaleprice 应为 {want}，实际 {parts[-1] or '空'}")
        checked.append(f"{kind} bsaleprice={want}")
    return "、".join(checked)


def _replay(run: _Run, body: dict, dl: int, state: _State) -> None:
    payload = run.ok(_GEN, body, "同一幂等键重放")
    if payload.get("id") != dl:
        if type(payload.get("id")) is int and payload["id"] > 0:
            run.drop("dispatch", payload["id"])
            state.dls.add(payload["id"])
        raise _Fail(f"重放应返回同一张发货单 {dl}，实际 {payload.get('id')}")
    count = run.cell(disp_count_sql(state.so_lines))
    if count != str(_LINES):
        raise _Fail(f"重放后挂订单行的发货单行应仍为 {_LINES}，实际 {count}")


def _note(cols: list[_Col], refused: str) -> str:
    used = [col.name for col in cols]
    skipped = [name for name in WANTED if name not in used]
    tail = f"；账套没有 {'、'.join(skipped)}，未测" if skipped else ""
    head = f"测了 {'、'.join(used)}（两行各自的值，库、load、预演 docs 都读回，没有串行）{tail}；{refused}"
    return head + f"；部分数量 {_QTY}/{_SO_QTY} × 含税单价 {_PRICE} 的行金额按界面口径；幂等重放同一张"


def _run_body(run: _Run, state: _State) -> tuple[str, str]:
    cols = _cols(run)
    if not cols:
        raise _NotApplicable("账套的 DispatchLists_extradefine 没有可写的 cbdefine5–7")
    parts = run.pick(pick_sql(), 6)
    if parts is None:
        raise _NotApplicable("账套挑不到不管货位的仓库里可用量 >= 4 的可销售简单存货")
    wh = safe_lot(parts[5])
    so = _order(run, parts)
    state.so_lines = run.cells(so_lines_sql(str(so)))
    if len(state.so_lines) != _LINES:
        raise _Fail(f"新订单应有 {_LINES} 行，实际 {state.so_lines}")
    body = _body(so, state.so_lines, wh, run.env.today, cols)
    refused = _refuse(run, body, state)
    _dry(run, body, state, cols)
    keyed = {**body, "idempotency_key": "e2e-dlbd-" + os.urandom(8).hex()}
    dl = _made(run, keyed, state)
    dl_lines = _stored(run, dl, state, cols)
    taxed = sale_taxed(run)
    _amounts(run, dl, taxed)
    flags = check_taxed(run, taxed, [("sale_order", so), ("dispatch", dl)])
    loaded = run.ok(_LOAD, {"type": "dispatch", "id": dl}, "读取发货单")
    _check_lines("vouchers/load 的行", loaded.get("lines") or [], "idlsid", dl_lines, cols)
    _replay(run, keyed, dl, state)
    return "PASS", f"订单 {so} 生成发货单 {dl}：" + _note(cols, refused) + "；" + flags


def _reconcile(run: _Run, state: _State) -> str:
    """清理前按用例自己的订单行找发货单：没登记删除的（响应 504、没给 id、预演误落库）补登记，记下 iDLsID。"""
    if not state.so_lines:
        return ""
    try:
        rows = [row.strip().split("|") for row in run.cells(disp_by_lines_sql(state.so_lines))]
    except (RuntimeError, ValueError) as exc:
        return "按订单行对账失败：" + str(exc)[:120]
    stray = []
    for row in rows:
        if len(row) != 2 or not row[0].isdigit():
            continue
        state.dl_lines.append(row[1])
        if int(row[0]) not in state.dls:
            state.dls.add(int(row[0]))
            run.drop("dispatch", int(row[0]))
            stray.append(row[0])
    return f"对账找出未登记的发货单 {'、'.join(stray)}" if stray else ""


def _gone(run: _Run, state: _State) -> str:
    """清理之后：挂订单行或记下的 iDLsID 的发货单行、这些 iDLsID 的扩展表行都应没有。"""
    if not state.so_lines:
        return ""
    lines = sorted(set(line for line in state.dl_lines if line.isdigit()))
    try:
        got = run.cell(gone_sql(state.so_lines, lines))
    except (RuntimeError, ValueError) as exc:
        return "清理核对失败：" + str(exc)[:120]
    if got == "0|0":
        return ""
    return f"清理后仍有残留（发货单行|扩展表行）{got}，iDLsID {'、'.join(lines)}"


def dispatch_bdefine(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    run = _Run(env, spec)
    state = _State()
    try:
        result = _run_body(run, state)
    except _NotApplicable as exc:
        result = ("N/A", str(exc)[:240])
    except (_Fail, KeyError, RuntimeError, ValueError) as exc:
        result = ("FAIL", str(exc)[:240])
    problems = [_reconcile(run, state)]
    problems.append(run.cleanup())
    problems.append(_gone(run, state))
    bad = "；".join(item for item in problems if item)
    if not bad:
        return result
    head = result[1] + "；" if result[0] == "FAIL" else ""
    return "FAIL", (head + "清理：" + bad)[:240]


DISPATCH_BDEFINE_OPS = {"dispatch_bdefine": dispatch_bdefine}
_ROUTES = (_CREATE, _VERIFY, _GEN, _LOAD, _DELETE)


def dispatch_bdefine_cases() -> list[CaseSpec]:
    step = Step("custom", sql_name="dispatch_bdefine", id_from="none")
    title = (
        "发货单参照 2 行销售订单生单带表体扩展自定义项 cbdefine5/6/7（两行不同值）：缺列 400、预演不落库且 docs 按行带值，"
        "正式写入 DispatchLists_extradefine 并在库和 vouchers/load 按行读回，幂等重放同一张；按订单行对账清理并核对无残留"
    )
    return [CaseSpec("dispatch-gen-line-bdefine", title, (), (step,), True, tags=_SALE_TAGS, routes=_ROUTES)]
