"""名称 → 编码解析（archives/resolve）用例，名 `resolve-archives`。只读，不改账套。

名称都在运行时取：archives/list 列出客户（至多 50 行），只留 SQL 查到的登录日期还在用（未停用）的，
挑一个名称至少 4 个字、在列表里不重名的客户：
  按编码解析 exact（match=code），同一请求里再带一项查不到的 none（候选为空、more=false）；
  按名称解析 exact 或 ambiguous（整张表可能有重名），候选里有它；
  按名称中间一段解析 partial 或 ambiguous，候选里有它；
  歧义：测试数据 resolve.ambiguous_q 给了就用它，必须 ambiguous 且候选至少 2 个；没给时从列表里找两个以上名称共有、
    又不等于任何编码或名称的片段，逐个试，第一个解析成 ambiguous 的要求候选至少 2 个（exact / partial 的片段不算数，
    都不行就不测这一项）；
  停用：SQL 挑一个登录日期已停用的客户，缺省（include_disabled=false）候选里没有它、include_disabled=true 时 exact 且 disabled=true
    （账套里没有停用客户就不测这一项）；
  不支持的档案（客户银行账户）400，field=items.0.archive。
权限、记录级过滤不在这里测。
"""

from __future__ import annotations

import json
import os
import re

from co.e2e.casespec.assertutil import first_cell, sql_cells
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.dry_judge import common_pieces, middle_piece, resolve_problem, result_codes
from co.e2e.casespec.dry_ops import send
from co.e2e.casespec.dry_sql import disabled_customer_sql, enabled_customers_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.testdata import DataMissing
from co.e2e.testdata import value as td

ROUTE = "/v1/archives/resolve"
_LIST = "/v1/archives/list"
_ARC = "customer"
_CODE = re.compile(r"^[0-9A-Za-z._-]{1,20}\Z")
_NAMES = "rsv_names"


def _ask(env: Env, items: list[dict], limit: int = 20, disabled: bool = False) -> list[object]:
    body: dict = {"items": items, "limit": limit}
    if disabled:
        body["include_disabled"] = True
    status, payload = send(env, ROUTE, body)
    results = payload.get("results") if status == 200 else None
    if not isinstance(results, list) or len(results) != len(items):
        raise ValueError(f"resolve {status} {payload.get('code')} {payload.get('message')}"[:200])
    return results


def _one(env: Env, text: str, disabled: bool = False) -> object:
    return _ask(env, [{"archive": _ARC, "q": text}], 20, disabled)[0]


def _target(rows: list[dict]) -> tuple[str, str]:
    names = [str(row.get("name") or "").strip() for row in rows]
    for row, name in zip(rows, names):
        code = str(row.get("code") or "")
        if _CODE.fullmatch(code) and len(name) >= 4 and names.count(name) == 1:
            return code, name
    return "", ""


def _enabled(env: Env, spec: CaseSpec) -> set[str]:
    # 登录日期还在用的客户编码：解析缺省排除停用的，目标和歧义片段都只从这些里取。
    return set(sql_cells(run_guarded(env, enabled_customers_sql(env.today), spec.name)))


