"""来源库存的销售出库单（未启用销售管理的账套手工录入，cSource=库存）修改：StockEditSrc 转 StockEdit.Update。

sale-out-nosrc-update：账套启用了销售管理（SA.dSaleStartDate 非空）或库存选项 ST.bSAcreat 打开时桥不收无来源销售出库单，
  记 N/A（拒绝由 sale-out-nosrc 断言）。否则挑一个不管货位的仓库里可用量 >= 3 的普通存货，无来源新建 1 行 2 个 →
  一次修改：表头备注、该行数量 2 改 1 加行备注、加 1 行 1 个 → 回读：来源库存、普通销售、VT 87、两行数量 1 / 1、
  都不挂发货单行、响应 lines 2 → 再删掉新加的行（响应 lines 1，只剩原行）→ 换仓库 409「不能修改销售出库单的仓库」
  （账套只有一个可用仓库时略过这一项）→ 读取 200 → 删除 → 读取 404。
  新建的载荷是本模块的小助手（_create_body）：表头仓库、客户、部门必填，另带挑出的收发类别、销售类型（与 sale-out-nosrc 同口径）。
清理按建的倒序（stock_plus._guard）。步骤 kind 为 "custom"，sql_name 是操作名（SALE_OUT_NOSRC_UPDATE_OPS 并入 ops_run）。
"""

from __future__ import annotations

from decimal import Decimal

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.stock_plus import _dec, _Fail, _guard, _NotApplicable, _Run
from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.sale_out_nosrc_update_sql import gate_sql, head_sql, lines_sql, pick_sql

_CREATE = "/v1/vouchers/create"
_UPDATE = "/v1/vouchers/update"
_LOAD = "/v1/vouchers/load"
_DELETE = "/v1/vouchers/delete"
_MEMO = "U8CO-E2E-OUT-NOSRC"
_EDITED = "U8CO-E2E-OUT-NOSRC-改"
_WH_MSG = "不能修改销售出库单的仓库"
NAME = "sale-out-nosrc-update"


def _brief(status: int, payload: dict) -> str:
    return f"{status} {payload.get('code')} {payload.get('message')}"[:200]


def _on(text: str) -> bool:
    return text.strip().upper() in ("1", "TRUE")


def _gate(run: _Run) -> None:
    parts = (run.cell(gate_sql()) + "|").split("|")
    if parts[0].strip():
        raise _NotApplicable(f"账套已启用销售管理（{parts[0].strip()}），桥不收无来源销售出库单")
    if _on(parts[1]):
        raise _NotApplicable("库存选项设置了销售出库单由销售系统生成（ST.bSAcreat）")


def _create_body(run: _Run, parts: list[str]) -> dict:
    wh, inv, cus, dep = (safe_lot(part) for part in parts[:4])
    head = {"cwhcode": wh, "ccuscode": cus, "cdepcode": dep, "ddate": run.env.today, "cmemo": _MEMO}
    if parts[4]:
        head["crdcode"] = safe_lot(parts[4])
    if parts[5]:
        head["cstcode"] = safe_lot(parts[5])
    return {"type": "sale_out", "head": head, "lines": [{"cinvcode": inv, "iquantity": 2}]}


def _rows(run: _Run, doc: int) -> list[list[str]]:
    return [row.split("|") for row in run.cells(lines_sql(str(doc)))]


def _check_head(run: _Run, doc: int, memo: str) -> None:
    head = run.cell(head_sql(str(doc))).split("|")
    if len(head) != 5:
        raise _Fail(f"回读表头 {head}")
    want = ("库存", "普通销售", "87", memo)
    if tuple(head[:4]) != want:
        raise _Fail(f"表头 来源|业务类型|VT|备注 应为 {'|'.join(want)}，实际 {'|'.join(head[:4])}")


def _first_line(run: _Run, doc: int) -> int:
    rows = _rows(run, doc)
    if len(rows) != 1 or len(rows[0]) != 4 or _dec(rows[0][1]) != Decimal(2):
        raise _Fail(f"新建后应有 1 行数量 2，实际 {rows}")
    return int(rows[0][0])


