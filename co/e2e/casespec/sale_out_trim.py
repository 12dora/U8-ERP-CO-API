"""销售出库按行部分生单（两步：MakeOutVouch 整单生成 → 新事务里改回 / 删行 / 删单）的待实测项（u8-notes 「销售出库按行部分出库」）。

桥里没有故障注入的口子，第 2 步失败只能靠数据触发。两条用例都自建自删、挑不到数据记 N/A；
sale-out-trim-multi 在快照内自建订单、发货单，各通道都可跑；sale-out-trim-compensate 用 SQL 改发货行货位，只在可造数、可写库的通道。

sale-out-trim-multi：同一存货的销售订单 3 行 → 生成发货单：行 A 2、行 B 1 在仓库 1，行 C 1 在仓库 2（MakeOutVouch 按仓库
  生成两张出库单）→ 审核 → 按行只出行 A 1。期望第 2 步：仓库 1 的出库单行 A 改成 1（editprop=M）、行 B editprop=D，
  仓库 2 的出库单整张删除；200 只返回保留的那一张。断言：发货单上只剩这一张出库单、只有行 A 一行数量 1，累计出库
  A / B / C = 1 / 0 / 0 → 删除出库单后都回 0、没有出库单。
sale-out-trim-compensate：货位仓库里结存 >= 2 的货位 → 销售订单 2 → 发货单 2（不带货位）审核后，用 SQL 把发货行货位写成
  该货位（API 不收发货行货位）→ 按行出 1。MakeOutVouch 按发货行带出货位、写货位记录，第 2 步要改数量的单据有货位记录时
  桥不改（StockGenSaleTrim.LoadOne 409），走补偿：删掉第 1 步生成的出库单，核对累计出库回到基线，返回 409 u8_rejected
  「已撤回生成的销售出库单：…」。断言：发货单上没有出库单、累计出库 0、货位存量和现存量与生成前相同。
  U8 没写货位记录（第 2 步照常成功）或第 1 步就被拒时记 N/A 并写明原因（补偿未覆盖）；504（补偿也失败）记 FAIL。
  补偿失败 → 504 outcome_unknown 这一支没有不注入故障就能稳定触发的办法，仍未覆盖。
清理按建的倒序（stock_plus._guard）：发货单上查到的出库单都记下删除，清理失败记 FAIL。
步骤 kind 为 "custom"，sql_name 是操作名（SALE_OUT_TRIM_OPS 并入 ops_run）。
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import (
    _SALE_ROUTES,
    _SALE_TAGS,
    _dec,
    _dispatch,
    _doc_id,
    _Fail,
    _guard,
    _Run,
    _sale_order,
)
from co.e2e.casespec.stock_plus_sql import bin_split_sql, safe_lot
from co.e2e.casespec.binned_sale_out_sql import pos_sum_sql, stock_sum_sql
from co.e2e.casespec.sale_out_trim_sql import (
    clear_pos_sql,
    disp_lines_sql,
    doc_rows_sql,
    multi_pick_sql,
    out_docs_sql,
    pos_col_sql,
    set_pos_sql,
    so_lines_sql,
)

_GEN = "/v1/vouchers/generate"
_CREATE = "/v1/vouchers/create"
_VERIFY = "/v1/vouchers/verify"
_MEMO = "CO桥回归"
_QTY = (2, 1, 1)
_UNDONE = "已撤回生成的销售出库单"


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _track(run: _Run, dl: int, known: set[int]) -> list[int]:
    """发货单上现有的出库单；没记过的都记下删除（生成失败、补偿失败时也不漏）。"""
    docs = [int(text) for text in run.cells(out_docs_sql(str(dl))) if text.strip().isdigit()]
    for doc in docs:
        if doc not in known:
            run.drop("sale_out", doc)
            known.add(doc)
    return docs


def _gen_out(run: _Run, dl: int, line: str, qty: int) -> tuple[int, dict]:
    body = {"type": "sale_out", "id": dl, "lines": [{"source_line_id": int(line), "quantity": qty}]}
    return run.send(_GEN, body)


def _out_qty(run: _Run, dl: int) -> list[Decimal]:
    return [_dec(row.split("|")[4]) for row in run.cells(disp_lines_sql(str(dl)))]


# ---- 跨仓库多张出库单的改回 ----


def _order3(run: _Run, parts: list[str]) -> tuple[int, list[str]]:
    cus, st, dep, person, inv = (safe_lot(part) for part in parts[:5])
    head = {
        "cCusCode": cus, "cSTCode": st, "cDepCode": dep, "cPersonCode": person, "cBusType": "普通销售",
        "cexch_name": "人民币", "iExchRate": 1, "iTaxRate": 13, "dDate": run.env.today, "cMemo": _MEMO,
    }
    pre = (date.fromisoformat(run.env.today) + timedelta(days=3)).isoformat()
    lines = [{"cInvCode": inv, "iQuantity": qty, "iTaxUnitPrice": 1, "iTaxRate": 13, "dPreDate": pre} for qty in _QTY]
    so = run.made(_CREATE, {"type": "sale_order", "head": head, "lines": lines}, "新建 3 行销售订单")
    run.drop("sale_order", so)
    run.ok(_VERIFY, {"type": "sale_order", "id": so, "action": "verify"}, "审核销售订单")
    run.unverify("sale_order", so)
    ids = [text.strip() for text in run.cells(so_lines_sql(str(so)))]
    if len(ids) != len(_QTY):
        raise _Fail(f"销售订单应有 {len(_QTY)} 行，实际 {len(ids)} 行")
    return so, ids


def _dispatch3(run: _Run, so: int, ids: list[str], whs: tuple[str, str, str]) -> tuple[int, list[str]]:
    lines = [{"source_line_id": int(sid), "quantity": qty, "cWhCode": wh} for sid, qty, wh in zip(ids, _QTY, whs)]
    body = {"type": "dispatch", "id": so, "head": {"cWhCode": whs[0], "dDate": run.env.today}, "lines": lines}
    dl = run.made(_GEN, body, "参照订单生成两个仓库的发货单")
    run.drop("dispatch", dl)
    run.ok(_VERIFY, {"type": "dispatch", "id": dl, "action": "verify"}, "审核发货单")
    run.unverify("dispatch", dl)
    rows = {parts[1]: parts for parts in (row.split("|") for row in run.cells(disp_lines_sql(str(dl)))) if len(parts) == 5}
    ordered = [rows.get(sid) for sid in ids]
    if len(rows) != len(ids) or any(parts is None for parts in ordered):
        raise _Fail(f"发货单行与订单行对不上：{sorted(rows)}，订单行 {ids}")
    got = tuple(parts[2].upper() for parts in ordered if parts)
    if got != tuple(wh.upper() for wh in whs):
        raise _Fail(f"发货行仓库应为 {whs}，实际 {got}")
    return dl, [parts[0] for parts in ordered if parts]


def _kept_problem(run: _Run, docs: list[int], kept: int, payload: dict, want: tuple[str, str]) -> str:
    if docs != [kept] or payload.get("ids") not in (None, [kept]):
        return f"发货单上应只剩出库单 {kept}，实际 {docs}（响应 ids {payload.get('ids')}）"
    rows = run.cells(doc_rows_sql(str(kept)))
    expect = f"{want[0]}|1.0000|{want[1]}|0"
    if [row.strip().upper() for row in rows] != [expect.upper()]:
        return f"保留的出库单应只有行 {want[0]} 数量 1（仓库 {want[1]}），实际 {rows}"
    return ""


def _multi_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(multi_pick_sql(), 7)
    if parts is None:
        return "N/A", "账套挑不到在两个不管货位的仓库里都有可用量（仓库 1 >= 3、仓库 2 >= 1）的可销售简单存货"
    wh1, wh2 = safe_lot(parts[5]), safe_lot(parts[6])
    so, ids = _order3(run, parts)
    dl, lines = _dispatch3(run, so, ids, (wh1, wh1, wh2))
    status, payload = _gen_out(run, dl, lines[0], 1)
    docs = _track(run, dl, set())
    if status != 200:
        raise _Fail(f"按行生成应 200，实际 {_brief(status, payload)}；发货单上现有出库单 {docs}")
    kept = _doc_id(payload)
    problem = _kept_problem(run, docs, kept, payload, (lines[0], wh1))
    outq = _out_qty(run, dl)
    if not problem and outq != [Decimal(1), Decimal(0), Decimal(0)]:
        problem = f"累计出库 A / B / C 应为 1 / 0 / 0，实际 {outq}"
    run.settle(len(docs))
    if problem:
        raise _Fail(problem)
    after = (_out_qty(run, dl), run.cells(out_docs_sql(str(dl))))
    if after[0] != [Decimal(0)] * 3 or after[1]:
        raise _Fail(f"删除出库单后累计出库应全为 0、没有出库单，实际 {after[0]}、{after[1]}")
    return "PASS", (
        f"发货单 {dl}（{wh1}：A 2、B 1；{wh2}：C 1）按行只出 A 1：保留出库单 {kept} 只有行 A 数量 1，"
        f"B 删行、{wh2} 的出库单不在，累计出库 1 / 0 / 0；删除后回 0"
    )


# ---- 第 2 步失败的补偿 ----


def _undone(run: _Run, dl: int, docs: list[int], base: tuple[Decimal, Decimal], keys: tuple[str, str, str]) -> str:
    wh, pos, inv = keys
    if docs:
        return f"补偿后发货单上仍有出库单 {docs}"
    outq = _out_qty(run, dl)
    if outq != [Decimal(0)]:
        return f"补偿后累计出库应为 0，实际 {outq}"
    now = (_dec(run.cell(pos_sum_sql(wh, pos, inv))), _dec(run.cell(stock_sum_sql(wh, inv))))
    if now != base:
        return f"补偿后货位存量、现存量应回到 {base[0]}、{base[1]}，实际 {now[0]}、{now[1]}"
    return ""


def _judge(run: _Run, dl: int, reply: tuple[int, dict], docs: list[int], ctx: tuple) -> tuple[str, str]:
    status, payload = reply
    base, keys = ctx
    message = str(payload.get("message") or "")
    if status == 504:
        raise _Fail(f"补偿也失败：{_brief(status, payload)}；发货单上现有出库单 {docs}（清理会再删）")
    if status == 409 and payload.get("code") == "u8_rejected" and message.startswith(_UNDONE):
        problem = _undone(run, dl, docs, base, keys)
        if problem:
            raise _Fail(problem)
        return "PASS", f"409「{message[:120]}」：出库单已删，累计出库回 0，货位存量 {base[0]}、现存量 {base[1]} 与生成前相同"
    if status == 200:
        binned = sum(int(row.split("|")[3]) for doc in docs for row in run.cells(doc_rows_sql(str(doc))))
        return "N/A", f"第 2 步没有失败（200，出库单 {docs}，货位记录 {binned} 行）：U8 没按发货行货位写货位记录，补偿未覆盖"
    if docs:
        raise _Fail(f"{_brief(status, payload)}，却留下出库单 {docs}（不是补偿后的 409）")
    return "N/A", f"第 1 步就被拒（{_brief(status, payload)}），补偿未覆盖"


def _compensate_body(run: _Run) -> tuple[str, str]:
    parts = run.pick(bin_split_sql(), 9)
    if parts is None:
        return "N/A", "账套挑不到货位仓库里有结存的可销售存货"
    if _dec(parts[7]) < 2:
        return "N/A", f"货位 {parts[6]} 结存 {parts[7]}，不够整行 2 放在一个货位"
    if run.cell(pos_col_sql()) != "1":
        return "N/A", "发货单表体没有货位列 cPosition"
    inv, wh, pos = safe_lot(parts[4]), safe_lot(parts[5]), safe_lot(parts[6])
    base = (_dec(run.cell(pos_sum_sql(wh, pos, inv))), _dec(run.cell(stock_sum_sql(wh, inv))))
    so = _sale_order(run, parts, 2)
    dl, line = _dispatch(run, so, wh, 2, "")
    if run.cell(set_pos_sql(str(dl), line, pos)) != "1":
        raise _Fail(f"把发货行 {line} 的货位写成 {pos} 应改 1 行")
    reply = _gen_out(run, dl, line, 1)
    docs = _track(run, dl, set())
    # 删发货单之前把货位复原（出库单的删除不看发货行货位）。
    if run.cell(clear_pos_sql(str(dl), line)) != "1":
        raise _Fail(f"复原发货行 {line} 的货位应改 1 行")
    status, detail = _judge(run, dl, reply, docs, (base, (wh, pos, inv)))
    return status, f"发货单 {dl}（{wh} / {pos} / {inv}，2 → 出 1）：{detail}"


# ---- 接线 ----


def sale_out_trim_multi(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _multi_body)


def sale_out_trim_compensate(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _compensate_body)


SALE_OUT_TRIM_OPS = {
    "sale_out_trim_multi": sale_out_trim_multi,
    "sale_out_trim_compensate": sale_out_trim_compensate,
}


def _case(name: str, title: str, op: str) -> CaseSpec:
    step = Step("custom", sql_name=op, id_from="none")
    return CaseSpec(name, title, (), (step,), True, tags=_SALE_TAGS, routes=_SALE_ROUTES)


def sale_out_trim_cases() -> list[CaseSpec]:
    # 同 sale-out-bin-split：新建订单、发货单、出库单，动现存量（stock）。补偿用例用 SQL 改自建发货行的货位，只放可写库的通道。
    return [
        _case("sale-out-trim-multi",
              "销售出库按行部分生单跨两个仓库：一张改数量并删行、另一张整张删除，只返回保留的出库单，累计出库对上",
              "sale_out_trim_multi"),
        _case("sale-out-trim-compensate",
              "销售出库按行部分生单第 2 步失败（生成单有货位记录）：撤回生成的出库单，409「已撤回…」，累计出库和货位存量回到生成前",
              "sale_out_trim_compensate"),
    ]
