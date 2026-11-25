"""列表步骤：按往来单位和日期筛选、只取键、翻页、按变更水位筛选、现存量按仓库和存货筛选。

响应形状以桥为准：{ok, items:[…], next, watermark}。next 是本页最后一行的 id，没有下一页为 null。
"""

from __future__ import annotations

import re

from co.e2e.casespec.assertutil import first_cell
from co.e2e.casespec.env import Env
from co.e2e.casespec.model import CaseSpec, Step
from co.e2e.casespec.doc_list_sql import list_count_sql, stock_count_sql
from co.e2e.casespec.sqlexec import run_guarded
from co.e2e.casespec.steps import auth_fields, invoke

LIMIT = 500
_KEYS_ONLY = {"id", "code", "ufts"}
_DIGITS = re.compile(r"^[0-9]{1,19}\Z")


def _ask(env: Env, route: str, extra: dict) -> dict:
    fields = auth_fields(env)
    fields.update(extra)
    status, payload = invoke(env.client, route, fields)
    if status != 200:
        raise ValueError(f"{route} {status} {payload.get('code')} {payload.get('message')}"[:200])
    return payload


def items_of(payload: dict) -> list[dict]:
    rows = payload.get("items")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise ValueError("列表响应缺 items")
    return rows


def ids_of(rows: list[dict]) -> list[int]:
    found = []
    for row in rows:
        raw = row.get("id")
        if isinstance(raw, bool) or not isinstance(raw, (int, str)) or not str(raw).isdigit():
            raise ValueError("列表行 id 不合法")
        found.append(int(raw))
    if found != sorted(set(found)):
        raise ValueError("列表 id 未按升序且唯一")
    return found


def _text(row: dict, key: str) -> str:
    val = row.get(key)
    return "" if val is None else str(val).strip()


def _pack(env: Env, key: str, count: int) -> list[str]:
    parts = [item.strip() for item in (env.bag.get(key) or "").split("|")]
    if len(parts) != count or any(item == "" for item in parts):
        raise ValueError("挑单结果不完整")
    return parts


def _count(env: Env, spec: CaseSpec, sql: str) -> int:
    cell = first_cell(run_guarded(env, sql, spec.name))
    if not cell.isdigit():
        raise ValueError("计数回读失败")
    return int(cell)


def _size_ok(rows: list, want: int, payload: dict) -> bool:
    # 行数与 SQL 计数一致；超过一页时正好一页并带 next。
    if want <= LIMIT:
        return len(rows) == want and payload.get("next") is None
    return len(rows) == LIMIT and payload.get("next") is not None


def _filter_body(kind: str, parts: list[str], keys_only: bool) -> dict:
    cus, low, high = parts
    body = {"type": kind, "filter": {"cus_code": cus, "date_from": low, "date_to": high}, "limit": LIMIT}
    if keys_only:
        body["keys_only"] = True
    return body


def _slot(spec: CaseSpec) -> str:
    return "core_ids:" + spec.name


def list_filter(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    args = dict(step.fields)
    parts = _pack(env, args["pick"], 3)
    payload = _ask(env, "/v1/vouchers/list", _filter_body(args["type"], parts, False))
    rows = items_of(payload)
    env.bag[_slot(spec)] = ",".join(str(num) for num in ids_of(rows))
    cus, low, high = parts
    for row in rows:
        day = _text(row, "doc_date")[:10]
        if _text(row, "cus_code") != cus or not low <= day <= high:
            return "FAIL", "筛选外的行 " + _text(row, "code")
    want = _count(env, spec, list_count_sql(args["type"], cus, low, high))
    if not _size_ok(rows, want, payload):
        return "FAIL", f"行数 {len(rows)} 期望 {want}"
    return "PASS", f"{len(rows)} 行"


def list_keys(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    args = dict(step.fields)
    parts = _pack(env, args["pick"], 3)
    rows = items_of(_ask(env, "/v1/vouchers/list", _filter_body(args["type"], parts, True)))
    for row in rows:
        if set(row) != _KEYS_ONLY or not _DIGITS.fullmatch(_text(row, "ufts")):
            return "FAIL", "keys_only 行的键 " + ",".join(sorted(row))
    got = ",".join(str(num) for num in ids_of(rows))
    if got != env.bag.get(_slot(spec), ""):
        return "FAIL", "keys_only 的 id 与完整列表不同"
    return "PASS", f"{len(rows)} 行"


def list_page(env: Env, _spec: CaseSpec, step: Step) -> tuple[str, str]:
    kind = dict(step.fields)["type"]
    # 每页 1 行：挑单只保证至少 2 张单据，每页 2 行时恰好 2 张就没有第二页。
    first = _ask(env, "/v1/vouchers/list", {"type": kind, "limit": 1})
    head = ids_of(items_of(first))
    after = str(first.get("next"))
    if len(head) != 1 or not after.isdigit() or int(after) != head[-1]:
        return "FAIL", "第一页应有 1 行且 next 为该行 id"
    second = ids_of(items_of(_ask(env, "/v1/vouchers/list", {"type": kind, "limit": 1, "after": int(after)})))
    if not second or second[0] <= head[-1]:
        return "FAIL", "第二页没有接在第一页之后"
    return "PASS", f"{head[-1]} → {second[0]}"


def _mark_slot(spec: CaseSpec) -> str:
    return "core_mark:" + spec.name


def list_mark(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    kind = dict(step.fields)["type"]
    mark = str(_ask(env, "/v1/vouchers/list", {"type": kind, "keys_only": True, "limit": 1}).get("watermark") or "")
    if _DIGITS.fullmatch(mark) is None:
        return "FAIL", "watermark 不是数字串"
    env.bag[_mark_slot(spec)] = mark
    return "PASS", mark


def list_since(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    kind = dict(step.fields)["type"]
    mark = env.bag.get(_mark_slot(spec), "")
    made = env.made.get(spec.name, "")
    if _DIGITS.fullmatch(mark) is None or not made.isdigit():
        return "FAIL", "缺水位或新建单据"
    body = {"type": kind, "keys_only": True, "changed_since": mark, "limit": LIMIT}
    rows = items_of(_ask(env, "/v1/vouchers/list", body))
    older = [row for row in rows if not _DIGITS.fullmatch(_text(row, "ufts")) or int(_text(row, "ufts")) <= int(mark)]
    if older:
        return "FAIL", "返回了水位之前的行 " + _text(older[0], "code")
    if int(made) not in ids_of(rows):
        return "FAIL", "新建单据不在变更列表里"
    return "PASS", f"{len(rows)} 行"


def stock_filter(env: Env, spec: CaseSpec, step: Step) -> tuple[str, str]:
    wh, inv = _pack(env, dict(step.fields)["pick"], 2)
    payload = _ask(env, "/v1/stock/current", {"wh": wh, "inv": inv, "limit": LIMIT})
    rows = items_of(payload)
    ids_of(rows)
    for row in rows:
        if _text(row, "wh_code") != wh or _text(row, "inv_code") != inv:
            return "FAIL", "筛选外的现存量行 " + _text(row, "id")
    want = _count(env, spec, stock_count_sql(wh, inv))
    if want == 0 or not _size_ok(rows, want, payload):
        return "FAIL", f"行数 {len(rows)} 期望 {want}"
    return "PASS", f"{len(rows)} 行"


LIST_OPS = {
    "list_filter": list_filter,
    "list_keys": list_keys,
    "list_page": list_page,
    "list_mark": list_mark,
    "list_since": list_since,
    "stock_filter": stock_filter,
}
