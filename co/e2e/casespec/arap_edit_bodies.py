"""应收应付修改的正文：借新建正文把金额改成 10，修改时把这一行改成 7 并改摘要。

明细 id 由回读先存进 bag（键 <类型>_edit_line）；单据 id 取本用例新建的那张。
"""

from __future__ import annotations

import re

from co.e2e.casespec.arap_gen_bodies import ap_bill_body, ap_payment_body, ar_bill_body, ar_receipt_body
from co.e2e.casespec.env import Env

NEW_AMOUNT = 10
UPD_AMOUNT = 7
UPD_TEXT = "7.00"
# 回读 SQL 按这个串比对，只用 ASCII。
DIGEST_UPD = "arap_mod"
KINDS = ("ar_receipt", "ap_payment", "ar_bill", "ap_bill")
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_INT_MAX = 2147483647
_NEW = {
    "ar_receipt": ar_receipt_body,
    "ap_payment": ap_payment_body,
    "ar_bill": ar_bill_body,
    "ap_bill": ap_bill_body,
}


def amount_key(kind: str) -> str:
    # 收付款单表体金额 iAmt，应收/应付单表体 iAmount。
    return "iAmt" if kind in ("ar_receipt", "ap_payment") else "iAmount"


def line_key(kind: str) -> str:
    return kind + "_edit_line"


def _as_id(raw: str, label: str) -> int:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError(label)
    return int(raw)


def _new(kind: str, env: Env) -> dict:
    body = _NEW[kind](env)
    body["lines"][0][amount_key(kind)] = NEW_AMOUNT
    return body


def _upd(kind: str, env: Env) -> dict:
    line = {
        "op": "update",
        "line_id": _as_id(env.bag.get(line_key(kind), ""), "明细 id 不合法"),
        amount_key(kind): UPD_AMOUNT,
    }
    doc = _as_id(env.focus, "单据 id 不合法")
    return {"type": kind, "id": doc, "head": {"cDigest": DIGEST_UPD}, "lines": [line]}


def _builders() -> dict:
    found = {}
    for kind in KINDS:
        found["edit_" + kind + "_new"] = lambda env, k=kind: _new(k, env)
        found["edit_" + kind + "_upd"] = lambda env, k=kind: _upd(k, env)
    return found


EDIT_ARAP_BUILDERS = _builders()
