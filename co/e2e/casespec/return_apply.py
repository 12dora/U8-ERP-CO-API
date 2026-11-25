"""退货申请单（sale_return_apply，销售管理卡片 SA31）的只读核对与不开放路由的拒绝；写入循环见 return_apply_cycle。

list-sale-return-apply：取一页列表 → 行有 line_count、quantity 不为正 → keys_only 主键与完整列表相同 → vouchers/search
  按单号找回首行。没有退货申请单记 N/A（多数账套没有退货申请单）。
load-sale-return-apply：读取列表首行：单号一致、表体行数等于 line_count、每行数量不为正、state.verified / closed 与列表一致；
  再从它做单据追溯（上下各 1 跳）：起点在图里，表体挂着原发货单行（iDLsID）时有发货单 → 退货申请单的边。没有单据记 N/A。
sale-return-apply-write-refused：关闭一张不存在的退货申请单、以退货申请单为目标生单，桥在登录前回 400（类型不支持），
  不碰账套（新增、修改、删除、审核另见 return_apply_cycle）。

这三条只读，不改账套。步骤 kind 为 "custom"，sql_name 是操作名（RETURN_APPLY_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_ops import send
from co.e2e.casespec.return_apply_cycle import CYCLE_OPS, return_apply_cycle_cases

KIND = "sale_return_apply"
_LIST = "/v1/vouchers/list"
_LOAD = "/v1/vouchers/load"
_SEARCH = "/v1/vouchers/search"
_CLOSE = "/v1/vouchers/close"
_GEN = "/v1/vouchers/generate"
_TRACE = "/v1/reports/doc_trace"
_PAGE = 20
_MISSING_ID = 2147483647


def _ok(env: Env, route: str, body: dict) -> dict:
    status, payload = send(env, route, body)
    if status != 200:
        raise ValueError(f"{route} {status} {payload.get('code')} {payload.get('message')}"[:200])
    return payload


def _items(payload: dict, key: str = "items") -> list[dict]:
    rows = payload.get(key)
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError(f"响应缺 {key}")
    return rows


def _num(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _cell(row: dict, name: str) -> object:
    """表头 / 表体按 U8 列名取值，不分大小写。"""
    lower = name.lower()
    return next((value for key, value in row.items() if key.lower() == lower), None)


def _row_problem(row: dict) -> str:
    lines, qty = _num(row.get("line_count")), _num(row.get("quantity"))
    if lines is None or lines < 0:
        return f"退货申请单 {row.get('code')} 的 line_count 不是非负整数"
    if lines > 0 and (qty is None or qty > 0):
        return f"退货申请单 {row.get('code')} 的 quantity 应不为正，实际 {row.get('quantity')}"
    return ""


def _keys_problem(env: Env, rows: list[dict]) -> str:
    keys = _items(_ok(env, _LIST, {"type": KIND, "limit": _PAGE, "keys_only": True}))
    if [row.get("id") for row in keys] != [row.get("id") for row in rows]:
        return "退货申请单 keys_only 的主键与完整列表不同"
    return ""


def _search_problem(env: Env, row: dict) -> str:
    code = str(row.get("code") or "").strip()
    found = _items(_ok(env, _SEARCH, {"type": KIND, "code_like": code, "limit": _PAGE}))
    if row.get("id") not in [item.get("id") for item in found]:
        return f"退货申请单按单号 {code} 搜索没有找回 {row.get('id')}"
    return ""


def list_read(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    rows = _items(_ok(env, _LIST, {"type": KIND, "limit": _PAGE}))
    if not rows:
        return "N/A", "账套没有退货申请单"
    problem = next((text for text in map(_row_problem, rows) if text), "")
    problem = problem or _keys_problem(env, rows) or _search_problem(env, rows[0])
    if problem:
        return "FAIL", problem
    return "PASS", f"首页 {len(rows)} 张：列表、keys_only、按单号搜索一致"


def _lines_problem(row: dict, lines: list[dict]) -> str:
    if _num(row.get("line_count")) != len(lines):
        return f"退货申请单 {row.get('code')} 读取 {len(lines)} 行，列表 line_count {row.get('line_count')}"
    positive = [line for line in lines if (_num(_cell(line, "iQuantity")) or 0) > 0]
    if positive:
        return f"退货申请单 {row.get('code')} 有数量为正的行 {_cell(positive[0], 'AutoID')}"
    return ""


def _state_problem(row: dict, state: dict) -> str:
    for key in ("verified", "closed"):
        if not isinstance(state.get(key), bool) or state.get(key) != bool(row.get(key)):
            return f"退货申请单 {row.get('code')} 的 state.{key} 与列表不一致"
    return ""


def _trace_problem(env: Env, doc: int, lines: list[dict]) -> str:
    payload = _ok(env, _TRACE, {"type": KIND, "id": doc, "depth": 1})
    nodes, edges = _items(payload, "nodes"), _items(payload, "edges")
    if not any(node.get("type") == KIND and node.get("id") == doc for node in nodes):
        return f"单据追溯的节点里没有起点退货申请单 {doc}"
    linked = any((_num(_cell(line, "iDLsID")) or 0) > 0 for line in lines)
    edge = any(e.get("from_type") == "dispatch" and e.get("to_type") == KIND and e.get("to_id") == doc for e in edges)
    if linked and not edge and not payload.get("omitted"):
        return f"退货申请单 {doc} 挂着原发货单行，单据追溯却没有发货单 → 退货申请单的边"
    return ""


def load_read(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    rows = _items(_ok(env, _LIST, {"type": KIND, "limit": _PAGE}))
    if not rows:
        return "N/A", "账套没有退货申请单"
    row = rows[0]
    payload = _ok(env, _LOAD, {"type": KIND, "id": row["id"]})
    if payload.get("code") != row.get("code"):
        return "FAIL", f"退货申请单 {row['id']} 读取的单号 {payload.get('code')} 与列表 {row.get('code')} 不同"
    lines = _items(payload, "lines")
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    problem = _lines_problem(row, lines) or _state_problem(row, state) or _trace_problem(env, row["id"], lines)
    if problem:
        return "FAIL", problem
    return "PASS", f"退货申请单 {row.get('code')}：{len(lines)} 行，读取、状态、单据追溯一致"


def write_refused(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    calls = ((_CLOSE, {"action": "close"}), (_GEN, {"lines": [{"source_line_id": 1, "quantity": 1}]}))
    for route, extra in calls:
        body = {"type": KIND, "id": _MISSING_ID}
        body.update(extra)
        status, payload = send(env, route, body)
        if status != 400 or payload.get("code") != "bad_request":
            return "FAIL", f"{route} 应 400 bad_request，实际 {status} {payload.get('code')} {payload.get('message')}"[:200]
    return "PASS", "关闭、以退货申请单为目标生单都在登录前 400"


RETURN_APPLY_OPS = {
    "return_apply_list": list_read,
    "return_apply_load": load_read,
    "return_apply_write_refused": write_refused,
}
RETURN_APPLY_OPS.update(CYCLE_OPS)  # 写入循环


def return_apply_cases() -> list[CaseSpec]:
    # 只读；list:<类型> 让新建、删除退货申请单的用例（sale-return-apply-cycle）不同时跑。
    def case(name: str, title: str, op: str, routes: tuple[str, ...]) -> CaseSpec:
        step = Step("custom", sql_name=op, id_from="none")
        return CaseSpec(name, title, (), (step,), True, optional=True, tags=frozenset(("list:" + KIND,)), routes=routes)

    return [
        case("list-sale-return-apply", "退货申请单列表、keys_only 与按单号搜索", "return_apply_list", (_LIST, _SEARCH)),
        case("load-sale-return-apply", "退货申请单读取（表体、状态）与单据追溯", "return_apply_load", (_LIST, _LOAD, _TRACE)),
        case("sale-return-apply-write-refused", "退货申请单关闭、作为生单目标在登录前 400", "return_apply_write_refused",
             (_CLOSE, _GEN)),
    ] + return_apply_cycle_cases()
