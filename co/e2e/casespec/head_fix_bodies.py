"""销售发货单表头修正（`voucher-head-fix`）用例的请求正文、响应断言和涉及行的快照比对。不连接服务器。

快照是 ``{标签: {行键: {列(小写): 值}}}``（标签见 head_fix_sql.snapshot_sql），NULL 记 ``<nil>``。行版本列（ufts）在被改的行上
必然变化，比对时忽略。目标列（caddcode、cPersonCode、往来明细的 cPerson）按桥文档里的归一化比较：去首尾空白、不分大小写、
空串与 NULL 相同；其余列逐字比较。地址原本是 NULL 的样本用严格比较（NULL 必须还原成 NULL）。

回滚靠正向响应的 ``undo``：字段 → 表 → 主键（往来明细是 Auto_ID）。期望的变化格子直接由 undo 推出，再和响应的 downstream
互相核对，往来明细只能是被更新发票自己的行。
"""

from __future__ import annotations

import hashlib

from co.e2e.casespec.arc_cus_bodies import with_flags
from co.e2e.casespec.arc_cus_person_bodies import shown
from co.e2e.casespec.env import Env
from co.e2e.casespec.arc_cus_person_sql import NIL
from co.e2e.casespec.steps import auth_fields, invoke

ROUTE = "/v1/vouchers/head_fix"
# 请求字段 → 表头列（三张表同名；往来明细只有业务员，列名 cperson）。
FIELD_COLS = {"addr_code": "caddcode", "person_code": "cpersoncode"}
STAMPS = frozenset({"ufts", "pubufts"})
TARGET_COLS = frozenset({"caddcode", "cpersoncode", "cperson"})
# 响应 downstream 里的表名 → 快照标签。
TAGS = {"rdrecord32": "RD", "SaleBillVouch": "SB"}
UNDO_TABLES = {**TAGS, "Ar_Detail": "AR"}
_MISSING = "<缺>"
Snap = dict[str, dict[str, dict[str, str]]]
Cell = tuple[str, str, str]
Undo = dict[str, dict[str, list[int]]]


def hf_body(dlid: str, fields: dict, expect: dict | None, dry: bool = False, undo: dict | None = None) -> dict:
    body = {"type": "dispatch", "id": int(dlid), "fields": dict(fields)}
    if undo is not None:
        body["undo"] = {key: {table: list(ids) for table, ids in tables.items()} for key, tables in undo.items()}
    return with_flags(body, expect, dry, "headfix")


def send_hf(env: Env, body: dict) -> tuple[int, dict]:
    fields = auth_fields(env)
    fields.update(body)
    return invoke(env.client, ROUTE, fields)


# ---- 快照 ----


def parse_snap(cells: list[str]) -> Snap:
    out: Snap = {}
    for item in cells:
        parts = item.split("\t", 3)
        if len(parts) < 3 or not parts[0].strip() or not parts[1].strip() or not parts[2].strip():
            continue
        row = out.setdefault(parts[0].strip(), {}).setdefault(parts[1].strip(), {})
        row[parts[2].strip().lower()] = (parts[3] if len(parts) == 4 else "").strip()
    return out


def _flat(snap: Snap) -> dict[Cell, str]:
    return {
        (tag, key, col): val for tag, rows in snap.items() for key, cols in rows.items() for col, val in cols.items()
    }


def norm_val(value: str) -> str:
    """目标列的归一化：NULL 当空串，去首尾空白，不分大小写。"""
    return "" if value == NIL else value.strip().lower()


def _differs(col: str, one: str, two: str, loose: bool) -> bool:
    if one == two:
        return False
    if loose and col in TARGET_COLS and _MISSING not in (one, two):
        return norm_val(one) != norm_val(two)
    return True


