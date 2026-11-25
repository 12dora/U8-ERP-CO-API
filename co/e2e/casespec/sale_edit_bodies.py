"""销售类修改的正文：发货单、退货单、销售发票各先生单 2 个，再用 vouchers/update 改成 1 个。

来源单据和明细 id 来自挑单（sale_edit_sql），新单的行 id 来自生单后的回读（bag 的 s6?_nline）。
"""

from __future__ import annotations

import re

from co.e2e.casespec.env import Env
from co.e2e.casespec.sale_edit_sql import MEMO

GEN_QTY = 2
UPD_QTY = 1
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


def _gen(env: Env, kind: str, line: dict, head: dict | None) -> dict:
    body = {"type": kind, "id": _focus(env), "lines": [line]}
    if head:
        body["head"] = head
    return body


def _src(env: Env, key: str) -> dict:
    return {"source_line_id": _as_id(env.bag.get(key, ""), "来源行 id 不合法"), "quantity": GEN_QTY}


def _upd(env: Env, kind: str, nline_key: str, qty: int, line_memo: bool = True) -> dict:
    line: dict = {"op": "update", "line_id": _as_id(env.bag.get(nline_key, ""), "新单行 id 不合法"), "iQuantity": qty}
    if line_memo:
        line["cMemo"] = MEMO
    return {"type": kind, "id": _focus(env), "head": {"cMemo": MEMO}, "lines": [line]}


def disp_gen(env: Env) -> dict:
    # 参照销售订单行发货 2 个。
    wh = _token(env.bag.get("disp_mod_wh", ""))
    line = dict(_src(env, "disp_mod_line"), cWhCode=wh)
    return _gen(env, "dispatch", line, {"dDate": env.today})


def disp_upd(env: Env) -> dict:
    # 数量 2 → 1，同时改表头备注和表体备注。
    return _upd(env, "dispatch", "disp_mod_nline", UPD_QTY)


def disp_up(env: Env) -> dict:
    # 数量 2 → 3：修改只能减少数量，桥 400。
    return _upd(env, "dispatch", "disp_mod_nline", GEN_QTY + 1, False)


def ret_gen(env: Env) -> dict:
    # 参照蓝字发货行退 2 个，未开票退货。
    return _gen(env, "sale_return", _src(env, "sret_mod_line"), {"invoiced": False})


def ret_upd(env: Env) -> dict:
    # 退货数量（正数）2 → 1。
    return _upd(env, "sale_return", "sret_mod_nline", UPD_QTY)


def inv_gen(env: Env) -> dict:
    # 参照发货行开专用发票 2 个。
    return _gen(env, "sale_invoice", _src(env, "sinv_mod_line"), None)


def inv_upd(env: Env) -> dict:
    return _upd(env, "sale_invoice", "sinv_mod_nline", UPD_QTY)


SA_MOD_BUILDERS = {
    "sa_mod_disp_gen": disp_gen,
    "sa_mod_disp_upd": disp_upd,
    "sa_mod_disp_up": disp_up,
    "sa_mod_ret_gen": ret_gen,
    "sa_mod_ret_upd": ret_upd,
    "sa_mod_inv_gen": inv_gen,
    "sa_mod_inv_upd": inv_upd,
}
