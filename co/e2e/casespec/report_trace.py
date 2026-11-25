"""只读报表：订单执行（order_execution）和单据追溯（doc_trace）。不改库。

从测试账套挑一张已发货、已出库的销售订单（及其一张蓝字发货单），挑不到记 N/A。
订单执行：按 id 读这张订单的行，核对行字段和执行数；再列 5 行未执行完的采购订单行，核对都没关闭。
单据追溯：从订单向下 2 跳，发货单在第 1 跳、边从订单指向它、出库单在第 2 跳；再从发货单向上 1 跳能找回订单；
不存在的 id 回 404 not_found。步骤 kind 为 "custom"，sql_name 是操作名（TRACE_OPS 并入 ops_run）。

其他报检 → 其他检验（`report-doc-trace-qm-other`）：挑最新一张按 INSPECTAUTOID 挂着其他报检单行的其他检验单
（只有有这类单据的账套才有，挑不到记 N/A）；从检验单向上 1 跳找回报检单、从报检单向下 1 跳找回检验单，边都从报检单指向
检验单；不存在的检验单 id 回 404 not_found。
"""

from __future__ import annotations

import re

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

_ROUTE = "/v1/reports/"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_LIMIT = 5
_MISSING_ID = 2147483647
_SALE_KEYS = ("id", "code", "line_id", "qty", "shipped_qty", "out_qty", "invoiced_qty", "received_amount", "open")
_NODE_KEYS = ("type", "id", "code", "date", "state", "level")
_EDGE_KEYS = ("from_type", "from_id", "to_type", "to_id", "lines")
INSPECT_OTHER = "qm_other_inspect"
CHECK_OTHER = "qm_other_check"


def trace_pick_sql() -> str:
    # 最新一张有蓝字发货单、且该发货单已出库的销售订单：订单 ID|发货单 DLID。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), s.ID) + N'|' + CONVERT(varchar(20), d.DLID) AS v
FROM dbo.SO_SODetails AS s
JOIN dbo.DispatchLists AS d ON d.iSOsID = s.iSOsID
JOIN dbo.DispatchList AS x ON x.DLID = d.DLID
WHERE x.cVouchType = N'05' AND ISNULL(x.bReturnFlag, 0) = 0
  AND EXISTS (SELECT 1 FROM dbo.rdrecords32 AS r WHERE r.iDLsID = d.iDLsID)
ORDER BY s.ID DESC, d.DLID;
"""


def _pick(env: Env, spec: CaseSpec) -> tuple[int, int] | None:
    cell = first_cell(run_guarded(env, trace_pick_sql(), spec.name)).strip()
    parts = cell.split("|")
    if len(parts) != 2 or not all(_ID.fullmatch(part) for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def _ask(env: Env, name: str, extra: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, _ROUTE + name, fields)


def _ok(env: Env, name: str, extra: dict) -> dict:
    status, payload = _ask(env, name, extra)
    if status != 200 or payload.get("ok") is not True:
        raise ValueError(f"{name} {status} {payload.get('code')} {payload.get('message')}"[:200])
    return payload


def _rows(payload: dict, key: str) -> list[dict]:
    rows = payload.get(key)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(key + " 不是对象列表")
    return rows


def _sale_problem(rows: list[dict], order: int) -> str:
    if not rows:
        return "按 id 读订单没有行"
    for row in rows:
        lost = [key for key in _SALE_KEYS if key not in row]
        if lost:
            return "订单行缺 " + ",".join(lost)
        if row["id"] != order:
            return "混入别的订单"
    if all(row["shipped_qty"] == 0 for row in rows):
        return "挑的是已发货订单，累计发货全为 0"
    return ""


def _open_problem(payload: dict) -> str:
    rows = _rows(payload, "items")
    if payload.get("type") != "purchase_order" or payload.get("only_open") is not True or len(rows) > _LIMIT:
        return "采购订单未执行行的回显或行数不对"
    if any(row.get("open") is not True or row.get("closed") is not False for row in rows):
        return "only_open 混入已执行完或已关闭的行"
    if any("in_qty" not in row or "paid_amount" not in row for row in rows):
        return "采购订单行缺 in_qty 或 paid_amount"
    return ""


def trace_exec(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    picked = _pick(env, spec)
    if picked is None:
        return "N/A", "本账套没有已发货、已出库的销售订单"
    order, _dispatch = picked
    rows = _rows(_ok(env, "order_execution", {"type": "sale_order", "ids": [order]}), "items")
    problem = _sale_problem(rows, order)
    if not problem:
        problem = _open_problem(_ok(env, "order_execution", {"type": "purchase_order", "only_open": True, "limit": _LIMIT}))
    if problem:
        return "FAIL", problem
    return "PASS", f"订单 {order} {len(rows)} 行"


def _shape_problem(payload: dict) -> str:
    nodes, edges = _rows(payload, "nodes"), _rows(payload, "edges")
    if any(key not in node for node in nodes for key in _NODE_KEYS):
        return "节点缺字段"
    if any(key not in edge for edge in edges for key in _EDGE_KEYS):
        return "边缺字段"
    if not isinstance(payload.get("omitted"), int) or not isinstance(payload.get("truncated"), bool):
        return "omitted 或 truncated 类型不对"
    known = {(node["type"], node["id"]) for node in nodes}
    if any((e["from_type"], e["from_id"]) not in known or (e["to_type"], e["to_id"]) not in known for e in edges):
        return "边指向不在图里的节点"
    return ""


def _has(payload: dict, kind: str, doc: int, level: int) -> bool:
    return any(n["type"] == kind and n["id"] == doc and n["level"] == level for n in payload["nodes"])


def _down_problem(payload: dict, order: int, dispatch: int) -> str:
    problem = _shape_problem(payload)
    if problem:
        return problem
    if not _has(payload, "sale_order", order, 0) or payload["nodes"][0].get("level") != 0:
        return "起点不是第一项或 level 不是 0"
    if not _has(payload, "dispatch", dispatch, 1):
        return "下游第 1 跳没有挑出的发货单"
    link = ("sale_order", order, "dispatch", dispatch)
    if not any((e["from_type"], e["from_id"], e["to_type"], e["to_id"]) == link for e in payload["edges"]):
        return "缺订单 → 发货单的边"
    if not any(n["type"] == "sale_out" and n["level"] == 2 for n in payload["nodes"]):
        return "下游第 2 跳没有销售出库单"
    return ""


def trace_graph(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    picked = _pick(env, spec)
    if picked is None:
        return "N/A", "本账套没有已发货、已出库的销售订单"
    order, dispatch = picked
    down = _ok(env, "doc_trace", {"type": "sale_order", "id": order, "depth": 2, "direction": "down"})
    problem = _down_problem(down, order, dispatch)
    if not problem:
        up = _ok(env, "doc_trace", {"type": "dispatch", "id": dispatch, "depth": 1, "direction": "up"})
        problem = _shape_problem(up) or ("" if _has(up, "sale_order", order, -1) else "从发货单向上找不回订单")
    if not problem:
        status, payload = _ask(env, "doc_trace", {"type": "sale_order", "id": _MISSING_ID})
        if status != 404 or payload.get("code") != "not_found":
            problem = f"不存在的订单应 404 not_found，实际 {status} {payload.get('code')}"
    if problem:
        return "FAIL", problem
    return "PASS", f"订单 {order}：{len(down['nodes'])} 个节点、{len(down['edges'])} 条边"


def qm_other_pick_sql() -> str:
    # 最新一张其他检验单（QM15）及其报检行所属的其他报检单（QM11）：检验单 ID|报检单 ID。按 INSPECTAUTOID 关联，
    # 不用 SOURCEAUTOID（其他检验单上恒为空）。
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), c.ID) + N'|' + CONVERT(varchar(20), i.ID) AS v
FROM dbo.QMCHECKVOUCHER AS c
JOIN dbo.QMINSPECTVOUCHERS AS s ON s.AUTOID = c.INSPECTAUTOID
JOIN dbo.QMINSPECTVOUCHER AS i ON i.ID = s.ID
WHERE c.CVOUCHTYPE = N'QM15' AND i.CVOUCHTYPE = N'QM11'
ORDER BY c.ID DESC;
"""