def changes(before: Snap, after: Snap, loose: bool = True) -> dict[Cell, tuple[str, str]]:
    """两份快照之间变了的格子 {(标签, 行键, 列): (原值, 新值)}；整行出现或消失时值记 ``<缺>``；行版本列不算。

    loose 为真时目标列按归一化比较（见模块说明）。
    """
    one, two = _flat(before), _flat(after)
    hits: dict[Cell, tuple[str, str]] = {}
    for cell in set(one) | set(two):
        if cell[2] in STAMPS:
            continue
        old, new = one.get(cell, _MISSING), two.get(cell, _MISSING)
        if _differs(cell[2], old, new, loose):
            hits[cell] = (old, new)
    return hits


def fingerprint(snap: Snap) -> str:
    lines = sorted(f"{tag}|{key}|{col}|{val}" for (tag, key, col), val in _flat(snap).items() if col not in STAMPS)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:16]


def dispatch_key(snap: Snap) -> str:
    keys = sorted(snap.get("DL", {}))
    if len(keys) != 1:
        raise ValueError("快照里没有唯一的发货单表头")
    return keys[0]


def plain(val: str) -> str:
    return "" if val == NIL else val


def view_of(snap: Snap) -> dict[str, str]:
    """发货单表头当前的 addr_code / person_code（NULL 记空串，和请求的 expect 同口径）。"""
    row = snap["DL"][dispatch_key(snap)]
    return {name: plain(row.get(col, "")) for name, col in FIELD_COLS.items()}


def row_ids(snap: Snap, tag: str) -> list[str]:
    return sorted(snap.get(tag, {}), key=lambda item: (len(item), item))


def _db(value: str) -> str:
    return NIL if value == "" else value


# ---- undo ----


def undo_of(payload: dict) -> Undo | None:
    """响应里的 undo 规整成 {字段: {表: [主键(升序)]}}；缺少、形状不对或有不认识的字段 / 表时返回 None。"""
    raw = payload.get("undo")
    if not isinstance(raw, dict):
        return None
    out: Undo = {}
    for name, tables in raw.items():
        if name not in FIELD_COLS or not isinstance(tables, dict):
            return None
        for table, ids in tables.items():
            if table not in UNDO_TABLES or not isinstance(ids, list) or any(type(item) is not int for item in ids):
                return None
            if ids:
                out.setdefault(name, {})[table] = sorted(ids)
    return out


def undo_ids(undo: Undo, name: str, table: str) -> list[int]:
    return undo.get(name, {}).get(table, [])


def _undo_pairs(undo: Undo) -> set[tuple[str, str, str]]:
    return {(name, table, str(item)) for name, tables in undo.items() for table, ids in tables.items() for item in ids}


def _invoices(snap: Snap, ids: list[str]) -> set[tuple[str, str]]:
    """发票表头快照里的 (cVouchType, 发票号)。"""
    return {(snap["SB"][key].get("cvouchtype", ""), snap["SB"][key].get("csbvcode", "")) for key in ids if key in snap.get("SB", {})}


def _own_rows(snap: Snap, invoices: set[tuple[str, str]]) -> set[str]:
    return {
        key
        for key, row in snap.get("AR", {}).items()
        if (row.get("cvouchtype", ""), row.get("cvouchid", "")) in invoices
    }


def ar_problem(snap: Snap, undo: Undo, exact: str) -> str:
    """往来明细只能是发票自己的行（cVouchType、cVouchID 等于发票）。exact 非空（旧业务员）时，被改的行还必须恰是
    被更新发票自己 cPerson 等于它的全部行；空串时（回滚）只要求都是本发货单发票自己的行。"""
    got = {str(item) for item in undo_ids(undo, "person_code", "Ar_Detail")}
    if not got and not exact:
        return ""
    updated = [str(item) for item in undo_ids(undo, "person_code", "SaleBillVouch")]
    scope = _own_rows(snap, _invoices(snap, updated if exact else list(snap.get("SB", {}))))
    if not got <= scope:
        return f"Ar_Detail {sorted(got - scope)} 不是发票自己的行"[:200]
    if exact:
        mine = {key for key in scope if norm_val(snap["AR"][key].get("cperson", "")) == norm_val(exact)}
        if got != mine:
            return f"Ar_Detail 改了 {sorted(got)}，发票自己 cPerson 为旧值的行是 {sorted(mine)}"[:200]
    return ""


