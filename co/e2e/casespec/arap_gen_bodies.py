"""新建和生单的正文。主数据和数量只接受挑单里的安全文本。

应收应付单据的表头、表体用 U8 列名（桥按类型的允许列表校验）。收付款单表体金额是 iAmt、科目是 cKm；
应收/应付单表体是 Ap_Vouchs 的 cCode、iAmount。金额固定 100，回读断言按这个数比对。
"""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal

from co.e2e.casespec.assertutil import as_num
from co.e2e.casespec.env import Env
from co.e2e.testdata import MATERIAL_OUT_RD, PRODUCT_IN_RD
from co.e2e.testdata import value as td

AMOUNT = 100
AMOUNT_TEXT = "100.00"
_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_NUM = re.compile(r"^[0-9]+(\.[0-9]+)?\Z")
# 与挑单 SQL 的 NOT LIKE '%[^0-9A-Za-z._-]%' 同一字符集。
_TOKEN = re.compile(r"^[0-9A-Za-z._-]{1,30}\Z")
_INT_MAX = 2147483647
_MEMO = "CO桥回归"


def _parts(text: str, count: int) -> list[str]:
    parts = [item.strip() for item in (text or "").split("|")]
    if len(parts) != count or any(_TOKEN.fullmatch(item) is None for item in parts):
        raise ValueError("主数据不完整")
    return parts


def _as_id(raw: str, label: str) -> int:
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError(label)
    return int(raw)


def _focus(env: Env) -> int:
    return _as_id(env.focus, "单据 id 不合法")


def _qty(raw: str) -> int | float:
    text = (raw or "").strip()
    if _NUM.fullmatch(text) is None or Decimal(text) <= 0:
        raise ValueError("数量不合法")
    return as_num(text)


def ar_receipt_body(env: Env) -> dict:
    cus, dep, person, ctrl, bank, settle, _bill = _parts(env.bag.get("ar_master", ""), 7)
    head = {
        "cDwCode": cus,
        "cDeptCode": dep,
        "cPerson": person,
        "cSSCode": settle,
        "cCode": bank,
        "dVouchDate": env.today,
        "cDigest": _MEMO,
    }
    return {"type": "ar_receipt", "head": head, "lines": [{"iType": 0, "cKm": ctrl, "iAmt": AMOUNT}]}


def ap_payment_body(env: Env) -> dict:
    ven, ctrl, bank, settle, _bill = _parts(env.bag.get("ap_master", ""), 5)
    head = {"cDwCode": ven, "cSSCode": settle, "cCode": bank, "dVouchDate": env.today, "cDigest": _MEMO}
    return {"type": "ap_payment", "head": head, "lines": [{"iType": 0, "cKm": ctrl, "iAmt": AMOUNT}]}


def ar_bill_body(env: Env) -> dict:
    cus, dep, person, ctrl, _bank, _settle, bill = _parts(env.bag.get("ar_master", ""), 7)
    head = {
        "cDwCode": cus,
        "cDeptCode": dep,
        "cPerson": person,
        "cCode": ctrl,
        "dVouchDate": env.today,
        "cDigest": _MEMO,
    }
    return {"type": "ar_bill", "head": head, "lines": [{"cCode": bill, "iAmount": AMOUNT}]}


def ap_bill_body(env: Env) -> dict:
    ven, ctrl, _bank, _settle, bill = _parts(env.bag.get("ap_master", ""), 5)
    head = {"cDwCode": ven, "cCode": ctrl, "dVouchDate": env.today, "cDigest": _MEMO}
    return {"type": "ap_bill", "head": head, "lines": [{"cCode": bill, "iAmount": AMOUNT}]}


def material_out_nord(env: Env) -> dict:
    # 不带收发类别：桥应 400 field head.crdcode，进 U8 之前就拒绝。
    wh = _parts(env.bag.get("mo_alloc_wh", ""), 1)[0]
    line = {"source_line_id": _as_id(env.bag.get("mo_alloc_line", ""), "子件 id 不合法"), "quantity": 1}
    head = {"cWhCode": wh, "dDate": env.today, "cMemo": _MEMO}
    return {"type": "material_out", "id": _focus(env), "head": head, "lines": [line]}