def _pick_qm_other(env: Env, spec: CaseSpec) -> tuple[int, int] | None:
    cell = first_cell(run_guarded(env, qm_other_pick_sql(), spec.name)).strip()
    parts = cell.split("|")
    if len(parts) != 2 or not all(_ID.fullmatch(part) for part in parts):
        return None
    return int(parts[0]), int(parts[1])


def _qm_other_problem(payload: dict, start: tuple[str, int], other: tuple[str, int, int]) -> str:
    problem = _shape_problem(payload)
    if problem:
        return problem
    if not _has(payload, start[0], start[1], 0):
        return f"起点 {start[0]} {start[1]} 不在第 0 层"
    if not _has(payload, *other):
        return f"第 {other[2]} 跳没有 {other[0]} {other[1]}"
    ends = {start[:2], other[:2]}
    link = [e for e in payload["edges"] if {(e["from_type"], e["from_id"]), (e["to_type"], e["to_id"])} == ends]
    if not link or link[0]["from_type"] != INSPECT_OTHER:
        return "缺其他报检单 → 其他检验单的边"
    return ""


def trace_qm_other(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    picked = _pick_qm_other(env, spec)
    if picked is None:
        return "N/A", "本账套没有挂着其他报检单的其他检验单（只有有这类单据的账套才有）"
    check, inspect = picked
    up = _ok(env, "doc_trace", {"type": CHECK_OTHER, "id": check, "depth": 1, "direction": "up"})
    problem = _qm_other_problem(up, (CHECK_OTHER, check), (INSPECT_OTHER, inspect, -1))
    if not problem:
        down = _ok(env, "doc_trace", {"type": INSPECT_OTHER, "id": inspect, "depth": 1, "direction": "down"})
        problem = _qm_other_problem(down, (INSPECT_OTHER, inspect), (CHECK_OTHER, check, 1))
    if not problem:
        status, payload = _ask(env, "doc_trace", {"type": CHECK_OTHER, "id": _MISSING_ID})
        if status != 404 or payload.get("code") != "not_found":
            problem = f"不存在的其他检验单应 404 not_found，实际 {status} {payload.get('code')}"
    if problem:
        return "FAIL", problem
    return "PASS", f"其他报检单 {inspect} ↔ 其他检验单 {check}"


TRACE_OPS = {"trace_exec": trace_exec, "trace_graph": trace_graph, "trace_qm_other": trace_qm_other}


def _custom(op: str) -> Step:
    return Step("custom", sql_name=op, id_from="none")


def trace_cases() -> list[CaseSpec]:
    return [
        CaseSpec("report-order-execution", "报表：订单执行（销售订单按 id、采购订单未执行行）", (), (_custom("trace_exec"),), True,
                 routes=(_ROUTE + "order_execution",)),
        CaseSpec("report-doc-trace", "报表：单据追溯（订单向下两跳、发货单向上一跳、不存在 404）", (), (_custom("trace_graph"),), True,
                 routes=(_ROUTE + "doc_trace",)),
        CaseSpec("report-doc-trace-qm-other", "报表：单据追溯（其他检验单向上找回其他报检单、反向找回、不存在 404）", (),
                 (_custom("trace_qm_other"),), True, optional=True, routes=(_ROUTE + "doc_trace",)),
    ]
