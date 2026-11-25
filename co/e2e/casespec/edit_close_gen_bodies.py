"""修改、关闭和生单的正文。数字和主键只接受挑单或回读里的安全文本。"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from co.e2e.casespec.assertutil import as_num, multiple_qty
from co.e2e.casespec.env import Env
from co.e2e.testdata import value as td

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_NUM = re.compile(r"^[0-9]+(\.[0-9]+)?\Z")
# 与 picks._CODE、挑单 SQL 的 NOT LIKE '%[^0-9A-Za-z._-]%' 同一字符集：字母、数字、-、_、.
_TOKEN = re.compile(r"^[0-9A-Za-z._-]{1,60}\Z")
_INT_MAX = 2147483647


def _as_id(raw: str, label: str) -> int:
    if _ID.fullmatch(raw or "") is None:
        raise ValueError(label)
    num = int(raw)
    if num > _INT_MAX:
        raise ValueError(label)
    return num


def _focus(env: Env) -> int:
    return _as_id(env.focus, "单据 id 不合法")


def _bag_id(env: Env, key: str) -> int:
    return _as_id(env.bag.get(key, ""), "明细 id 不合法")


def _plain(value: str, limit: int) -> str:
    text = value.strip()
    if text == "" or len(text) > limit or "\n" in text or "\r" in text:
        raise ValueError("主数据不完整")
    return text


def _token(value: str) -> str:
    text = value.strip()
    if _TOKEN.fullmatch(text) is None:
        raise ValueError("主数据不完整")
    return text


def _number(text: str, allow_zero: bool) -> int | float:
    raw = text.strip()
    if _NUM.fullmatch(raw) is None:
        raise ValueError("主数据不完整")
    num = Decimal(raw)
    if num < 0 or (num == 0 and not allow_zero):
        raise ValueError("主数据不完整")
    return as_num(format(num, "f"))


def _plus(env: Env, key: str) -> int | float:
    raw = env.bag.get(key, "")
    if _NUM.fullmatch(raw) is None:
        raise ValueError("数量不合法")
    return _number(format(Decimal(raw) + 1, "f"), False)


def _parts(text: str, count: int) -> list[str]:
    parts = (text or "").split("|")
    if len(parts) != count:
        raise ValueError("主数据不完整")
    return [item.strip() for item in parts]


def _sale_add(parts: list[str], today: str) -> dict:
    inv, rate, price, tax, _pre = parts[4:]
    when = (date.fromisoformat(today) + timedelta(days=3)).isoformat()
    return {
        "op": "add",
        "cInvCode": _plain(inv, 60),
        "iQuantity": as_num(multiple_qty(rate)),
        "iTaxUnitPrice": as_num(price or "1"),
        "iTaxRate": as_num(tax or "13"),
        "dPreDate": when,
    }


def sale_update(env: Env) -> dict:
    # sale_body 只建一行，所以这里改这一行并追加一行，不删除。
    parts = _parts(env.bag.get("sale_master", ""), 9)
    if any(item == "" for item in parts[:5]):
        raise ValueError("主数据不完整")
    lines = [
        {"op": "update", "line_id": _bag_id(env, "so_line"), "iQuantity": _plus(env, "so_qty")},
        _sale_add(parts, env.today),
    ]
    return {"type": "sale_order", "id": _focus(env), "head": {"cMemo": "CO桥回归改"}, "lines": lines}


def sale_drop(env: Env) -> dict:
    line = {"op": "delete", "line_id": _bag_id(env, "so_extra")}
    return {"type": "sale_order", "id": _focus(env), "lines": [line]}


def sale_touch(env: Env) -> dict:
    return {"type": "sale_order", "id": _focus(env), "head": {"cMemo": "CO桥回归"}}


def _line_action(env: Env, kind: str, action: str, key: str) -> dict:
    return {"type": kind, "id": _focus(env), "action": action, "line_ids": [_bag_id(env, key)]}


def sale_close_line(env: Env) -> dict:
    return _line_action(env, "sale_order", "close", "sale_line")


def sale_open_line(env: Env) -> dict:
    return _line_action(env, "sale_order", "open", "sale_line")


def sale_close_made(env: Env) -> dict:
    return {"type": "sale_order", "id": _focus(env), "action": "close"}


def dispatch_gen(env: Env) -> dict:
    wh = _token(env.bag.get("so_ship_wh", ""))
    line = {"source_line_id": _bag_id(env, "so_ship_line"), "quantity": 1, "cWhCode": wh}
    return {"type": "dispatch", "id": _focus(env), "head": {"cWhCode": wh, "dDate": env.today}, "lines": [line]}


def invoice_gen(env: Env) -> dict:
    qty = _number(env.bag.get("inv_qty", ""), False)
    line = {"source_line_id": _bag_id(env, "inv_src_line"), "quantity": qty}
    return {"type": "sale_invoice", "id": _focus(env), "lines": [line]}


def sale_out_gen(env: Env) -> dict:
    return {"type": "sale_out", "id": _focus(env)}


def close_other(_env: Env) -> dict:
    return {"type": "other_in", "id": 1, "action": "close"}


def po_body(env: Env) -> dict:
    parts = _parts(env.bag.get("po_master", ""), 11)
    if parts[10] not in ("T", "U"):
        raise ValueError("主数据不完整")
    head = {
        "cVenCode": _plain(parts[0], 20),
        "cDepCode": _plain(parts[1], 12),
        "cPTCode": _plain(parts[3], 2),
        "cBusType": _plain(parts[4], 8),
        "cexch_name": _plain(parts[5], 8),
        "nflat": _number(parts[6], False),
        "iTaxRate": _number(parts[7], True),
        "dPODate": env.today,
        "cMemo": "CO桥回归",
    }
    if parts[2] != "":
        head["cPersonCode"] = _plain(parts[2], 20)
    price_key = "iTaxPrice" if parts[10] == "T" else "iUnitPrice"
    line = {
        "cInvCode": _plain(parts[8], 60),
        "iQuantity": 1,
        price_key: _number(parts[9], False),
        "iPerTaxRate": _number(parts[7], True),
    }
    return {"type": "purchase_order", "head": head, "lines": [line]}


def po_qty(env: Env) -> dict:
    line = {"op": "update", "line_id": _bag_id(env, "po_line"), "iQuantity": 2}
    return {"type": "purchase_order", "id": _focus(env), "lines": [line]}


def po_close_line(env: Env) -> dict:
    return _line_action(env, "purchase_order", "close", "po_one")


def po_open_line(env: Env) -> dict:
    return _line_action(env, "purchase_order", "open", "po_one")


def pin_gen(env: Env) -> dict:
    line = {"source_line_id": _bag_id(env, "po_recv_line"), "quantity": 1}
    head = {"cWhCode": _token(env.bag.get("po_recv_wh", ""))}
    return {"type": "purchase_in", "id": _focus(env), "head": head, "lines": [line]}


def transfer_body(env: Env) -> dict:
    out_wh, in_wh, inv, dep_o, dep_i = [_token(item) for item in _parts(env.bag.get("transfer_master", ""), 5)]
    head = {
        "cOWhCode": out_wh,
        "cIWhCode": in_wh,
        "cORdCode": td("stock.transfer_out_rd"),
        "cIRdCode": td("stock.transfer_in_rd"),
        "cODepCode": dep_o,
        "cIDepCode": dep_i,
        "dTVDate": env.today,
        "cMemo": "CO桥回归",
    }
    return {"type": "transfer", "head": head, "lines": [{"cInvCode": inv, "iTVQuantity": 1}]}


def transfer_qty(env: Env) -> dict:
    inv = _token(_parts(env.bag.get("transfer_master", ""), 5)[2])
    line = {"op": "update", "line_id": _bag_id(env, "tv_line"), "iTVQuantity": 2}
    added = {"op": "add", "cInvCode": inv, "iTVQuantity": 1}
    return {"type": "transfer", "id": _focus(env), "lines": [line, added]}


def other_in_edit(env: Env) -> dict:
    _wh, _rd, _dep, inv = _parts(env.bag.get("other_in_master", ""), 4)
    inv_code = _token(inv)
    changed = {
        "op": "update",
        "line_id": _bag_id(env, "oi_line"),
        "iQuantity": _plus(env, "oi_qty"),
        "iUnitCost": 1.5,
    }
    added = {"op": "add", "cInvCode": inv_code, "iQuantity": 1, "iUnitCost": 1.5}
    return {"type": "other_in", "id": _focus(env), "lines": [changed, added]}


FLOW_BUILDERS = {
    "sale_update": sale_update,
    "sale_drop": sale_drop,
    "sale_touch": sale_touch,
    "sale_close_line": sale_close_line,
    "sale_open_line": sale_open_line,
    "sale_close_made": sale_close_made,
    "dispatch_gen": dispatch_gen,
    "invoice_gen": invoice_gen,
    "sale_out_gen": sale_out_gen,
    "close_other": close_other,
    "purchase_order": po_body,
    "po_qty": po_qty,
    "po_close_line": po_close_line,
    "po_open_line": po_open_line,
    "pin_gen": pin_gen,
    "transfer": transfer_body,
    "transfer_qty": transfer_qty,
    "other_in_edit": other_in_edit,
}