def material_out_gen(env: Env) -> dict:
    # 领 1 个主计量单位；回读断言按 IssQty +1 比对。收发类别取测试数据 stock.material_out_rd（桥没有缺省值）。
    body = material_out_nord(env)
    body["head"]["cRdCode"] = td(MATERIAL_OUT_RD)
    return body


def product_in_gen(env: Env) -> dict:
    check = _focus(env)
    wh = _parts(env.bag.get("qm_pin_wh", ""), 1)[0]
    line = {"source_line_id": check, "quantity": _qty(env.bag.get("qm_pin_qty", ""))}
    head = {"cWhCode": wh, "cRdCode": td(PRODUCT_IN_RD), "dDate": env.today, "cMemo": _MEMO}
    return {"type": "product_in", "id": check, "head": head, "lines": [line]}


def _pinv(env: Env, head: dict, source: str = "") -> dict:
    line = {"source_line_id": _as_id(env.bag.get("pinv_line", ""), "入库单行 id 不合法"), "quantity": 1}
    body = {"type": "purchase_invoice", "id": _focus(env), "head": head, "lines": [line]}
    if source:
        body["source_type"] = source
    return body


def purchase_invoice_gen(env: Env) -> dict:
    # 发票号每次不同，记进 bag，供回读和重号用例使用。开 1 个，回读断言按 +1 比对。
    code = "EXE2E" + datetime.now().strftime("%H%M%S")
    env.bag["pinv_code"] = code
    return _pinv(env, {"cPBVCode": code, "cPBVBillType": "01"})


def purchase_invoice_dup(env: Env) -> dict:
    code = _parts(env.bag.get("pinv_code", ""), 1)[0]
    return _pinv(env, {"cPBVCode": code, "cPBVBillType": "01"})


def purchase_invoice_nocode(env: Env) -> dict:
    return _pinv(env, {"cPBVBillType": "01"})


def purchase_invoice_badsrc(env: Env) -> dict:
    return _pinv(env, {"cPBVCode": "EXE2EBAD", "cPBVBillType": "01"}, "dispatch")


def purchase_in_qm_gen(env: Env) -> dict:
    check = _focus(env)
    wh = _parts(env.bag.get("qmin_wh", ""), 1)[0]
    line = {"source_line_id": check, "quantity": 1}
    body = {"type": "purchase_in", "id": check, "source_type": "qm_incoming_check"}
    body.update({"head": {"cWhCode": wh}, "lines": [line]})
    return body


def _arrival(env: Env, qty: int | float) -> dict:
    line = {"source_line_id": _as_id(env.bag.get("arr_line", ""), "订单行 id 不合法"), "quantity": qty}
    head = {"cWhCode": _parts(env.bag.get("arr_wh", ""), 1)[0]}
    return {"type": "arrival", "id": _focus(env), "head": head, "lines": [line]}


def arrival_gen(env: Env) -> dict:
    # 到货 1 个；回读断言按 iArrQTY、fPoArrQuantity 各 +1 比对。
    return _arrival(env, 1)


def arrival_over(env: Env) -> dict:
    # 比剩余可到货多 1（挑单时算好），桥应 409 拒绝。
    return _arrival(env, _qty(env.bag.get("arr_over", "")))


CORE_BUILDERS = {
    "ar_receipt": ar_receipt_body,
    "ap_payment": ap_payment_body,
    "ar_bill": ar_bill_body,
    "ap_bill": ap_bill_body,
    "material_out_gen": material_out_gen,
    "material_out_nord": material_out_nord,
    "product_in_gen": product_in_gen,
    "purchase_invoice_gen": purchase_invoice_gen,
    "purchase_invoice_dup": purchase_invoice_dup,
    "purchase_invoice_nocode": purchase_invoice_nocode,
    "purchase_invoice_badsrc": purchase_invoice_badsrc,
    "purchase_in_qm_gen": purchase_in_qm_gen,
    "arrival_gen": arrival_gen,
    "arrival_over": arrival_over,
}