def pick(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    status, payload = send(env, _LIST, {"archive": _ARC, "limit": 50})
    rows = payload.get("items") if status == 200 else None
    if not isinstance(rows, list):
        return "FAIL", f"客户列表 {status} {payload.get('code')}"
    enabled = _enabled(env, spec)
    rows = [row for row in rows if isinstance(row, dict) and str(row.get("code") or "") in enabled]
    code, name = _target(rows)
    if not code:
        return "N/A", "本账套没有可用来解析的（未停用的）客户"
    env.bag.update({"rsv_code": code, "rsv_name": name})
    env.bag[_NAMES] = json.dumps([[str(row.get("code") or ""), str(row.get("name") or "")] for row in rows])
    return "PASS", code


def by_code(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    code, missing = env.bag["rsv_code"], "无此客户" + os.urandom(4).hex()
    first, second = _ask(env, [{"archive": _ARC, "q": code}, {"archive": _ARC, "q": missing}], 5)
    problem = resolve_problem(first, ("exact",), code) or resolve_problem(second, ("none",))
    if not problem and (first.get("match") or {}).get("match") != "code":
        problem = f"match.match={(first.get('match') or {}).get('match')}，期望 code"
    if not problem and (first.get("q"), second.get("q")) != (code, missing):
        problem = "结果没有按请求顺序回显 q"
    return ("FAIL", problem) if problem else ("PASS", "exact + none")


def by_name(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    code, name = env.bag["rsv_code"], env.bag["rsv_name"]
    problem = resolve_problem(_one(env, name), ("exact", "ambiguous"), code)
    if not problem:
        problem = resolve_problem(_one(env, middle_piece(name)), ("partial", "ambiguous"), code)
    return ("FAIL", problem) if problem else ("PASS", "name + contains")


def _fallback_pieces(env: Env) -> list[str]:
    pairs = json.loads(env.bag.get(_NAMES) or "[]")
    taken = {text for pair in pairs for text in pair}
    return common_pieces([pair[1] for pair in pairs], taken)


def _ambiguous_problem(result: object) -> str:
    problem = resolve_problem(result, ("ambiguous",))
    if not problem and len(result.get("candidates") or []) < 2:
        problem = "ambiguous 的候选少于 2 个"
    return problem


def ambiguous(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    try:
        text = td("resolve.ambiguous_q")
    except DataMissing:
        text = ""
    if text:
        # 测试数据给的片段：必须 ambiguous。
        problem = _ambiguous_problem(_one(env, text))
        return ("FAIL", f"「{text}」{problem}") if problem else ("PASS", "ambiguous " + text)
    # 没配时从列表里找共有片段；片段可能碰巧等于某个客户的简称、助记码（exact）或只剩一个在用的（partial），
    # 这种片段不算数，换下一个；都不行就不测这一项。
    for piece in _fallback_pieces(env):
        result = _one(env, piece)
        if isinstance(result, dict) and result.get("status") == "ambiguous":
            problem = _ambiguous_problem(result)
            return ("FAIL", f"「{piece}」{problem}") if problem else ("PASS", "ambiguous " + piece)
    return "PASS", "找不到能构成歧义的片段（可配 resolve.ambiguous_q），歧义未测"


def disabled(env: Env, spec: CaseSpec, _step: Step) -> tuple[str, str]:
    code = first_cell(run_guarded(env, disabled_customer_sql(env.today), spec.name)).strip()
    if _CODE.fullmatch(code) is None:
        return "PASS", "账套里没有停用的客户，停用未测"
    hidden = _one(env, code)
    # 缺省排除停用：候选里不能有它（编码碰巧含在别的客户名称里时可能是 partial，只要不是它就行）。
    problem = "" if isinstance(hidden, dict) and code not in result_codes(hidden) else "缺省解析仍返回了停用客户"
    shown = _one(env, code, True)
    problem = problem or resolve_problem(shown, ("exact",), code)
    rows = shown.get("candidates") if isinstance(shown, dict) else None
    first = rows[0] if isinstance(rows, list) and rows else None
    if not problem and not (isinstance(first, dict) and first.get("disabled") is True):
        problem = "include_disabled 时候选没有标 disabled"
    return ("FAIL", f"停用客户 {code}：{problem}") if problem else ("PASS", "disabled " + code)


def bad_archive(env: Env, _spec: CaseSpec, _step: Step) -> tuple[str, str]:
    status, payload = send(env, ROUTE, {"items": [{"archive": "customer_bank", "q": "x"}]})
    if status != 400 or payload.get("code") != "bad_request":
        return "FAIL", f"{status} {payload.get('code')}，期望 400 bad_request"
    if payload.get("field") != "items.0.archive":
        return "FAIL", f"field={payload.get('field')!r}，期望 items.0.archive"
    return "PASS", "400 items.0.archive"


DRY_RESOLVE_OPS = {
    "dry_rs_pick": pick,
    "dry_rs_code": by_code,
    "dry_rs_name": by_name,
    "dry_rs_ambiguous": ambiguous,
    "dry_rs_disabled": disabled,
    "dry_rs_bad": bad_archive,
}


def resolve_case() -> CaseSpec:
    ops = ("dry_rs_pick", "dry_rs_code", "dry_rs_name", "dry_rs_ambiguous", "dry_rs_disabled", "dry_rs_bad")
    steps = tuple(Step("custom", ROUTE, sql_name=op, id_from="none") for op in ops)
    title = "名称解析：编码 exact、查不到 none、名称和包含、歧义、停用客户、不支持的档案 400"
    tags = frozenset(("arc-read:customer",))
    return CaseSpec("resolve-archives", title, (), steps, True, tags=tags, routes=(ROUTE, _LIST))