def ar_report(snap: Snap, undo: Undo) -> str:
    """被改的往来明细行按 cProcStyle / cCancelNo 汇总，写进用例说明。"""
    counts: dict[str, int] = {}
    for item in undo_ids(undo, "person_code", "Ar_Detail"):
        row = snap.get("AR", {}).get(str(item), {})
        label = f"{plain(row.get('cprocstyle', '')) or '-'}/{plain(row.get('ccancelno', '')) or '-'}"
        counts[label] = counts.get(label, 0) + 1
    return "Ar_Detail " + ",".join(f"{label}×{n}" for label, n in sorted(counts.items())) if counts else ""


def _fields_of(entry: dict, todo: dict[str, str]) -> list[str]:
    """这一行实际改了哪几个字段：响应给了 fields 就按它（部分更新），没给按全部。"""
    raw = entry.get("fields")
    return [str(item) for item in raw] if isinstance(raw, list) else list(todo)


def _cross(snap: Snap, down: list[dict], undo: Undo, todo: dict[str, str]) -> str:
    """响应 downstream 标 updated 的出库单、发票（及字段）与 undo 逐条一致；往来明细行数与 undo 里该发票的行数一致。"""
    claims = {
        (name, str(entry["table"]), str(entry.get("id")))
        for entry in down
        if entry.get("action") == "updated" and entry.get("table") in TAGS
        for name in _fields_of(entry, todo)
    }
    given = {pair for pair in _undo_pairs(undo) if pair[1] in TAGS}
    if claims != given:
        return f"downstream 与 undo 不一致：只在 downstream {sorted(claims - given)}，只在 undo {sorted(given - claims)}"[:200]
    codes: dict[str, int] = {}
    for item in undo_ids(undo, "person_code", "Ar_Detail"):
        code = snap.get("AR", {}).get(str(item), {}).get("cvouchid", "")
        codes[code] = codes.get(code, 0) + 1
    rows = {shown(e.get("invoice")): e.get("rows") for e in down if e.get("table") == "Ar_Detail" and e.get("action") == "updated"}
    if rows != codes:
        return f"Ar_Detail 行数 downstream {rows!r} 与 undo {codes!r} 不一致"[:200]
    return ""


def want_changes(
    snap: Snap, fields: dict[str, str], down: list[dict], undo: Undo, exact: bool = True
) -> tuple[dict[Cell, str], str]:
    """快照里应当变的格子 {格子: 新值}：发货单表头加上 undo 列出的下游行；响应和快照对不上给出原因。

    undo 是桥自己报告的「写了哪些行」，再和 downstream、往来明细的归属规则互相核对。exact 为假是回滚调用（只改 undo 列出的行，
    不做「发票自己 cPerson 为旧值的全部行」那条核对）。
    """
    key = dispatch_key(snap)
    old = view_of(snap)
    todo = {name: val for name, val in fields.items() if norm_val(old[name]) != norm_val(val)}
    want: dict[Cell, str] = {("DL", key, FIELD_COLS[name]): _db(val) for name, val in todo.items()}
    for name, tables in undo.items():
        if name not in todo:
            return want, f"undo 里有没改的字段 {name}"
        for table, ids in tables.items():
            tag = UNDO_TABLES[table]
            for item in ids:
                if str(item) not in snap.get(tag, {}):
                    return want, f"undo 里的 {table} {item} 不在快照里"
                want[(tag, str(item), "cperson" if tag == "AR" else FIELD_COLS[name])] = _db(todo[name])
    old_person = old["person_code"] if exact and "person_code" in todo else ""
    return want, ar_problem(snap, undo, old_person) or _cross(snap, down, undo, todo)


def _names(cells: list[Cell]) -> str:
    return ",".join(f"{tag}.{key}.{col}" for tag, key, col in sorted(cells))[:120]