def _edit(run: _Run, doc: int, line: int, inv: str) -> int:
    lines = [
        {"op": "update", "line_id": line, "iQuantity": 1, "cbMemo": "改"},
        {"op": "add", "cInvCode": inv, "iQuantity": 1, "cbMemo": "加"},
    ]
    payload = run.ok(_UPDATE, {"type": "sale_out", "id": doc, "head": {"cMemo": _EDITED}, "lines": lines}, "改数量并加行")
    if payload.get("lines") != 2:
        raise _Fail(f"改数量并加行后响应 lines 应为 2，实际 {payload.get('lines')}")
    _check_head(run, doc, _EDITED)
    rows = _rows(run, doc)
    got = [(int(r[0]) == line, _dec(r[1]), r[2], r[3]) for r in rows if len(r) == 4]
    want = [(True, Decimal(1), "改", "0"), (False, Decimal(1), "加", "0")]
    if got != want:
        raise _Fail(f"修改后两行应为 原行 1「改」/ 新行 1「加」且都不挂发货单行，实际 {rows}")
    return int(rows[1][0])


def _drop_added(run: _Run, doc: int, line: int, added: int) -> None:
    payload = run.ok(_UPDATE, {"type": "sale_out", "id": doc, "lines": [{"op": "delete", "line_id": added}]}, "删除新加的行")
    if payload.get("lines") != 1:
        raise _Fail(f"删行后响应 lines 应为 1，实际 {payload.get('lines')}")
    rows = _rows(run, doc)
    if [r[0] for r in rows] != [str(line)] or _dec(rows[0][1]) != Decimal(1):
        raise _Fail(f"删行后应只剩原行 {line} 数量 1，实际 {rows}")


def _refuse_wh(run: _Run, doc: int, other: str) -> str:
    if not other:
        return "（账套没有第二个不管货位的仓库，略过换仓库）"
    status, payload = run.send(_UPDATE, {"type": "sale_out", "id": doc, "head": {"cWhCode": safe_lot(other)}})
    if status != 409 or payload.get("code") != "state_mismatch" or _WH_MSG not in str(payload.get("message") or ""):
        raise _Fail(f"换仓库应 409「{_WH_MSG}」，实际 {_brief(status, payload)}")
    return f"换到 {other} 409"


def _body(run: _Run) -> tuple[str, str]:
    _gate(run)
    parts = run.pick(pick_sql(), 7)
    if parts is None:
        raise _NotApplicable("账套挑不到不管货位的仓库里可用量 >= 3 的普通可销售存货，或没有客户、末级部门")
    doc = run.made(_CREATE, _create_body(run, parts), "无来源新建销售出库单")
    run.drop("sale_out", doc)
    _check_head(run, doc, _MEMO)
    line = _first_line(run, doc)
    added = _edit(run, doc, line, safe_lot(parts[1]))
    _drop_added(run, doc, line, added)
    wh_note = _refuse_wh(run, doc, parts[6])
    status, payload = run.send(_LOAD, {"type": "sale_out", "id": doc})
    if status != 200:
        raise _Fail(f"修改后读取 {_brief(status, payload)}")
    run.settle(1)
    status, payload = run.send(_LOAD, {"type": "sale_out", "id": doc})
    if status != 404:
        raise _Fail(f"删除后读取应 404，实际 {_brief(status, payload)}")
    return "PASS", f"出库单 {doc}（{parts[0]} / {parts[1]}）：改 2→1、加行再删行，不挂发货单；{wh_note}；删除后 404"


def sale_out_nosrc_update(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    return _guard(env, spec, _body)


SALE_OUT_NOSRC_UPDATE_OPS = {"sale_out_nosrc_update": sale_out_nosrc_update}


def sale_out_nosrc_update_cases() -> list[CaseSpec]:
    # 新建、修改、删除销售出库单，动现存量（stock）。选项由用例按 SQL 判断，挑不到或启用了销售管理记 N/A，
    # 未启用销售管理的账套上才真正执行，所以分片配置可让它在所有通道上跑。
    step = Step("custom", sql_name="sale_out_nosrc_update", id_from="none")
    tags = frozenset(("new:sale_out", "w:sale_out", "stock"))
    title = "来源库存的销售出库单修改：改数量和备注、加行、删行，不挂发货单；换仓库 409；删除后 404"
    return [CaseSpec(NAME, title, (), (step,), True, optional=True, tags=tags, routes=(_CREATE, _UPDATE, _LOAD, _DELETE))]
