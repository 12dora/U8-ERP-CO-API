"""客户字段与收货地址用例的请求正文、响应断言、行快照比对。不连接服务器。

客户整行沿用 arc_cus_person_bodies 的 parse_row / changed；收货地址快照是 {行号: {列: 值}}，NULL 记 ``<nil>``。
"""

from __future__ import annotations

import datetime as dt
import os

from co.e2e.casespec.arc_cus_person_bodies import cell, changed, parse_row, shown
from co.e2e.casespec.arc_cus_person_sql import NIL

# 客户字段路由允许变的列之外，每次写入必变或可变的审计列（小写）。
STAMPS = frozenset({"cmodifyperson", "dmodifydate", "pubufts", "ufts"})
# 收货地址：路由把空值写成 NULL，原值可能是空串，比对时两者算同一个。
_LOOSE = frozenset({"cdeliveradd", "cdeliverunit"})
_TRUE = ("1", "true")
Dump = dict[str, dict[str, str]]


def new_key(tag: str) -> str:
    return f"e2e-{tag}-" + os.urandom(8).hex()


def with_flags(body: dict, expect: dict | None, dry: bool, tag: str) -> dict:
    if expect is not None:
        body["expect"] = dict(expect)
    if dry:
        body["dry_run"] = True  # 预演不带幂等键
    else:
        body["idempotency_key"] = new_key(tag)
    return body


def fields_body(code: str, fields: dict, expect: dict | None = None, dry: bool = False) -> dict:
    return with_flags({"code": code, "fields": dict(fields)}, expect, dry, "cusfields")


def addr_body(code: str, address: str, fields: dict, expect: dict | None = None, dry: bool = False) -> dict:
    body = {"code": code, "address": address, "fields": dict(fields)}
    return with_flags(body, expect, dry, "cusaddr")


def other_text(orig: str, tag: str, limit: int) -> str:
    """与原值不同、不超长的新文本。"""
    for cand in ((tag + orig)[:limit], (tag + "2")[:limit], tag[: max(1, limit - 1)] + "x"):
        if cand and cand != orig:
            return cand
    return "Q"


def other_date(orig: str, today: str, created: str) -> str:
    """不早于建档日、与原值不同的日期（yyyy-MM-dd）。"""
    floor = max(today[:10], created[:10]) if created[:10] not in ("", "-") else today[:10]
    day = dt.date.fromisoformat(floor)
    if day.isoformat() == orig[:10]:
        day += dt.timedelta(days=1)
    return day.isoformat()


# ---- 客户字段 ----


def load_row(cells: list[str]) -> dict[str, str]:
    """客户整行；dEndDate 只留日期部分。"""
    row = parse_row(cells)
    for key in row:
        if key.lower() == "denddate":
            row[key] = row[key][:10]
    return row


def field_view(row: dict[str, str]) -> dict[str, str]:
    return {
        "abbrname": cell(row, "cCusAbbName"),
        "district": cell(row, "cDCCode"),
        "end_date": cell(row, "dEndDate")[:10],
        "sa_protocol": cell(row, "cCusSAProtocol"),
        "name": cell(row, "cCusName"),
        "tax_no": cell(row, "cCusRegCode"),
        "contact": cell(row, "cCusPerson"),
        "bank_open": cell(row, "cCusBank"),
        "ccustradeccode": cell(row, "cCusTradeCCode"),
        "self_define1": cell(row, "cCusDefine1"),
    }


def _fields_of(raw: object, keys: tuple[str, ...]) -> dict[str, str] | None:
    """响应 before / after：键恰好是请求 fields 的键（多带或少带都要报出来）。"""
    if not isinstance(raw, dict) or set(raw) != set(keys):
        return None
    return {key: shown(raw.get(key))[:10] if key.endswith("_date") else shown(raw.get(key)) for key in keys}


def _reply_head(payload: dict, code: str) -> str:
    if payload.get("ok") is not True:
        return "响应 ok 应为 true"
    if str(payload.get("code") or "") != code:
        return f"响应 code={payload.get('code')!r} 期望 {code!r}"[:200]
    return ""


def _block(payload: dict, name: str, want: dict[str, str]) -> str:
    got = _fields_of(payload.get(name), tuple(want))
    if got != want:
        return f"响应 {name}={payload.get(name)!r} 期望 {want!r}"[:200]
    return ""


def reply_fields(payload: dict, code: str, views: tuple[dict, dict], flag: bool) -> str:
    """真写（含 no-op）的响应：changed 和 before / after 三个键都核对。"""
    bad = _reply_head(payload, code)
    if bad:
        return bad
    if payload.get("changed") is not flag:
        return f"响应 changed={payload.get('changed')!r} 期望 {flag!r}"
    return _block(payload, "before", views[0]) or _block(payload, "after", views[1])


def reply_dry(payload: dict) -> str:
    if payload.get("dry_run") is not True or str(payload.get("mode") or "") != "validate":
        return f"预演应 dry_run/validate，实际 {payload.get('dry_run')!r}/{payload.get('mode')!r}"[:200]
    return ""


def reply_fields_dry(payload: dict, code: str, before: dict[str, str]) -> str:
    return _reply_head(payload, code) or reply_dry(payload) or _block(payload, "before", before)


def merged(row: dict[str, str], edits: dict[str, str]) -> dict[str, str]:
    """row 的副本，按列名（不分大小写）改几格。"""
    out = dict(row)
    for name, val in edits.items():
        out[next((key for key in out if key.lower() == name.lower()), name)] = val
    return out


