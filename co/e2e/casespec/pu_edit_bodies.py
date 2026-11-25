"""采购类修改的正文：到货单、采购退货单、采购发票各先生单 2 个，再用 vouchers/update 改成 1 个。

来源单据和明细 id 来自挑单（pu_edit_sql），新单的行 id 来自生单后的回读（bag 的 edit?_nline）。
"""

from __future__ import annotations

import re
from datetime import datetime

from co.e2e.casespec.env import Env

GEN_QTY = 2
UPD_QTY = 1
MEMO_UPD = "CO桥回归改"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
# 与挑单 SQL 的 NOT LIKE '%[^0-9A-Za-z._-]%' 同一字符集。
_TOKEN = re.compile(r"^[0-9A-Za-z._-]{1,30}\Z")
_INT_MAX = 2147483647


def _as_id(raw: str, label: str) -> int:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError(label)
    return int(raw)


def _focus(env: Env) -> int:
    return _as_id(env.focus, "单据 id 不合法")


def _token(raw: str) -> str:
    text = (raw or "").strip()
    if _TOKEN.fullmatch(text) is None:
        raise ValueError("主数据不完整")
    return text


def _gen(env: Env, kind: str, line_key: str, head: dict) -> dict:
    line = {"source_line_id": _as_id(env.bag.get(line_key, ""), "来源行 id 不合法"), "quantity": GEN_QTY}
    return {"type": kind, "id": _focus(env), "head": head, "lines": [line]}


def _upd(env: Env, kind: str, nline_key: str, line_fields: dict, head: dict) -> dict:
    line = {"op": "update", "line_id": _as_id(env.bag.get(nline_key, ""), "新单行 id 不合法")}
    line.update(line_fields)
    return {"type": kind, "id": _focus(env), "head": head, "lines": [line]}


def arr_gen(env: Env) -> dict:
    # 参照采购订单行到货 2 个。
    return _gen(env, "arrival", "arr_mod_line", {"cWhCode": _token(env.bag.get("arr_mod_wh", ""))})


def arr_upd(env: Env) -> dict:
    # 数量 2 → 1，同时改表头备注和表体备注。
    fields = {"iquantity": UPD_QTY, "cbmemo": MEMO_UPD}
    return _upd(env, "arrival", "arr_mod_nline", fields, {"cmemo": MEMO_UPD})


def ret_gen(env: Env) -> dict:
    # 参照蓝字到货行退 2 个（来源缺省是到货单）。
    return _gen(env, "purchase_return", "pret_mod_line", {"dDate": env.today})


def ret_upd(env: Env) -> dict:
    # 退货数量（正数）2 → 1。
    return _upd(env, "purchase_return", "pret_mod_nline", {"iquantity": UPD_QTY}, {"cmemo": MEMO_UPD})


def inv_gen(env: Env) -> dict:
    # 发票号每次不同（专用发票 01），开 2 个。
    code = "EXP6" + datetime.now().strftime("%H%M%S")
    return _gen(env, "purchase_invoice", "pinv_mod_line", {"cPBVCode": code, "cPBVBillType": "01"})


def inv_upd(env: Env) -> dict:
    fields = {"ipbvquantity": UPD_QTY, "cbmemo": MEMO_UPD}
    return _upd(env, "purchase_invoice", "pinv_mod_nline", fields, {"cpbvmemo": MEMO_UPD})


PU_MOD_BUILDERS = {
    "pu_mod_arr_gen": arr_gen,
    "pu_mod_arr_upd": arr_upd,
    "pu_mod_ret_gen": ret_gen,
    "pu_mod_ret_upd": ret_upd,
    "pu_mod_inv_gen": inv_gen,
    "pu_mod_inv_upd": inv_upd,
}
