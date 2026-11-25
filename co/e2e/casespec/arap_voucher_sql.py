"""应收制单用例（arap_voucher）的回读 SQL。

has_av / av_sql 进 stash / pred 的回读，av_samples() 进样例 SQL（接线在 wiring 汇总模块）。挑单沿用 arap_verify 的
arv_inv（发货行）。标识符都是常量；发票 id 只接受正整数，外部业务号只接受 bag 里的数字，再拼进 SQL。
"""

from __future__ import annotations

import re

from co.e2e.casespec.mo_close_sql import safe_id

PZ_KEY = "av_pzno"
_DIGITS = re.compile(r"^[0-9]{1,20}\Z")
_HEAD = "(SELECT h.cVouchType AS t, h.cSBVCode AS c FROM dbo.SaleBillVouch AS h WHERE h.SBVID = {id})"


def pz_of(bag: dict[str, str]) -> str:
    digits = bag.get(PZ_KEY, "")
    if _DIGITS.fullmatch(digits or "") is None:
        raise ValueError("缺外部业务号")
    return "AR" + digits


def _pz_digits(doc: str, _bag: dict[str, str]) -> str:
    # 发票原始往来明细上的外部业务号去掉 AR 前缀后的数字（bag 只收数字，用例再拼回前缀）；没制单时为空。
    head = _HEAD.format(id=safe_id(doc))
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + ISNULL(MAX(SUBSTRING(d.cPZid, 3, 20)), N'') AS v\n"
        f"FROM dbo.Ar_Detail AS d INNER JOIN {head} AS h ON d.cVouchType = h.t AND d.cVouchID = h.c\n"
        "WHERE d.cFlag = N'AR' AND d.cProcStyle = d.cVouchType AND ISNULL(d.cPZid, N'') <> N'';\n"
    )


def _gl_bad(doc: str, bag: dict[str, str]) -> str:
    # 凭证的问题数：少于 2 行、借贷不平、借方合计不等于发票价税合计、有行不带应收来源（AR、原始单据类型和单号）、
    # 第 1 行不是往来明细科目的借方。0 表示都对。
    pz = pz_of(bag)
    head = _HEAD.format(id=safe_id(doc))
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(12), ISNULL(\n"
        "  (CASE WHEN COUNT(*) < 2 THEN 1 ELSE 0 END)\n"
        "  + (CASE WHEN SUM(g.md) = SUM(g.mc) THEN 0 ELSE 1 END)\n"
        f"  + (CASE WHEN SUM(g.md) = (SELECT SUM(b.iNatSum) FROM dbo.SaleBillVouchs AS b WHERE b.SBVID = {safe_id(doc)})"
        " THEN 0 ELSE 1 END)\n"
        "  + SUM(CASE WHEN ISNULL(g.coutsysname, N'') = N'AR' AND ISNULL(g.coutbillsign, N'') = h.t"
        " AND ISNULL(g.coutid, N'') = h.c THEN 0 ELSE 1 END)\n"
        "  + (CASE WHEN MAX(CASE WHEN g.inid = 1 AND g.md > 0 AND g.ccode = a.k THEN 1 ELSE 0 END) = 1 THEN 0 ELSE 1 END),"
        " 99)) AS v\n"
        f"FROM dbo.GL_accvouch AS g CROSS JOIN {head} AS h\n"
        f"CROSS JOIN (SELECT TOP (1) d.cCode AS k FROM dbo.Ar_Detail AS d WHERE d.cPZid = N'{pz}') AS a\n"
        f"WHERE g.coutno_id = N'{pz}';\n"
    )


def _marks_bad(doc: str, bag: dict[str, str]) -> str:
    # 回写的问题数：原始往来明细缺 cPZid / cGLSign / iGLno_id，发票表体缺线索号 / 凭证号。
    pz = pz_of(bag)
    sid = safe_id(doc)
    head = _HEAD.format(id=sid)
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(12),\n"
        f"  (SELECT COUNT(*) FROM dbo.Ar_Detail AS d INNER JOIN {head} AS h ON d.cVouchType = h.t AND d.cVouchID = h.c\n"
        f"   WHERE d.cProcStyle = d.cVouchType AND (ISNULL(d.cPZid, N'') <> N'{pz}' OR d.cGLSign IS NULL"
        " OR d.iGLno_id IS NULL))\n"
        f"  + (SELECT COUNT(*) FROM dbo.SaleBillVouchs AS b WHERE b.SBVID = {sid}"
        f" AND (ISNULL(b.cClue, N'') <> N'{pz}' OR b.cPZNum IS NULL))) AS v;\n"
    )


def _left(doc: str, bag: dict[str, str]) -> str:
    # 取消制单之后还引用该外部业务号的行数（凭证行、往来明细、发票表体线索号）。0 表示清干净了。
    pz = pz_of(bag)
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(12),\n"
        f"  (SELECT COUNT(*) FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{pz}')\n"
        f"  + (SELECT COUNT(*) FROM dbo.Ar_Detail AS d WHERE d.cPZid = N'{pz}')\n"
        f"  + (SELECT COUNT(*) FROM dbo.SaleBillVouchs AS b WHERE b.SBVID = {safe_id(doc)} AND b.cClue IS NOT NULL)) AS v;\n"
    )


_READBACK = {
    "av_pz": _pz_digits,
    "av_glbad": _gl_bad,
    "av_marks": _marks_bad,
    "av_left": _left,
}


def has_av(name: str) -> bool:
    return name in _READBACK


def av_sql(name: str, doc_id: str, bag: dict[str, str]) -> str:
    builder = _READBACK.get(name)
    if builder is None:
        raise ValueError("未知 SQL")
    return builder(doc_id, bag)


def av_samples() -> list[tuple[str, str]]:
    bag = {PZ_KEY: "0000000000001"}
    return [("av-" + name.replace("_", "-"), fn("100", bag)) for name, fn in _READBACK.items()]
