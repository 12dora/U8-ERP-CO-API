"""`archive-customer-address-rows`（客户收货地址新增 / 删除）的请求正文和响应、整户地址行断言。不连接服务器。

整户地址快照 Dump 和比对沿用 arc_cus_bodies：{行号: {列: 值}}，NULL 记 ``<nil>``，文本列 NULL 与空串算同一个。
"""

from __future__ import annotations

from dataclasses import dataclass

from co.e2e.casespec.arc_cus_bodies import Dump, cell_text, dump_problem, edited, reply_addr, row_of, with_flags
from co.e2e.casespec.arc_cus_person_bodies import shown
from co.e2e.casespec.arc_cus_person_sql import NIL


_MISSING_CUS = "ZZNOCUS01"
_NAME_LIMIT = 255
_UNIT = "E2E新增单位"


@dataclass(frozen=True)
class Call:
    """一次要发的调用：路由末段、正文、期望的 (状态码, 错误码)、说明；owner 非空时核对的是那个客户的地址行。"""

    op: str
    body: dict
    want: tuple[int, str]
    label: str
    owner: str = ""


def add_body(code: str, address: str, fields: dict, dry: bool = False) -> dict:
    body = {"code": code, "address": address, "fields": dict(fields)}
    return with_flags(body, None, dry, "cusaddradd")


def delete_body(code: str, address: str, expect: dict | None, dry: bool = False) -> dict:
    return with_flags({"code": code, "address": address}, expect, dry, "cusaddrdel")


def expect_of(dump: Dump, rid: str) -> dict:
    """删除用的 expect：这一行当前的名称、收货单位、默认标志。"""
    return {
        "name": cell_text(dump, rid, "cDeliverAdd"),
        "unit": cell_text(dump, rid, "cDeliverUnit"),
        "default": cell_text(dump, rid, "bDefault") == "1",
    }


def without(dump: Dump, rid: str) -> Dump:
    return {key: dict(cols) for key, cols in dump.items() if key != rid}


def reply_rows(payload: dict, ids: tuple[str, str], flag: bool, dumps: tuple[Dump, Dump]) -> str:
    """新增 / 删除的响应：ok、code、address、changed，以及整户 before / after 与写前写后的库快照逐行一致。"""
    if shown(payload.get("address")) != ids[1]:
        return f"响应 address={payload.get('address')!r} 期望 {ids[1]!r}"[:200]
    return reply_addr(payload, (ids[0], ""), flag, dumps)


def _blank(cols: dict[str, str]) -> list[str]:
    """新行里应保持 NULL 的列：英文地址、联系人。"""
    low = {name: name.lower() for name in cols}
    names = [name for name, key in low.items() if key.startswith("cenglishadd") or key == "clinkperson"]
    return [name for name in names if cols[name].strip() not in (NIL, "")]


def _new_row_problem(row: dict[str, str], rid: str, want: dict) -> str:
    text = {"cDeliverAdd": want["name"], "cDeliverUnit": want["unit"], "bDefault": "1" if want["default"] else "0"}
    one = {rid: row}
    for col, val in text.items():
        if cell_text(one, rid, col) != val:
            return f"新地址行 {col}={cell_text(one, rid, col)!r} 期望 {val!r}"[:200]
    guid = row.get("cCusAddressGUID")
    if guid is not None and guid.strip() in (NIL, ""):
        return "新地址行没有生成 cCusAddressGUID"
    left = _blank(row)
    return f"新地址行这些列应为 NULL：{left}"[:200] if left else ""


def added_problem(prev: Dump, now: Dump, ids: tuple[str, str], want: dict) -> str:
    """now 应等于 prev 加一行新地址（ids = 客户编码, 地址编码；want = name / unit / default）：新行的值、客户、其余列为空，
    其余行不变（default 为真时同户其他行的默认标志清 0）。"""
    rid = row_of(now, ids[1])
    if not rid or rid in prev:
        return "新地址行没有出现"
    row = now[rid]
    if row.get("cCusCode", ids[0]).strip() != ids[0]:
        return f"新地址行 cCusCode={row.get('cCusCode')!r} 期望 {ids[0]!r}"[:200]
    bad = _new_row_problem(row, rid, want)
    if bad:
        return bad
    rest = edited(prev, {key: {"bDefault": "0"} for key in prev}) if want["default"] else prev
    return dump_problem(rest, without(now, rid))


def clean_codes(dump: Dump) -> bool:
    """每条地址的编码都是 1 到 30 个字符、首尾无空格（路由才能定位）。"""
    return all(1 <= len(cols.get("cAddCode", "").strip()) <= 30 and cols["cAddCode"] == cols["cAddCode"].strip() for cols in dump.values())


def add_calls(code: str, addr: dict[str, str], names: dict[str, str]) -> list[Call]:
    """新增会被拒绝的调用。addr / names 的键 a 是已新增的那条，b 是还没用的空闲编码。"""
    one, two, name = addr["a"], addr["b"], names["a"]
    taken = (409, "address_code_taken")
    ops = "customer_address_add"
    return [
        Call(ops, add_body(code, one, {"name": name + "~", "unit": _UNIT}), taken, "同编码不同名称"),
        Call(ops, add_body(code, one, {"name": name, "unit": _UNIT + "~"}), taken, "同编码不同收货单位"),
        Call(ops, add_body(code, one, {"name": name, "unit": _UNIT, "default": True}), taken, "同编码不同默认标志"),
        Call(ops, add_body(code, two, {"name": name}), (409, "address_name_taken"), "名称已被本户另一地址占用"),
        Call(ops, add_body(code, two, {"unit": _UNIT}), (400, ""), "缺名称"),
        Call(ops, add_body(code, two, {"name": ""}), (400, ""), "名称为空"),
        Call(ops, add_body(code, two, {"name": "长" * (_NAME_LIMIT + 1)}), (400, ""), "名称超长"),
        Call(ops, add_body(code, "", {"name": names["b"]}), (400, ""), "地址编码为空"),
        Call(ops, add_body(_MISSING_CUS, two, {"name": names["b"]}), (404, "not_found"), "客户不存在"),
    ]


def delete_calls(code: str, addr: dict[str, str], exp: dict) -> list[Call]:
    """删除会被拒绝的调用。addr 的键 b 是现存的默认地址（exp 是它当前的 expect），g 是不存在的编码。"""
    two, ops = addr["b"], "customer_address_delete"
    return [
        Call(ops, delete_body(code, two, None), (400, ""), "缺 expect"),
        Call(ops, delete_body(code, two, {"name": exp["name"]}), (400, ""), "expect 只给了名称"),
        Call(ops, delete_body(code, two, {**exp, "name": exp["name"] + "~"}), (409, "state_mismatch"), "expect 名称不符"),
        Call(ops, delete_body(code, two, {**exp, "default": False}), (409, "state_mismatch"), "expect 默认标志不符"),
        Call(ops, delete_body(code, addr["g"], exp), (404, "address_not_found"), "地址不存在"),
        Call(ops, delete_body(_MISSING_CUS, two, exp), (404, "not_found"), "客户不存在"),
        Call(ops, delete_body(code, two, exp), (409, "address_is_default"), "默认地址且有其他地址"),
    ]
