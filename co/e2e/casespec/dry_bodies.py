"""写预演用例的正文构造器（DRY_SUITE_BUILDERS 并入 wiring 汇总，steps._make 认得）。纯函数，不引用 steps。

应收 / 应付制单用本用例新建并审核的应收单 / 应付单（bag 的 dry_arb / dry_apb）；取消制单的外部业务号取自制单响应
（ap_writeoff_voucher_run 记进 dry_apb_pz，只留数字）。应付自动核销的挑单见 dry_sql.DRY_AP_PACKS。
"""

from __future__ import annotations

import re

from co.e2e.casespec.env import Env
from co.e2e.casespec.writeoff_auto import ask_body

_ID = re.compile(r"^[1-9][0-9]{0,9}\Z")
_DIGITS = re.compile(r"^[0-9]{1,20}\Z")
_CODE = re.compile(r"^[0-9A-Za-z._-]{1,12}\Z")
_INT_MAX = 2147483647
PZ_KEY = "dry_apb_pz"


def bag_id(env: Env, key: str) -> int:
    raw = env.bag.get(key, "")
    if _ID.fullmatch(raw or "") is None or int(raw) > _INT_MAX:
        raise ValueError("缺 " + key)
    return int(raw)


def _digits(env: Env, key: str) -> str:
    raw = env.bag.get(key, "")
    if _DIGITS.fullmatch(raw or "") is None:
        raise ValueError("缺 " + key)
    return raw


def arb_voucher(env: Env) -> dict:
    return {"flag": "AR", "type": "ar_bill", "id": bag_id(env, "dry_arb")}


def apb_voucher(env: Env) -> dict:
    return {"flag": "AP", "type": "ap_bill", "id": bag_id(env, "dry_apb")}


def apb_drop(env: Env) -> dict:
    return {"flag": "AP", "pz_id": "AP" + _digits(env, PZ_KEY)}


def ap_auto(env: Env, dry_run: bool) -> dict:
    """应付自动核销：限定挑中的付款单行和采购发票；试算不设上限，执行上限 1.00。"""
    partner = env.bag.get("dry_ap_dw", "")
    if _CODE.fullmatch(partner or "") is None:
        raise ValueError("挑单缺 dry_ap_dw")
    body = {
        "flag": "AP",
        "partner": partner,
        "receipt": {"type": "ap_payment", "id": bag_id(env, "dry_ap_pc"), "line_id": bag_id(env, "dry_ap_pcl")},
        "targets": [{"type": "purchase_invoice", "id": bag_id(env, "dry_ap_pinv")}],
        "dry_run": dry_run,
    }
    if not dry_run:
        body["max_amount"] = 1
    return body


def ap_auto_plan(env: Env) -> dict:
    return ap_auto(env, True)


def ap_auto_run(env: Env) -> dict:
    return ap_auto(env, False)


def ap_auto_cancel(env: Env) -> dict:
    return {"flag": "AP", "cancel_no": "HXAP" + _digits(env, "dry_ap_cno")}


def ar_auto_plan(env: Env) -> dict:
    # 应收自动核销试算：同 ar-writeoff-auto 的挑单和正文。
    return ask_body(env, True)


def probe_other_in(_env: Env) -> dict:
    # 拒绝用例的正文：桥在登录前就拒绝（预演带幂等键、未知字段），内容不必能存盘。
    return {"type": "other_in", "head": {"cMemo": "CO预演"}, "lines": [{"iQuantity": 1}]}


def probe_no_lines(_env: Env) -> dict:
    return {"type": "other_in", "head": {"cMemo": "CO预演"}}


def hx_cancel(env: Env) -> dict:
    # dry-ar-writeoff 自己的核销号（ap_writeoff_voucher_run.wo_exec 记进 dry_hx_cno，不和 ar-writeoff 的 hx_cno 混用）。
    return {"flag": "AR", "cancel_no": "HXAR" + _digits(env, "dry_hx_cno")}


DRY_SUITE_BUILDERS = {
    "dry_probe_other_in": probe_other_in,
    "dry_probe_no_lines": probe_no_lines,
    "dry_hx_cancel": hx_cancel,
    "dry_arb_voucher": arb_voucher,
    "dry_apb_voucher": apb_voucher,
    "dry_apb_drop": apb_drop,
    "dry_ap_plan": ap_auto_plan,
    "dry_ap_run": ap_auto_run,
    "dry_ap_cancel": ap_auto_cancel,
    "dry_hxa_plan": ar_auto_plan,
}
