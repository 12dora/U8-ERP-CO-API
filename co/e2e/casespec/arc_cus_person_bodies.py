"""客户专管业务员用例的正文、health 例外列表、SQL 行解析。不连接服务器。"""

from __future__ import annotations

from co.e2e.casespec.env import Env
from co.e2e.casespec.arc_cus_person_sql import NIL
from co.e2e.casespec.steps import auth_fields, invoke

_ROUTE = "/v1/archives/"
# 专管写入允许变的列：修改人、修改日期、行版本（timestamp / ufts）。
_FIXED = frozenset({"cModifyPerson", "dModifyDate", "pubufts", "ufts"})


def send(env: Env, op: str, extra: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(extra)
    return invoke(env.client, _ROUTE + op, fields)


def person_body(code: str, person: str, expect: str | None = None, dry: bool = False, key: str = "") -> dict:
    body: dict = {"code": code, "person": person}
    if expect is not None:
        body["expect"] = expect
    if dry:
        body["dry_run"] = True
    if key:
        body["idempotency_key"] = key
    return body


def update_body(code: str, fields: dict, key: str = "") -> dict:
    body = {"archive": "customer", "code": code, "fields": fields}
    if key:
        body["idempotency_key"] = key
    return body


def shown(raw: object) -> str:
    return "" if raw is None else str(raw).strip()


def cell(row: dict[str, str], name: str) -> str:
    want = name.lower()
    for key, val in row.items():
        if key.lower() == want:
            return str(val or "").strip()
    return ""


def expect(status: int, payload: dict, want: tuple[int, str], label: str) -> str:
    code = str(payload.get("code") or "")
    if status != want[0] or (want[1] and code != want[1]):
        return f"{label}应 {want[0]} {want[1]}，实际 {status} {code}"[:200]
    return ""


def reply_base(payload: dict, code: str, before: str) -> str:
    if payload.get("ok") is not True:
        return "响应 ok 应为 true"
    if str(payload.get("code") or "") != code:
        return f"响应 code={payload.get('code')!r} 期望 {code!r}"[:200]
    if shown(payload.get("before")) != before:
        return f"响应 before={payload.get('before')!r} 期望 {before!r}"[:200]
    return ""


def reply_write(payload: dict, code: str, before: str, after: str, depts: tuple[str, str] = ("", "")) -> str:
    bad = reply_base(payload, code, before)
    if bad:
        return bad
    if shown(payload.get("after")) != after:
        return f"响应 after={payload.get('after')!r} 期望 {after!r}"[:200]
    if shown(payload.get("dept_before")) != depts[0]:
        return f"响应 dept_before={payload.get('dept_before')!r} 期望 {depts[0]!r}"[:200]
    if shown(payload.get("dept_after")) != depts[1]:
        return f"响应 dept_after={payload.get('dept_after')!r} 期望 {depts[1]!r}"[:200]
    return ""


def reply_dry(payload: dict, code: str, before: str) -> str:
    bad = reply_base(payload, code, before)
    if bad:
        return bad
    if payload.get("dry_run") is not True or str(payload.get("mode") or "") != "validate":
        return f"预演应 dry_run/validate，实际 {payload.get('dry_run')!r}/{payload.get('mode')!r}"[:200]
    return ""


def _rules(health: dict) -> list[dict]:
    rows: list[dict] = []
    for key in ("read_only_exceptions", "api_read_only_exceptions"):
        val = health.get(key)
        if isinstance(val, list):
            rows.extend(item for item in val if isinstance(item, dict))
    return rows


def listed_exceptions(health: dict) -> bool:
    return bool(_rules(health))


def volatile_set(names: list[str]) -> frozenset[str]:
    found = {item.strip() for item in names if item.strip()}
    return frozenset(_FIXED | found)


def parse_row(cells: list[str]) -> dict[str, str]:
    """``customer_row_sql`` 的逐列输出 → {列名: 值}；``<nil>`` 记为空串。"""
    out: dict[str, str] = {}
    for item in cells:
        name, sep, value = item.partition("\t")
        if sep and name:
            out[name.strip()] = "" if value.strip() == NIL else value.strip()
    return out


def changed(before: dict[str, str], after: dict[str, str], ignore: frozenset[str]) -> list[str]:
    skip = {name.lower() for name in ignore}
    keys = set(before) | set(after)
    hits = []
    for key in keys:
        if key.lower() in skip:
            continue
        old, new = (before.get(key) or "").strip(), (after.get(key) or "").strip()
        if old != new:
            hits.append(key)
    return sorted(hits)


def parse_auth(cells: list[str]) -> list[tuple[str, str, str]]:
    """Customer_Auth 快照：``autoId, Privilege_ID, Privilege_Type``。"""
    rows: list[tuple[str, str, str]] = []
    for item in cells:
        parts = item.split("\t")
        if len(parts) >= 3 and parts[0].strip():
            rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return rows


def check_auth(
    before: list[tuple[str, str, str]], after: list[tuple[str, str, str]], new_dept: str, changed_dept: bool
) -> str:
    others_b = [row for row in before if row[2] != "0"]
    others_a = [row for row in after if row[2] != "0"]
    if others_b != others_a:
        return "Customer_Auth 非部门行被改了"
    zero_b = [row for row in before if row[2] == "0"]
    zero_a = [row for row in after if row[2] == "0"]
    if not changed_dept:
        return "" if zero_b == zero_a else "Customer_Auth 部门行不应变化"
    if not zero_a:
        return "Customer_Auth 缺少部门行"
    if any(row[1] != new_dept for row in zero_a):
        return f"Customer_Auth Privilege_ID 应为 {new_dept}"[:200]
    return ""
