"""重做用例的准备步骤（replay_prep）用到的只读 SQL：结账标志、凭证状态、结算单关联的发票。

只读。年度、期间、id 只接受正整数，外部业务号只接受字母和数字，再拼进 SQL。每条 SQL 输出 ROW:<值>（多个值用 | 分隔）。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.exgain_replay_sql import safe_pz

# 结账标志列（GL_mend），顺序与 MODULES 一致。
MODULES = ("pu", "sa", "st", "ia", "ar", "ap", "gl")
_COLS = ("bflag_PU", "bflag_SA", "bflag_ST", "bflag_IA", "bflag_AR", "bflag_AP", "bflag")
VOUCHER_COLS = 10
INVOICE_COLS = 5


def _num(value: int, low: int, high: int) -> int:
    if type(value) is not int or not low <= value <= high:
        raise ValueError("年度或期间不合法")
    return value


def closed_sql(year: int, period: int) -> str:
    """该年度 period 到 12 期，每期一行：<期间>|<pu>|<sa>|<st>|<ia>|<ar>|<ap>|<gl>（1 已结账，0 未结账）。"""
    y, p = _num(year, 1900, 2999), _num(period, 1, 12)
    flags = " + N'|' + ".join(f"CASE WHEN ISNULL(m.{col}, 0) <> 0 THEN N'1' ELSE N'0' END" for col in _COLS)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(4), m.iperiod) + N'|' + {flags} AS v
FROM dbo.GL_mend AS m WHERE m.iyear = {y} AND m.iperiod BETWEEN {p} AND 12
ORDER BY m.iperiod DESC;
"""


def voucher_sql(pz: str) -> str:
    """一行：<凭证行数>|<凭证张数>|<已记账行数>|<作废行数>|<年度>|<期间>|<类别字>|<凭证号>|<已审核 1/0>|<已出纳签字 1/0>。"""
    key = safe_pz(pz)
    one = "COUNT(DISTINCT CONVERT(varchar(4), g.iyear) + CONVERT(varchar(4), g.iperiod) + g.csign + CONVERT(varchar(12), g.ino_id))"
    parts = (
        "CONVERT(varchar(12), COUNT(*))",
        f"CONVERT(varchar(12), {one})",
        "CONVERT(varchar(12), SUM(CASE WHEN ISNULL(g.ibook, 0) <> 0 THEN 1 ELSE 0 END))",
        "CONVERT(varchar(12), SUM(CASE WHEN ISNULL(g.iflag, 0) = 1 THEN 1 ELSE 0 END))",
        "ISNULL(CONVERT(varchar(4), MIN(g.iyear)), N'')",
        "ISNULL(CONVERT(varchar(4), MIN(g.iperiod)), N'')",
        "ISNULL(MIN(g.csign), N'')",
        "ISNULL(CONVERT(varchar(12), MIN(g.ino_id)), N'')",
        "CASE WHEN MAX(LTRIM(RTRIM(ISNULL(g.ccheck, N'')))) <> N'' THEN N'1' ELSE N'0' END",
        "CASE WHEN MAX(LTRIM(RTRIM(ISNULL(g.ccashier, N'')))) <> N'' THEN N'1' ELSE N'0' END",
    )
    expr = " + N'|' + ".join(parts)
    return f"SET NOCOUNT ON;\nSELECT N'ROW:' + {expr} AS v\nFROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{key}';\n"


def invoices_sql(psvid: int) -> str:
    """结算单关联的每张采购发票一行：<PBVID>|<已应付审核 1/0>|<应付审核行上的凭证号>|<审核登记年度>|<期间>。"""
    doc = safe_id(str(psvid))
    sign = (
        "FROM dbo.Ap_Detail AS d WHERE d.cFlag = N'AP' AND d.cVouchType = h.cPBVBillType AND d.cVouchID = h.cPBVCode"
        " AND d.cProcStyle = d.cVouchType"
    )
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(20), h.PBVID)
  + N'|' + CASE WHEN LTRIM(RTRIM(ISNULL(h.cPBVVerifier, N''))) <> N'' THEN N'1' ELSE N'0' END
  + N'|' + ISNULL((SELECT MIN(LTRIM(RTRIM(d.cPZid))) {sign} AND LTRIM(RTRIM(ISNULL(d.cPZid, N''))) <> N''), N'')
  + N'|' + ISNULL((SELECT CONVERT(varchar(4), MIN(YEAR(d.dRegDate))) {sign}), N'')
  + N'|' + ISNULL((SELECT CONVERT(varchar(4), MIN(d.iPeriod)) {sign}), N'') AS v
FROM dbo.PurBillVouch AS h
WHERE h.PBVID IN (SELECT b.PBVID FROM dbo.PurSettleVouchs AS l INNER JOIN dbo.PurBillVouchs AS b ON b.ID = l.iBsID
  WHERE l.PSVID = {doc})
ORDER BY h.PBVID;
"""


def replay_prep_samples() -> list[tuple[str, str]]:
    return [
        ("replay-prep-closed", closed_sql(2026, 8)),
        ("replay-prep-voucher", voucher_sql("AR0000000000007")),
        ("replay-prep-invoices", invoices_sql(461)),
    ]
