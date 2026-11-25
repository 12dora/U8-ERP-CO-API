"""其他报检单（QM11，qm_other_inspect）、其他检验单（QM15，qm_other_check）的只读核对。

qm-other-read：两种类型各取一页列表 → 行不带 wf / wf_state / current_auditor、check_type 是 OTH，其他报检单的来源三列
  为空、其他检验单有 inspect_id → keys_only 只有 id / code / ufts 且主键与完整列表相同 → 翻页（limit 1，next 接上）→
  读取首行：其他报检单 source 为 null、有表体、不带 wf；其他检验单不带 wf，表头 INSPECTID 能读出一张其他报检单 →
  vouchers/search 按单号找回首行。

单张读取的基本断言另在 load-qm_other_inspect / load-qm_other_check（doc_basic，按 sql_load 挑最新一张）。
两种都挑不到（只有有这两类单据的账套才有）记 N/A；只有一种时只核这一种。只读，不改账套。
步骤 kind 为 "custom"，sql_name 是操作名（QM_OTHER_OPS 并入 ops_run）。
"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_ops import send

INSPECT = "qm_other_inspect"
CHECK = "qm_other_check"
_LIST = "/v1/vouchers/list"
_LOAD = "/v1/vouchers/load"
_SEARCH = "/v1/vouchers/search"
_PAGE = 20
_WF_KEYS = ("wf", "wf_state", "current_auditor")
_KEYS_ONLY = {"id", "code", "ufts"}
_SOURCE_KEYS = ("source", "source_code", "source_id")


def _ok(env: Env, route: str, body: dict) -> dict:
    status, payload = send(env, route, body)
    if status != 200:
        raise ValueError(f"{route} {body.get('type')} {status} {payload.get('code')} {payload.get('message')}"[:200])
    return payload


def _items(payload: dict) -> list[dict]:
    rows = payload.get("items")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("列表响应缺 items")
    return rows


def _blank(value: object) -> bool:
    return value is None or str(value).strip() == ""


def _row_problem(kind: str, row: dict) -> str:
    extra = [key for key in _WF_KEYS if key in row]
    if extra:
        return f"{kind} 列表行带了 {','.join(extra)}"
    if str(row.get("check_type") or "").strip() != "OTH":
        return f"{kind} 列表行 check_type 不是 OTH：{row.get('check_type')}"
    if kind == INSPECT and not all(_blank(row.get(key)) for key in _SOURCE_KEYS):
        return f"其他报检单 {row.get('code')} 的来源列不为空"
    if kind == CHECK and _blank(row.get("inspect_id")):
        return f"其他检验单 {row.get('code')} 没有 inspect_id"
    return ""


def _keys_problem(env: Env, kind: str, rows: list[dict]) -> str:
    keys = _items(_ok(env, _LIST, {"type": kind, "limit": _PAGE, "keys_only": True}))
    if any(set(row) != _KEYS_ONLY for row in keys):
        return f"{kind} keys_only 行的键不对"
    if [row.get("id") for row in keys] != [row.get("id") for row in rows]:
        return f"{kind} keys_only 的主键与完整列表不同"
    return ""


def _page_problem(env: Env, kind: str, rows: list[dict]) -> str:
    if len(rows) < 2:
        return ""
    first = _ok(env, _LIST, {"type": kind, "limit": 1})
    after = first.get("next")
    if after != rows[0].get("id"):
        return f"{kind} 第一页 next 应为 {rows[0].get('id')}，实际 {after}"
    second = _items(_ok(env, _LIST, {"type": kind, "limit": 1, "after": after}))
    if not second or second[0].get("id") != rows[1].get("id"):
        return f"{kind} 第二页没有接在第一页之后"
    return ""


def _head_cell(head: object, name: str) -> str:
    if not isinstance(head, dict):
        return ""
    for key, value in head.items():
        if key.upper() == name:
            return "" if value is None else str(value).strip()
    return ""


def _load_problem(env: Env, kind: str, doc: int) -> str:
    payload = _ok(env, _LOAD, {"type": kind, "id": doc})
    if "wf" in payload:
        return f"{kind} {doc} 读取结果带了 wf"
    if kind == INSPECT:
        lines = payload.get("lines")
        if payload.get("source") is not None or not isinstance(lines, list) or not lines:
            return f"其他报检单 {doc} 应 source 为 null 且有表体"
        return ""
    inspect = _head_cell(payload.get("head"), "INSPECTID")
    if not inspect.isdigit() or int(inspect) <= 0:
        return f"其他检验单 {doc} 表头没有 INSPECTID"
    _ok(env, _LOAD, {"type": INSPECT, "id": int(inspect)})
    return ""


def _search_problem(env: Env, kind: str, row: dict) -> str:
    code = str(row.get("code") or "").strip()
    if not code:
        return f"{kind} 列表首行没有单号"
    found = _items(_ok(env, _SEARCH, {"type": kind, "code_like": code, "limit": _PAGE}))
    if row.get("id") not in [item.get("id") for item in found]:
        return f"{kind} 按单号 {code} 搜索没有找回 {row.get('id')}"
    return ""


def _kind_problem(env: Env, kind: str, rows: list[dict]) -> str:
    for row in rows:
        problem = _row_problem(kind, row)
        if problem:
            return problem
    doc = rows[0].get("id")
    if not isinstance(doc, int) or isinstance(doc, bool):
        return f"{kind} 列表首行 id 不是整数"
    return (
        _keys_problem(env, kind, rows)
        or _page_problem(env, kind, rows)
        or _load_problem(env, kind, doc)
        or _search_problem(env, kind, rows[0])
    )


def qm_other_read(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    seen = []
    for kind in (INSPECT, CHECK):
        rows = _items(_ok(env, _LIST, {"type": kind, "limit": _PAGE}))
        if not rows:
            continue
        problem = _kind_problem(env, kind, rows)
        if problem:
            return "FAIL", problem
        seen.append(f"{kind} 首页 {len(rows)} 行")
    if not seen:
        return "N/A", "本账套没有其他报检单、其他检验单（只有有这两类单据的账套才有）"
    return "PASS", "；".join(seen) + "：列表、keys_only、翻页、读取、按单号搜索一致"


QM_OTHER_OPS = {"qm_other_read": qm_other_read}


def qm_other_cases() -> list[CaseSpec]:
    # 只读；list:<类型> 让之后新建、删除这两类单据的用例不同时跑。
    step = Step("custom", sql_name="qm_other_read", id_from="none")
    title = "其他报检单、其他检验单的列表（不带 wf、check_type=OTH）、keys_only、翻页、读取和按单号搜索"
    tags = frozenset(("list:" + INSPECT, "list:" + CHECK))
    return [CaseSpec("qm-other-read", title, (), (step,), True, optional=True, tags=tags, routes=(_LIST, _LOAD, _SEARCH))]
