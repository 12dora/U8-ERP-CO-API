"""应收合并制单用例（arap_voucher_merge）的回读 SQL。

只读；标识符是常量。收款单 id 只接受正整数（safe_id），单号只接受 [0-9A-Za-z._-]（safe_lot），外部业务号只接受 AR 加数字，
金额只接受整数，再拼进 SQL。每条 SQL 输出一行 ROW:<值>。
"""

from __future__ import annotations

import re

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.stock_plus_sql import safe_lot

_PZ = re.compile(r"^AR[0-9]{1,20}\Z")
_YEAR = re.compile(r"^[0-9]{4}\Z")


def safe_pz(raw: str) -> str:
    if _PZ.fullmatch(raw or "") is None:
        raise ValueError("外部业务号不合法")
    return raw


def _one(expr: str, frm: str = "") -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v" + ("\n" + frm if frm else "") + ";\n"


def _in(codes: tuple[str, ...]) -> str:
    return ", ".join("N'" + safe_lot(code) + "'" for code in codes)


def km_sql(day: str) -> str:
    """应收受控科目：会计年度里 1122 开头、带客户辅助的末级科目（同种子 steps_docs_arap.km_sql）；没有时为空。"""
    year = day[:4]
    if _YEAR.fullmatch(year) is None:
        raise ValueError("日期不合法")
    inner = (
        f"SELECT TOP (1) LTRIM(RTRIM(c.ccode)) FROM dbo.code AS c WHERE c.iyear = {year} AND c.bend = 1 AND c.bcus = 1"
        " AND c.ccode LIKE N'1122%' ORDER BY c.ccode"
    )
    return _one(f"ISNULL(({inner}), N'')")


def code_sql(doc: str) -> str:
    """收款单号（Ap_CloseBill.cVouchID）。"""
    return _one(f"ISNULL((SELECT b.cVouchID FROM dbo.Ap_CloseBill AS b WHERE b.iID = {safe_id(doc)}), N'')")


def gl_bad_sql(pz: str, bills: tuple[tuple[str, int], ...]) -> str:
    """凭证的问题数：不是同一张凭证、借贷不平、有行不带应收来源（AR、48、来源单号是本次的收款单之一）、
    某张收款单的分录借贷合计不等于它的金额（每行 coutid 是各自的单号）。0 表示都对。"""
    codes = tuple(code for code, _amount in bills)
    terms = [
        "(CASE WHEN COUNT(DISTINCT CONVERT(varchar(4), g.iperiod) + g.csign + CONVERT(varchar(12), g.ino_id)) = 1"
        " THEN 0 ELSE 1 END)",
        "(CASE WHEN SUM(g.md) = SUM(g.mc) THEN 0 ELSE 1 END)",
        "SUM(CASE WHEN ISNULL(g.coutsysname, N'') = N'AR' AND ISNULL(g.coutbillsign, N'') = N'48'"
        f" AND g.coutid IN ({_in(codes)}) THEN 0 ELSE 1 END)",
    ]
    for code, amount in bills:
        own = f"g.coutid = N'{safe_lot(code)}'"
        cash = int(amount)
        terms.append(
            f"(CASE WHEN SUM(CASE WHEN {own} THEN g.md ELSE 0 END) = {cash}"
            f" AND SUM(CASE WHEN {own} THEN g.mc ELSE 0 END) = {cash} THEN 0 ELSE 1 END)"
        )
    expr = "CONVERT(varchar(12), ISNULL(" + "\n  + ".join(terms) + ", 99))"
    return _one(expr, f"FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{safe_pz(pz)}'")


def marks_bad_sql(pz: str, docs: tuple[str, ...], codes: tuple[str, ...]) -> str:
    """回写的问题数：两张收款单的原始往来明细缺 cPZid / cGLSign / iGLno_id 或不是这个外部业务号，表头 cPzID 不是它，
    引用它的往来明细不正好来自这两张单据。0 表示都对。"""
    key = safe_pz(pz)
    ids = ", ".join(safe_id(doc) for doc in docs)
    expr = (
        "CONVERT(varchar(12),\n"
        f"  (SELECT COUNT(*) FROM dbo.Ar_Detail AS d WHERE d.cFlag = N'AR' AND d.cVouchType = N'48'"
        f" AND d.cVouchID IN ({_in(codes)}) AND d.cProcStyle = d.cVouchType"
        f" AND (ISNULL(d.cPZid, N'') <> N'{key}' OR d.cGLSign IS NULL OR d.iGLno_id IS NULL))\n"
        f"  + (SELECT COUNT(*) FROM dbo.Ap_CloseBill AS b WHERE b.iID IN ({ids}) AND ISNULL(b.cPzID, N'') <> N'{key}')\n"
        f"  + (CASE WHEN (SELECT COUNT(DISTINCT d.cVouchID) FROM dbo.Ar_Detail AS d WHERE d.cPZid = N'{key}')"
        f" = {len(codes)} THEN 0 ELSE 1 END))"
    )
    return _one(expr)


def left_sql(pz: str, docs: tuple[str, ...]) -> str:
    """取消制单之后还引用该外部业务号的行数（凭证行、往来明细、收款单表头）。0 表示清干净了。"""
    key = safe_pz(pz)
    ids = ", ".join(safe_id(doc) for doc in docs)
    expr = (
        "CONVERT(varchar(12),\n"
        f"  (SELECT COUNT(*) FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{key}')\n"
        f"  + (SELECT COUNT(*) FROM dbo.Ar_Detail AS d WHERE d.cPZid = N'{key}')\n"
        f"  + (SELECT COUNT(*) FROM dbo.Ap_CloseBill AS b WHERE b.iID IN ({ids}) AND ISNULL(b.cPzID, N'') <> N''))"
    )
    return _one(expr)


def voucher_merge_samples() -> list[tuple[str, str]]:
    bills = (("SK0001", 60), ("SK0002", 40))
    return [
        ("avm-km", km_sql("2026-10-02")),
        ("avm-code", code_sql("100")),
        ("avm-gl", gl_bad_sql("AR0000000000001", bills)),
        ("avm-marks", marks_bad_sql("AR0000000000001", ("100", "101"), ("SK0001", "SK0002"))),
        ("avm-left", left_sql("AR0000000000001", ("100", "101"))),
    ]


def seed_ready_sql(customer: str, dept: str, person: str) -> str:
    """种子数据的客户、部门、业务员都在时为 1，否则 0（没有种子数据的账套没有这些档案，用例记 N/A）。"""
    cus, dep, per = (safe_lot(code) for code in (customer, dept, person))
    expr = (
        f"CASE WHEN EXISTS (SELECT 1 FROM dbo.Customer WHERE cCusCode = N'{cus}')"
        f" AND EXISTS (SELECT 1 FROM dbo.Department WHERE cDepCode = N'{dep}')"
        f" AND EXISTS (SELECT 1 FROM dbo.Person WHERE cPersonCode = N'{per}') THEN N'1' ELSE N'0' END"
    )
    return _one(expr)
