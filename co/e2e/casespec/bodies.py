"""新建单据的正文。主数据来自挑单结果，不把调用方原文拼进 SQL。"""

from __future__ import annotations

from datetime import date, timedelta

from co.e2e.casespec.assertutil import as_num, multiple_qty
from co.e2e.casespec.env import Env


def _parts(text: str, count: int) -> list[str]:
    parts = (text or "").split("|")
    if len(parts) != count:
        raise ValueError("主数据不完整")
    return [item.strip() for item in parts]


def _filled(parts: list[str]) -> None:
    if any(item == "" for item in parts):
        raise ValueError("主数据不完整")


def other_in_body(env: Env) -> dict:
    wh, rd, dep, inv = _parts(env.bag.get("other_in_master", ""), 4)
    _filled([wh, rd, dep, inv])
    return {
        "type": "other_in",
        "head": {
            "cWhCode": wh,
            "cRdCode": rd,
            "cDepCode": dep,
            "dDate": env.today,
            "cMemo": "CO桥回归",
        },
        "lines": [{"cInvCode": inv, "iQuantity": 2, "iUnitCost": 1.5}],
    }


def other_out_body(env: Env) -> dict:
    wh, rd, dep, inv = _parts(env.bag.get("other_out_master", ""), 4)
    _filled([wh, rd, dep, inv])
    return {
        "type": "other_out",
        "head": {"cWhCode": wh, "cRdCode": rd, "cDepCode": dep, "dDate": env.today, "cMemo": "CO桥回归"},
        "lines": [{"cInvCode": inv, "iQuantity": 2}],
    }


def _sale_line(parts: list[str], today: str) -> dict:
    # 模板订单的预发货日期早于今天时 U8 会拒绝（预发货日期不能小于订单日期），统一用今天之后三天。
    inv, rate, price, tax, _pre = parts[4:]
    return {
        "cInvCode": inv,
        "iQuantity": as_num(multiple_qty(rate)),
        "iTaxUnitPrice": as_num(price or "1"),
        "iTaxRate": as_num(tax or "13"),
        "dPreDate": (date.fromisoformat(today) + timedelta(days=3)).isoformat(),
    }


def sale_body(env: Env) -> dict:
    parts = _parts(env.bag.get("sale_master", ""), 9)
    _filled(parts[:5])
    head = {
        "cCusCode": parts[0],
        "cSTCode": parts[1],
        "cDepCode": parts[2],
        "cPersonCode": parts[3],
        "cBusType": "普通销售",
        "cexch_name": "人民币",
        "iExchRate": 1,
        "iTaxRate": 13,
        "dDate": env.today,
    }
    return {"type": "sale_order", "head": head, "lines": [_sale_line(parts, env.today)]}


def protected_body(env: Env) -> dict:
    return {
        "type": "other_in",
        "head": {
            "cWhCode": "01",
            "cRdCode": "0101",
            "cDepCode": "01",
            "dDate": env.today,
            "cMaker": "回归",
        },
        "lines": [{"cInvCode": "01", "iQuantity": 1, "iUnitCost": 1}],
    }


BUILDERS = {
    "other_in": other_in_body,
    "other_out": other_out_body,
    "sale_order": sale_body,
    "protected": protected_body,
}