def change_problem(before: Snap, after: Snap, want: dict[Cell, str]) -> str:
    """快照之间只允许 want 里的格子变成指定的值；多出来的、缺的、值不对的都报。"""
    got = changes(before, after)
    extra = [cell for cell in got if cell not in want]
    miss = [cell for cell in want if cell not in got]
    wrong = [cell for cell in want if cell in got and _differs(cell[2], got[cell][1], want[cell], True)]
    parts = []
    for label, cells in (("多改了", extra), ("没改到", miss), ("值不对", wrong)):
        if cells:
            parts.append(f"{label} {_names(cells)}")
    return "；".join(parts)[:200]


def same_problem(before: Snap, after: Snap, loose: bool = True) -> str:
    """两份快照应逐格一致（行版本列除外；loose 时目标列按归一化比较）。"""
    got = changes(before, after, loose)
    return f"快照有差异 {_names(list(got))}"[:200] if got else ""


# ---- 响应 ----


def downstream_of(payload: dict) -> list[dict]:
    raw = payload.get("downstream")
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def entry_of(down: list[dict], table: str, row: str = "") -> dict | None:
    for item in down:
        if item.get("table") == table and (not row or str(item.get("id")) == row):
            return item
    return None


def _block(payload: dict, name: str, want: dict[str, str]) -> str:
    raw = payload.get(name)
    if not isinstance(raw, dict):
        return f"响应 {name} 不是对象"
    got = {key: shown(raw.get(key)) for key in want}
    if got != want:
        return f"响应 {name}={raw!r} 期望 {want!r}"[:200]
    return ""


def _head(payload: dict, dlid: str) -> str:
    if payload.get("ok") is not True:
        return "响应 ok 应为 true"
    if payload.get("type") != "dispatch":
        return f"响应 type={payload.get('type')!r}"
    if str(payload.get("id")) != dlid:
        return f"响应 id={payload.get('id')!r} 期望 {dlid!r}"
    return ""


def reply_hf(payload: dict, dlid: str, flag: bool, views: tuple[dict, dict]) -> str:
    """真写（含无事可做）的响应：changed、before、after 都核对；views 只含请求里带的键。"""
    bad = _head(payload, dlid)
    if bad:
        return bad
    if payload.get("changed") is not flag:
        return f"响应 changed={payload.get('changed')!r} 期望 {flag!r}"
    return _block(payload, "before", views[0]) or _block(payload, "after", views[1])


def reply_hf_dry(payload: dict, dlid: str, before: dict[str, str]) -> str:
    bad = _head(payload, dlid)
    if bad:
        return bad
    if payload.get("dry_run") is not True or str(payload.get("mode") or "") != "validate":
        return f"预演应 dry_run/validate，实际 {payload.get('dry_run')!r}/{payload.get('mode')!r}"[:200]
    return _block(payload, "before", before)


def skipped_problem(down: list[dict], table: str, row: str) -> str:
    """合并发票应被跳过，原因是 shared 或 differs。"""
    item = entry_of(down, table, row)
    if item is None or item.get("action") != "skipped":
        return f"{table} {row} 应被跳过，实际 {item!r}"[:200]
    if item.get("reason") not in ("shared", "differs"):
        return f"{table} {row} 跳过原因 {item.get('reason')!r}"[:200]
    return ""


def updated_problem(down: list[dict], table: str, row: str, need: tuple[str, ...] = ()) -> str:
    """应被更新；need 里的字段都要在 fields 里（响应给了 fields 时）。"""
    item = entry_of(down, table, row)
    if item is None or item.get("action") != "updated":
        return f"{table} {row} 应被更新，实际 {item!r}"[:200]
    if isinstance(item.get("fields"), list) and set(need) - set(item["fields"]):
        return f"{table} {row} 只更新了 {item['fields']!r}，期望 {list(need)!r}"[:200]
    return ""


def already_new_problem(down: list[dict], table: str, row: str) -> str:
    """下游行已经是新值：不更新，不报错，记 skipped、reason=already_new。"""
    item = entry_of(down, table, row)
    if item is None or item.get("action") != "skipped" or item.get("reason") != "already_new":
        return f"{table} {row} 应记 skipped / already_new，实际 {item!r}"[:200]
    return ""