def row_problem(prev: dict[str, str], edits: dict[str, str], cur: dict[str, str], ignore: frozenset[str]) -> str:
    """cur 应等于 prev 加 edits；ignore 里的审计列不比。"""
    want = merged(prev, edits)
    hits = changed(want, cur, ignore)
    return f"客户行与预期不符 {hits}"[:200] if hits else ""


def stamp_problem(prev: dict[str, str], cur: dict[str, str], name: str) -> str:
    """真写之后行版本必变；知道操作员姓名时修改人也要对。"""
    if cell(prev, "pubufts") == cell(cur, "pubufts"):
        return "pubufts 应变化"
    if name.strip() and cell(cur, "cModifyPerson") != name.strip():
        return f"cModifyPerson={cell(cur, 'cModifyPerson')!r} 期望 {name.strip()!r}"[:200]
    return ""


# ---- 收货地址 ----


def parse_dump(cells: list[str]) -> Dump:
    out: Dump = {}
    for item in cells:
        parts = item.split("\t", 2)
        if len(parts) < 2 or not parts[0].strip() or not parts[1].strip():
            continue
        out.setdefault(parts[0].strip(), {})[parts[1].strip()] = parts[2] if len(parts) == 3 else ""
    return out


def _norm(col: str, val: str) -> str:
    low = col.lower()
    text = val.strip()
    if low in _LOOSE and text == NIL:
        return ""
    if low == "bdefault":
        return "1" if text.lower() in _TRUE else "0"
    return text


def dump_diff(want: Dump, got: Dump) -> list[str]:
    hits: list[str] = []
    for rid in sorted(set(want) | set(got)):
        one, two = want.get(rid), got.get(rid)
        if one is None or two is None:
            hits.append(f"{rid}:" + ("新增" if one is None else "缺失"))
            continue
        for col in sorted(set(one) | set(two)):
            if _norm(col, one.get(col, "")) != _norm(col, two.get(col, "")):
                hits.append(f"{rid}.{col}")
    return hits


def edited(dump: Dump, edits: dict[str, dict[str, str]]) -> Dump:
    """dump 的副本，按 {行号: {列: 值}} 改几格。"""
    out = {rid: dict(cols) for rid, cols in dump.items()}
    for rid, cols in edits.items():
        out.setdefault(rid, {}).update(cols)
    return out


def dump_problem(want: Dump, got: Dump) -> str:
    hits = dump_diff(want, got)
    return f"收货地址行与预期不符 {hits}"[:200] if hits else ""


def row_of(dump: Dump, code: str) -> str:
    """地址编码 → 行号，没有返回空串。"""
    for rid, cols in dump.items():
        if cols.get("cAddCode", "").strip() == code:
            return rid
    return ""


def cell_text(dump: Dump, rid: str, col: str) -> str:
    return _norm(col, dump.get(rid, {}).get(col, ""))


def default_edits(dump: Dump, target: str, flag: bool) -> dict[str, dict[str, str]]:
    """customer_address 设 default 的预期：设为 true 时同户其他行全清 0，设为 false 只清这一行。"""
    if not flag:
        return {target: {"bDefault": "0"}}
    return {rid: {"bDefault": "1" if rid == target else "0"} for rid in dump}


def sequence(new: str, orig: str, clear: bool) -> list[str]:
    """一个文本字段依次要设成的值：新值，（clear 时）清空，回原值；原值为空时清空这一步就是回原值。"""
    seq = [new]
    if clear and orig != "":
        seq.append("")
    seq.append(orig)
    return seq


def default_ids(dump: Dump) -> list[str]:
    ordered = sorted(dump.items(), key=lambda kv: int(kv[0]))
    return [rid for rid, cols in ordered if _norm("bDefault", cols.get("bDefault", "")) == "1"]


def _truthy(raw: object) -> bool:
    return raw is True or str(raw).strip().lower() in _TRUE


def dump_view(dump: Dump) -> list[tuple[str, str, str, bool]]:
    rows = [
        (
            _norm("cAddCode", cols.get("cAddCode", "")),
            _norm("cDeliverAdd", cols.get("cDeliverAdd", "")),
            _norm("cDeliverUnit", cols.get("cDeliverUnit", "")),
            _norm("bDefault", cols.get("bDefault", "")) == "1",
        )
        for cols in dump.values()
    ]
    return sorted(rows)


def payload_view(raw: object) -> list[tuple[str, str, str, bool]] | None:
    if not isinstance(raw, list):
        return None
    rows = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        text = (shown(item.get(key)) for key in ("code", "name", "unit"))
        rows.append((*text, _truthy(item.get("default"))))
    return sorted(rows)


def _view_block(payload: dict, name: str, dump: Dump) -> str:
    if payload_view(payload.get(name)) != dump_view(dump):
        return f"响应 {name} 与库里不符：{payload.get(name)!r}"[:200]
    return ""


def reply_addr(payload: dict, ids: tuple[str, str], flag: bool, dumps: tuple[Dump, Dump]) -> str:
    """ids = (客户, 新地址编码)；before / after 都是整户地址行，和写前写后的库快照逐行对。"""
    bad = _reply_head(payload, ids[0])
    if bad:
        return bad
    if payload.get("changed") is not flag:
        return f"响应 changed={payload.get('changed')!r} 期望 {flag!r}"
    if ids[1] and shown(payload.get("new_address")) != ids[1]:
        return f"响应 new_address={payload.get('new_address')!r} 期望 {ids[1]!r}"[:200]
    return _view_block(payload, "before", dumps[0]) or _view_block(payload, "after", dumps[1])


def reply_addr_dry(payload: dict, code: str, before: Dump) -> str:
    return _reply_head(payload, code) or reply_dry(payload) or _view_block(payload, "before", before)
