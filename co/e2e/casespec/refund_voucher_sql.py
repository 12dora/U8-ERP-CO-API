"""退款单制单用例（refund_voucher）的回读 SQL。

只读；表名、cFlag、cVouchType 来自常量 REFUND（arap_refund_sql），单据 id 只接受正整数（safe_id），单号、科目、
往来单位编码只接受 [0-9A-Za-z._-]（safe_lot），外部业务号只接受 AR / AP 加数字，再拼进 SQL。每条 SQL 输出一行 ROW:<值>。
"""

from __future__ import annotations

import re

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.arap_refund_sql import REFUND

_PZ = re.compile(r"^(AR|AP)[0-9]{1,20}\Z")


def safe_pz(raw: str) -> str:
    if _PZ.fullmatch(raw or "") is None:
        raise ValueError("外部业务号不合法")
    return raw


def _spec(kind: str) -> tuple[str, str, str, str, str]:
    if kind not in REFUND:
        raise ValueError("类型只能是 ap_refund 或 ar_refund")
    return REFUND[kind]


def _one(expr: str, frm: str = "") -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v" + ("\n" + frm if frm else "") + ";\n"


def _num(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,2), ISNULL({expr}, 0)))"


def _cnt(expr: str) -> str:
    return f"CONVERT(varchar(12), ISNULL({expr}, 0))"


def code_sql(doc: str) -> str:
    """退款单号（Ap_CloseBill.cVouchID）；没有为空。"""
    return _one(f"ISNULL((SELECT LTRIM(RTRIM(b.cVouchID)) FROM dbo.Ap_CloseBill AS b WHERE b.iID = {safe_id(doc)}), N'')")


def gl_sql(kind: str, pz: str, code: str, accounts: tuple[str, str], partner: str) -> str:
    """凭证行：行数|往来科目借方|往来科目贷方|结算科目借方|结算科目贷方|不带本单来源（制单系统、单据类型 48 / 49、单号）的行数|
    往来科目行里辅助项不是本单往来单位的行数|凭证张数（期间 + 类别 + 号）。accounts = (往来科目, 结算科目)。"""
    flag, vouch, _detail, _card, _twin = _spec(kind)
    ctrl, bank = (safe_lot(one) for one in accounts)
    aux = "g.csup_id" if flag == "AP" else "g.ccus_id"
    own = (
        f"ISNULL(g.coutsysname, N'') = N'{flag}' AND ISNULL(g.coutbillsign, N'') = N'{vouch}'"
        f" AND g.coutid = N'{safe_lot(code)}'"
    )
    is_ctrl = f"g.ccode = N'{ctrl}'"
    is_bank = f"g.ccode = N'{bank}'"
    terms = [
        _cnt("COUNT(*)"),
        _num(f"SUM(CASE WHEN {is_ctrl} THEN g.md ELSE 0 END)"),
        _num(f"SUM(CASE WHEN {is_ctrl} THEN g.mc ELSE 0 END)"),
        _num(f"SUM(CASE WHEN {is_bank} THEN g.md ELSE 0 END)"),
        _num(f"SUM(CASE WHEN {is_bank} THEN g.mc ELSE 0 END)"),
        _cnt(f"SUM(CASE WHEN {own} THEN 0 ELSE 1 END)"),
        _cnt(f"SUM(CASE WHEN {is_ctrl} AND ISNULL({aux}, N'') <> N'{safe_lot(partner)}' THEN 1 ELSE 0 END)"),
        _cnt("COUNT(DISTINCT CONVERT(varchar(4), g.iperiod) + g.csign + CONVERT(varchar(12), g.ino_id))"),
    ]
    expr = "\n  + N'|' + ".join(terms)
    return _one(expr, f"FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{safe_pz(pz)}'")


def marks_sql(kind: str, doc: str, code: str, pz: str) -> str:
    """回写：表头 cPzID 是本外部业务号（1 / 0）|本单原始往来明细里 cPZid 是它的行数|本单原始往来明细行数。"""
    flag, vouch, detail, _card, _twin = _spec(kind)
    key = safe_pz(pz)
    own = (
        f"FROM {detail} AS d WHERE d.cFlag = N'{flag}' AND d.cVouchType = N'{vouch}'"
        f" AND d.cVouchID = N'{safe_lot(code)}' AND d.cProcStyle = d.cVouchType"
    )
    head = f"(SELECT COUNT(*) FROM dbo.Ap_CloseBill AS h WHERE h.iID = {safe_id(doc)} AND h.cPzID = N'{key}')"
    marked = f"(SELECT COUNT(*) {own} AND d.cPZid = N'{key}')"
    total = f"(SELECT COUNT(*) {own})"
    return _one(_cnt(head) + "\n  + N'|' + " + _cnt(marked) + "\n  + N'|' + " + _cnt(total))


def left_sql(pz: str) -> str:
    """取消制单后仍引用该外部业务号的行数：凭证行、两张往来明细、收付款单表头。"""
    key = safe_pz(pz)
    expr = (
        "CONVERT(varchar(12),\n"
        f"  (SELECT COUNT(*) FROM dbo.GL_accvouch WHERE coutno_id = N'{key}')\n"
        f"  + (SELECT COUNT(*) FROM dbo.Ar_Detail WHERE cPZid = N'{key}')\n"
        f"  + (SELECT COUNT(*) FROM dbo.Ap_Detail WHERE cPZid = N'{key}')\n"
        f"  + (SELECT COUNT(*) FROM dbo.Ap_CloseBill WHERE cPzID = N'{key}'))"
    )
    return _one(expr)


def refund_voucher_samples() -> list[tuple[str, str]]:
    rows = [("rv-code", code_sql("100")), ("rv-left", left_sql("AP0000000000001"))]
    for kind, (flag, vouch, _detail, _card, _twin) in REFUND.items():
        tag = "rv-" + flag.lower() + vouch
        pz = flag + "0000000000001"
        rows += [
            (tag + "-gl", gl_sql(kind, pz, "0000000001", ("220201", "100201"), "S0001")),
            (tag + "-marks", marks_sql(kind, "100", "0000000001", pz)),
        ]
    return rows
