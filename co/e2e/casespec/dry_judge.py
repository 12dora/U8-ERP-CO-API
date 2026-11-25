"""写预演用例的纯判断：预演响应（dry_run / mode / docs / warnings）、回读前后比对、名称解析结果、幂等查询结果。

不连服务器、不引用 steps，单元测试直接调。预演步骤的写法见 dry_ops（Step.kind 为 "custom"、sql_name 为 "dry_op"）：
fields 里以 @ 打头的是步骤参数，其余是请求正文（值按 steps.resolve_token 解析，$id 是当前单据）：
  @b  正文构造器（steps._make 认的名字），构造器的结果覆盖同名字段
  @m  期望的 mode：rollback / validate / plan
  @x  期望的 docs（只在 rollback 模式查）：new（新单据，type 等于正文 type）/ exists / deleted（请求的单据）/ any（不查）
  @w  预演前后各读一次、要求完全相同的单据：逗号分隔的 <类型>:<来源>，来源是 $id 或 bag:<键>；
      类型写成 wf.<类型> 时读 workflow/state
  @d  detail 里必须有的键
"""

from __future__ import annotations

import json
from dataclasses import dataclass

MODES = ("rollback", "validate", "plan")
EXPECTS = ("new", "exists", "deleted", "any")
_ID_MAX = 2147483647


@dataclass(frozen=True)
class Want:
    mode: str
    expect: str
    kind: str
    target: int
    creating: bool
    detail: str = ""


def split_fields(fields: tuple[tuple[str, str], ...]) -> tuple[dict[str, str], tuple[tuple[str, str], ...]]:
    """（步骤参数, 正文字段）。"""
    args = {key: value for key, value in fields if key.startswith("@")}
    body = tuple((key, value) for key, value in fields if not key.startswith("@"))
    return args, body


def parse_watch(text: str) -> list[tuple[str, str]]:
    """"sale_order:$id,dispatch:bag:k" → [("sale_order", "$id"), ("dispatch", "bag:k")]。"""
    found = []
    for item in (text or "").split(","):
        kind, sep, src = item.strip().partition(":")
        if not item.strip():
            continue
        if not sep or not kind or not (src == "$id" or src.startswith("bag:")):
            raise ValueError("@w 写法不对：" + item)
        found.append((kind, src))
    return found


def is_creating(route: str) -> bool:
    return route.endswith("/create") or route.endswith("/generate")


def expected_warnings(want: Want) -> tuple[str, ...]:
    if want.mode == "validate":
        return ("validate_only",)
    if want.mode != "rollback":
        return ()
    if want.creating:
        return ("number_may_skip", "locks_held")
    return ("locks_held",)


def _as_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= _ID_MAX:
        return 0
    return value


def docs_of(payload: dict) -> list[dict]:
    rows = payload.get("docs")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def new_ids(payload: dict, kind: str, target: int = 0) -> list[int]:
    """预演里出现的 kind 类型、state=exists 的单据 id（不含请求自己的单据）。"""
    found = []
    for doc in docs_of(payload):
        doc_id = _as_id(doc.get("id"))
        if doc.get("type") == kind and doc.get("state") == "exists" and doc_id and doc_id != target:
            found.append(doc_id)
    return found


def _new_problem(doc: dict) -> str:
    head, lines = doc.get("head"), doc.get("lines")
    if not isinstance(head, dict) or not head:
        return "新单据没有 head"
    if not isinstance(lines, list) or not lines or not all(isinstance(row, dict) for row in lines):
        return "新单据没有 lines"
    total = doc.get("lines_total")
    if isinstance(total, bool) or not isinstance(total, int) or total < len(lines):
        return "lines_total 不对"
    return ""


def _docs_problem(payload: dict, want: Want) -> str:
    docs = [doc for doc in docs_of(payload) if doc.get("type") == want.kind]
    if want.expect == "new":
        fresh = [doc for doc in docs if doc.get("state") == "exists" and _as_id(doc.get("id")) not in (0, want.target)]
        if not fresh:
            return f"docs 里没有新的 {want.kind}"
        return _new_problem(fresh[0])
    mine = [doc for doc in docs if _as_id(doc.get("id")) == want.target]
    if not mine:
        return f"docs 里没有 {want.kind} {want.target}"
    state = mine[0].get("state")
    if want.expect == "deleted" and (state != "deleted" or "head" in mine[0]):
        return f"{want.kind} {want.target} 的 state={state}，期望 deleted 且不带 head"
    if want.expect == "exists" and state != "exists":
        return f"{want.kind} {want.target} 的 state={state}，期望 exists"
    return ""


def _envelope_problem(payload: dict, want: Want) -> str:
    if payload.get("ok") is not True or payload.get("dry_run") is not True:
        return f"不是预演响应：ok={payload.get('ok')} dry_run={payload.get('dry_run')}"
    if payload.get("mode") != want.mode:
        return f"mode={payload.get('mode')}，期望 {want.mode}"
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        return "没有 warnings"
    missing = [code for code in expected_warnings(want) if code not in warnings]
    if missing:
        return "warnings 缺 " + ",".join(missing)
    if want.detail:
        detail = payload.get("detail")
        if not isinstance(detail, dict) or detail.get(want.detail) in (None, "", [], {}):
            return "detail 缺 " + want.detail
    return ""


