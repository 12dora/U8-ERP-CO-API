"""库存单据修改用例的正文：生单 2 个，修改时减到 1 个并写表头备注和行备注。

主数据只接受挑单里的安全文本，id 只接受正整数。
"""

from __future__ import annotations

import re

from co.e2e.casespec.env import Env
from co.e2e.casespec.stock_gen_edit_sql import MEMO
from co.e2e.testdata import MATERIAL_OUT_RD, PRODUCT_IN_RD
from co.e2e.testdata import value as td

GEN_QTY = 2
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_TOKEN = re.compile(r"^[0-9A-Za-z._-]{1,30}\Z")
_INT_MAX = 2147483647


def _id(raw: str, label: str = "单据 id 不合法") -> int:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError(label)
    return int(raw)


def _wh(env: Env, key: str) -> str:
    text = (env.bag.get(key) or "").strip()
    if _TOKEN.fullmatch(text) is None:
        raise ValueError("主数据不完整")
    return text


def _focus(env: Env) -> int:
    return _id(env.focus)


# ---- 生单 ----


def se_disp_gen(env: Env) -> dict:
    wh = _wh(env, "se_so_wh")
    line = {"source_line_id": _id(env.bag.get("se_so_line", ""), "订单行 id 不合法"), "quantity": GEN_QTY, "cWhCode": wh}
    return {"type": "dispatch", "id": _focus(env), "head": {"cWhCode": wh, "dDate": env.today}, "lines": [line]}


def se_out_gen(env: Env) -> dict:
    return {"type": "sale_out", "id": _focus(env)}


def se_pin_po_gen(env: Env) -> dict:
    line = {"source_line_id": _id(env.bag.get("se_po_line", ""), "订单行 id 不合法"), "quantity": GEN_QTY}
    return {"type": "purchase_in", "id": _focus(env), "head": {"cWhCode": _wh(env, "se_po_wh")}, "lines": [line]}


def se_pin_qm_gen(env: Env) -> dict:
    check = _focus(env)
    body = {"type": "purchase_in", "id": check, "source_type": "qm_incoming_check"}
    body.update({"head": {"cWhCode": _wh(env, "se_qm_wh")}, "lines": [{"source_line_id": check, "quantity": GEN_QTY}]})
    return body


def se_pq_gen(env: Env) -> dict:
    check = _focus(env)
    head = {"cWhCode": _wh(env, "se_pq_wh"), "cRdCode": td(PRODUCT_IN_RD), "dDate": env.today}
    return {"type": "product_in", "id": check, "head": head, "lines": [{"source_line_id": check, "quantity": GEN_QTY}]}


def se_mo_gen(env: Env) -> dict:
    line = {"source_line_id": _id(env.bag.get("se_mo_line", ""), "子件 id 不合法"), "quantity": GEN_QTY}
    head = {"cWhCode": _wh(env, "se_mo_wh"), "cRdCode": td(MATERIAL_OUT_RD), "dDate": env.today}
    return {"type": "material_out", "id": _focus(env), "head": head, "lines": [line]}


# ---- 修改（env.focus 是新单据 id，行 id 在 bag）----


def _upd(env: Env, kind: str, line_key: str, qty: int) -> dict:
    line = {"op": "update", "line_id": _id(env.bag.get(line_key, ""), "明细行 id 不合法"), "iQuantity": qty, "cbMemo": MEMO}
    return {"type": kind, "id": _focus(env), "head": {"cMemo": MEMO}, "lines": [line]}


def se_out_upd(env: Env) -> dict:
    return _upd(env, "sale_out", "se_out_line", GEN_QTY - 1)


def se_pin_upd(env: Env) -> dict:
    return _upd(env, "purchase_in", "se_pin_line", GEN_QTY - 1)


def se_pinq_upd(env: Env) -> dict:
    return _upd(env, "purchase_in", "se_pinq_line", GEN_QTY - 1)


def se_pq_upd(env: Env) -> dict:
    return _upd(env, "product_in", "se_pq_rd_line", GEN_QTY - 1)


def se_mo_upd(env: Env) -> dict:
    return _upd(env, "material_out", "se_mo_rd_line", GEN_QTY - 1)


def se_mo_up(env: Env) -> dict:
    # 加数量：400「修改只能减少数量」。
    return _upd(env, "material_out", "se_mo_rd_line", GEN_QTY + 1)


def se_mo_add(env: Env) -> dict:
    # 新增行：400「该单据类型修改不能新增行」。
    line = {"op": "add", "iQuantity": 1}
    return {"type": "material_out", "id": _focus(env), "lines": [line]}


SE_BUILDERS = {
    "se_disp_gen": se_disp_gen,
    "se_out_gen": se_out_gen,
    "se_pin_po_gen": se_pin_po_gen,
    "se_pin_qm_gen": se_pin_qm_gen,
    "se_pq_gen": se_pq_gen,
    "se_mo_gen": se_mo_gen,
    "se_out_upd": se_out_upd,
    "se_pin_upd": se_pin_upd,
    "se_pinq_upd": se_pinq_upd,
    "se_pq_upd": se_pq_upd,
    "se_mo_upd": se_mo_upd,
    "se_mo_up": se_mo_up,
    "se_mo_add": se_mo_add,
}
