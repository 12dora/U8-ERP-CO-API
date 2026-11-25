"""新建和生单的正文：销售退货、采购退货、不良品处理单入库、请购单、无来源采购入库。

来源单据和明细 id 来自挑单；部门、存货等站点档案来自测试数据（co.e2e.testdata），缺了用例记 SKIP。
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

from co.e2e.casespec.assertutil import as_num
from co.e2e.casespec.env import Env
from co.e2e.testdata import PRODUCT_IN_RD
from co.e2e.testdata import value as td

MEMO = "CO桥回归"
MEMO_UPD = "CO桥回归改"
REQ_DATA = ("purchase_requisition.dept", "purchase_requisition.inventory")
SR_QTY = 2
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_NUM = re.compile(r"^[0-9]+(\.[0-9]+)?\Z")
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


def _qty(raw: str) -> int | float:
    text = (raw or "").strip()
    if _NUM.fullmatch(text) is None or Decimal(text) <= 0:
        raise ValueError("数量不合法")
    return as_num(text)


def sale_return_gen(env: Env) -> dict:
    # 参照蓝字发货行退 2 个，按未开票退货（回写原行 fretqtywkp）。
    line = {"source_line_id": _as_id(env.bag.get("sr_line", ""), "发货行 id 不合法"), "quantity": SR_QTY}
    return {"type": "sale_return", "id": _focus(env), "head": {"invoiced": False}, "lines": [line]}


def purchase_return_gen(env: Env) -> dict:
    # 参照蓝字到货行退 1 个（来源缺省是到货单）。
    line = {"source_line_id": _as_id(env.bag.get("pr_line", ""), "到货行 id 不合法"), "quantity": 1}
    return {"type": "purchase_return", "id": _focus(env), "head": {"dDate": env.today}, "lines": [line]}


def _reject(env: Env, qty_key: str) -> dict:
    line = {"source_line_id": _as_id(env.bag.get("qmrej_line", ""), "处理单行 id 不合法"), "quantity": _qty(env.bag.get(qty_key, ""))}
    head = {"cWhCode": _token(env.bag.get("qmrej_wh", "")), "dDate": env.today, "cMemo": MEMO}
    head["cRdCode"] = td(PRODUCT_IN_RD)
    body = {"type": "product_in", "source_type": "qm_product_reject", "id": _focus(env)}
    body.update({"head": head, "lines": [line]})
    return body


def product_in_reject_gen(env: Env) -> dict:
    # U8 只接受一次入库全部处理后数量。
    return _reject(env, "qmrej_qty")


def product_in_reject_part(env: Env) -> dict:
    # 只入一部分，桥应 409「须一次入库全部」。
    return _reject(env, "qmrej_part")


def _later(day: str, days: int) -> str:
    return (date.fromisoformat(day) + timedelta(days=days)).isoformat()


def purchase_requisition_new(env: Env) -> dict:
    head = {"ddate": env.today, "cdepcode": td("purchase_requisition.dept"), "cmemo": MEMO}
    line = {"cinvcode": td("purchase_requisition.inventory"), "fquantity": 5, "drequirdate": _later(env.today, 14)}
    return {"type": "purchase_requisition", "head": head, "lines": [line]}


def purchase_requisition_upd(env: Env) -> dict:
    return {"type": "purchase_requisition", "id": _focus(env), "head": {"cmemo": MEMO_UPD}}


def purchase_in_nosrc(env: Env) -> dict:
    # 无来源采购入库；只用于「普通业务必有订单」打开时的拒绝用例，不给单价。
    head = {
        "cwhcode": _token(env.bag.get("pinsrc_wh", "")),
        "cvencode": _token(env.bag.get("pinsrc_ven", "")),
        "cdepcode": _token(env.bag.get("pinsrc_dep", "")),
        "ddate": env.today,
    }
    line = {"cinvcode": _token(env.bag.get("pinsrc_inv", "")), "iquantity": 1}
    return {"type": "purchase_in", "head": head, "lines": [line]}


CUSTOM_BUILDERS = {
    "sale_return_gen": sale_return_gen,
    "purchase_return_gen": purchase_return_gen,
    "product_in_reject_gen": product_in_reject_gen,
    "product_in_reject_part": product_in_reject_part,
    "purchase_requisition_new": purchase_requisition_new,
    "purchase_requisition_upd": purchase_requisition_upd,
    "purchase_in_nosrc": purchase_in_nosrc,
}