def preview_problem(payload: dict, want: Want) -> str:
    """预演响应的问题；空串表示符合预期。"""
    problem = _envelope_problem(payload, want)
    if problem:
        return problem
    if want.mode == "validate" and want.creating and new_ids(payload, want.kind, want.target):
        return "validate 模式不该有新单据"
    # validate 模式的 docs 只是「可能带」当前已提交的单据（契约 §2），不查。
    if want.expect == "any" or want.mode != "rollback":
        return ""
    return _docs_problem(payload, want)


def canon(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def first_diff(before: object, after: object, path: str = "") -> str:
    """两次回读第一处不同的路径；相同返回空串。"""
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            if key not in before or key not in after:
                return (path + "." + key).lstrip(".")
            found = first_diff(before[key], after[key], path + "." + key)
            if found:
                return found
        return ""
    if isinstance(before, list) and isinstance(after, list):
        if len(before) != len(after):
            return (path + "#len").lstrip(".")
        for index, (left, right) in enumerate(zip(before, after)):
            found = first_diff(left, right, f"{path}.{index}")
            if found:
                return found
        return ""
    return "" if canon(before) == canon(after) else (path.lstrip(".") or "(整体)")


def cancel_nos(value: object) -> list[str]:
    # detail 里任意位置的 cancel_no（核销预演的 detail.writeoff 是核销响应去掉 ok 之后的样子）。
    if isinstance(value, dict):
        found = [value["cancel_no"]] if isinstance(value.get("cancel_no"), str) else []
        return found + [no for item in value.values() for no in cancel_nos(item)]
    if isinstance(value, list):
        return [no for item in value for no in cancel_nos(item)]
    return []


# ---- 名称解析 ----

RESOLVE_STATUS = ("exact", "ambiguous", "partial", "none")


def result_codes(result: dict) -> list[str]:
    rows = result.get("candidates")
    if not isinstance(rows, list):
        return []
    return [str(row.get("code") or "") for row in rows if isinstance(row, dict)]


def resolve_problem(result: object, statuses: tuple[str, ...], code: str = "") -> str:
    """一项解析结果：status 在 statuses 里；给了 code 时它要在候选里，exact / partial 时 match.code 等于它。"""
    if not isinstance(result, dict):
        return "结果不是对象"
    status = result.get("status")
    if status not in statuses:
        return f"status={status}，期望 {'/'.join(statuses)}"
    codes = result_codes(result)
    if status == "none":
        return "" if codes == [] and result.get("more") is False else "none 时候选应为空、more=false"
    match = result.get("match")
    if status in ("exact", "partial") and (not isinstance(match, dict) or not match.get("code")):
        return f"{status} 缺 match"
    if code and status in ("exact", "partial") and match.get("code") != code:
        return f"match.code={match.get('code')}，期望 {code}"
    if code and code not in codes and result.get("more") is not True:
        return f"候选里没有 {code}"
    return ""


def middle_piece(name: str) -> str:
    """取名称中间的一段（去掉首尾各一个字，至少 2 个字）；名称太短返回空串。"""
    text = name.strip()
    if len(text) < 4:
        return ""
    return text[1:-1]


def common_pieces(names: list[str], taken: set[str], limit: int = 3) -> list[str]:
    """至少两个不同名称都含、又不等于任何已知编码或名称的片段（2 到 6 个字，长的在前），至多 limit 个。"""
    clean = sorted({name.strip() for name in names if len(name.strip()) >= 2})
    found: list[str] = []
    for size in range(6, 1, -1):
        counts: dict[str, int] = {}
        for name in clean:
            for piece in {name[i : i + size] for i in range(len(name) - size + 1)}:
                counts[piece] = counts.get(piece, 0) + 1
        found += sorted(piece for piece, count in counts.items() if count >= 2 and piece.strip() == piece and piece not in taken)
        if len(found) >= limit:
            break
    return found[:limit]


def common_piece(names: list[str], taken: set[str]) -> str:
    """common_pieces 的第一个；找不到返回空串。"""
    pieces = common_pieces(names, taken, 1)
    return pieces[0] if pieces else ""


# ---- 幂等结果查询 ----


def idem_problem(payload: dict, created: dict) -> str:
    """idempotency/get 找到的记录：state ok、status 200、response 与第一次新建的响应键值相同（period / sign / no）。"""
    if payload.get("ok") is not True or payload.get("found") is not True:
        return f"found={payload.get('found')}"
    if payload.get("state") != "ok" or payload.get("status") != 200:
        return f"state={payload.get('state')} status={payload.get('status')}"
    stored = payload.get("response")
    if not isinstance(stored, dict):
        return "没有 response"
    for key in ("period", "sign", "no"):
        if stored.get(key) != created.get(key):
            return f"response.{key}={stored.get(key)}，第一次是 {created.get(key)}"
    if not isinstance(payload.get("created_utc"), str) or not payload["created_utc"]:
        return "没有 created_utc"
    return ""
