"""票据退回与应付票据用例（notes_return：note-return-cycle、note-return-split-cycle、ap-note-cycle）的挑单与回读 SQL。

只读；表名、列名是常量。拼进 SQL 的值都先校验：flag 只接受 AR / AP，票据号只接受用例自己编的 E2E 加数字（safe_note），
处理号只接受 PJJAR / CLAR / PJJAP / CLAP 加数字，退回生成的单据号只接受 1 到 30 位字母数字，外部业务号只接受 AR / AP 加数字，
主键只接受正整数，日期只接受 yyyy-mm-dd。每条 SQL 输出一行 ROW:<值>，多个值用 | 分隔；文本值里的 | 换成 /。
"""

from __future__ import annotations

import re

from co.e2e.casespec.arap_proc_sql import _count, _day, _line, _money, _open_period, _row, safe_pz
from co.e2e.casespec.notes_sql import _join, _text, _top, safe_note

_PROC = re.compile(r"^(PJJAR|CLAR|PJJAP|CLAP)[0-9]{1,20}\Z")
_BILL = re.compile(r"^[0-9A-Za-z]{1,30}\Z")
# flag → 往来明细表、收付款单类型、退回生成的单据类型。
_SIDE = {"AR": ("Ar_Detail", "48", "R0"), "AP": ("Ap_Detail", "49", "P0")}


def safe_flag(raw: str) -> str:
    if raw not in _SIDE:
        raise ValueError("flag 不合法")
    return raw


def safe_proc(raw: str) -> str:
    if _PROC.fullmatch(raw or "") is None:
        raise ValueError("处理号不合法")
    return raw


def safe_bill(raw: str) -> str:
    if _BILL.fullmatch(raw or "") is None:
        raise ValueError("单据号不合法")
    return raw


def bill_type(flag: str) -> str:
    """退回生成的单据类型：应收 R0、应付 P0。"""
    return _SIDE[safe_flag(flag)][2]


# ---- 挑单 ----


def ap_setup_sql(day: str) -> str:
    """一行：<供应商>|<部门>|<业务员（可空）>|<应付票据科目>|<结算银行科目>。

    取最近一张付款单（49）的供应商（未停用）、部门（末级）、业务员（存在或为空）；应付票据科目取 day 所在年度基本科目 pjkm 的
    应付一侧（cApCode），没有设置时取 2201 下编码最小的末级科目；银行科目取应收票据结算（9A）用得最多、在该年度是末级银行科目的，
    没有时取编码最小的末级银行科目；结算方式 301 是末级；day 所在期间应收、应付都未结账。
    """
    text, year, _month = _day(day)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {_text("c.cDwCode")} + N'|' + {_text("c.cDeptCode")} + N'|' + {_text("c.cPerson")}
  + N'|' + k.km + N'|' + b.code AS v
FROM dbo.Ap_CloseBill AS c
INNER JOIN dbo.Vendor AS ve ON ve.cVenCode = c.cDwCode AND ve.dEndDate IS NULL
INNER JOIN dbo.Department AS dp ON dp.cDepCode = c.cDeptCode AND dp.bDepEnd = 1
CROSS APPLY (SELECT TOP (1) x.km FROM (
    SELECT 0 AS o, LTRIM(RTRIM(i.cApCode)) AS km FROM dbo.Ap_InputCode AS i
    WHERE i.cNote_f = N'pjkm' AND i.iyear = {year} AND ISNULL(i.cApCode, N'') <> N''
    UNION ALL
    SELECT 1, LTRIM(RTRIM(a.ccode)) FROM dbo.code AS a WHERE a.iyear = {year} AND a.bend = 1 AND a.ccode LIKE N'2201%'
  ) AS x ORDER BY x.o, x.km) AS k
CROSS APPLY (SELECT TOP (1) LTRIM(RTRIM(a.ccode)) AS code FROM dbo.code AS a
  WHERE a.iyear = {year} AND a.bend = 1 AND a.bbank = 1
  ORDER BY (SELECT COUNT(*) FROM dbo.AP_Note_Sub AS s WHERE s.cCode = a.ccode AND s.cProcStyle = N'9A') DESC, a.ccode) AS b
WHERE c.cFlag = N'AP' AND c.cVouchType = N'49'
  AND (ISNULL(c.cPerson, N'') = N'' OR EXISTS (SELECT 1 FROM dbo.Person AS p WHERE p.cPersonCode = c.cPerson))
  AND EXISTS (SELECT 1 FROM dbo.SettleStyle AS t WHERE t.cSSCode = N'301' AND t.bSSEnd = 1)
  AND {_open_period(text)}
ORDER BY c.iID DESC;
"""


# ---- 回读 ----


def note_state_sql(flag: str, no: str) -> str:
    """一行：<票据张数>|<cLink = flag + 50 + 票据号的张数>|<票面>|<余额>|<票据科目>|<往来单位>|<iCloseID>
    |<与票据对上的收付款单张数（48 / 49、本方、来源 C / 50、来源号和票据号都是票据号）>|<以该票据号为票据号的收付款单张数>
    |<对上的收付款单里已审核的张数>|<该票据的处理行数>|<以该票据号为来源号的 R0 / P0 单据张数>。"""
    side, key = safe_flag(flag), safe_note(no)
    close, bill = _SIDE[side][1], _SIDE[side][2]
    frm = f"FROM dbo.AP_Note AS n WHERE n.cFlag = N'{side}' AND n.cVouchType = N'50' AND n.cVouchID = N'{key}'"
    tied = (
        "SELECT COUNT(*) FROM dbo.AP_Note AS n INNER JOIN dbo.Ap_CloseBill AS c ON c.iID = n.iCloseID"
        f" WHERE n.cFlag = N'{side}' AND n.cVouchType = N'50' AND n.cVouchID = N'{key}' AND c.cVouchType = N'{close}'"
        f" AND c.cFlag = N'{side}' AND c.cSrcFlag = N'C' AND c.cCoVouchType = N'50' AND c.cCoVouchID = n.cVouchID"
        " AND c.cNoteNo = n.cVouchID"
    )
    subs = (
        "SELECT COUNT(*) FROM dbo.AP_Note_Sub AS s INNER JOIN dbo.AP_Note AS n ON n.cLink = s.cLink"
        f" WHERE n.cFlag = N'{side}' AND n.cVouchType = N'50' AND n.cVouchID = N'{key}'"
    )
    parts = (
        _count(f"SELECT COUNT(*) {frm}"),
        _count(f"SELECT COUNT(*) {frm} AND n.cLink = N'{side}50' + n.cVouchID"),
        _money(f"(SELECT TOP (1) n.iAmount {frm})"),
        _money(f"(SELECT TOP (1) n.iRAmount {frm})"),
        _top("n.cCode", frm),
        _top("n.cEndorser", frm),
        _top("ISNULL(n.iCloseID, 0)", frm),
        _count(tied),
        _count(f"SELECT COUNT(*) FROM dbo.Ap_CloseBill AS c WHERE c.cCoVouchType = N'50' AND c.cNoteNo = N'{key}'"),
        _count(tied + " AND LTRIM(RTRIM(ISNULL(c.cCheckMan, N''))) <> N''"),
        _count(subs),
        _count(
            f"SELECT COUNT(*) FROM dbo.Ap_Vouch AS v WHERE v.cFlag = N'{side}' AND v.cVouchType = N'{bill}'"
            f" AND v.cSrcNo = N'{key}'"
        ),
    )
    return _row(_join(parts))


def close_sql(close_id: int) -> str:
    """一行（登记生成的收付款单，按主键）：<表体金额合计>|<表体余额合计>|<已审核 1 / 未审核 0>|<第一行应收（应付）款行的科目>
    |<两张往来明细里挂在该单上的行数>。退回前后应完全相同（登记时的收付款单不动）。"""
    ref = _line(close_id)
    lines = f"FROM dbo.Ap_CloseBills AS b WHERE b.iID = {ref}"
    on = "WHERE d.cVouchType = h.cVouchType AND d.cVouchID = h.cVouchID AND d.cFlag = h.cFlag"
    parts = (
        _money(f"(SELECT SUM(b.iAmt) {lines})"),
        _money(f"(SELECT SUM(b.iRAmt) {lines})"),
        _count("CASE WHEN LTRIM(RTRIM(ISNULL(h.cCheckMan, N''))) <> N'' THEN 1 ELSE 0 END"),
        _top("b.cKm", f"{lines} AND ISNULL(b.iType, 0) = 0 ORDER BY b.ID"),
        _count(
            f"(SELECT COUNT(*) FROM dbo.Ar_Detail AS d {on}) + (SELECT COUNT(*) FROM dbo.Ap_Detail AS d {on})"
        ),
    )
    return _row(_join(parts), f"FROM dbo.Ap_CloseBill AS h WHERE h.iID = {ref}")


def proc_sub_sql(no: str) -> str:
    """一行（票据处理行 AP_Note_Sub，按处理号）：<行数>|<处理方式>|<金额合计>|<费用与利息合计>|<科目>|<cBank>
    |<cCoVouchType>|<cCoVouchID>|<子票起号>|<子票止号>|<cPzID 不空的行数>。"""
    key = safe_proc(no)
    frm = f"FROM dbo.AP_Note_Sub AS s WHERE s.cCancelNo = N'{key}'"
    parts = (
        _count(f"SELECT COUNT(*) {frm}"),
        _top("s.cProcStyle", frm),
        _money(f"(SELECT SUM(s.iAmount) {frm})"),
        _money(f"(SELECT SUM(ISNULL(s.iExpense, 0) + ISNULL(s.iIntrest, 0)) {frm})"),
        _top("s.cCode", frm),
        _top("s.cBank", frm),
        _top("s.cCoVouchType", frm),
        _top("s.cCoVouchID", frm),
        _top("s.csbnstart", frm),
        _top("s.csbnend", frm),
        _count(f"SELECT COUNT(*) {frm} AND ISNULL(s.cPzID, N'') <> N''"),
    )
    return _row(_join(parts))


def proc_detail_sql(flag: str, no: str) -> str:
    """一行（本方往来明细，按处理号）：<行数>|<单据行（R0 / P0、iFlag 0）行数>|<其借方合计>|<贷方合计>|<科目>|<单号>
    |<票据行（50 / 50、iFlag 3）行数>|<其借方合计>|<贷方合计>|<科目>|<单号>|<另一张往来明细里该处理号的行数>
    |<单据行摘要>|<票据行摘要>。金额是本币。"""
    side, key = safe_flag(flag), safe_proc(no)
    table, other = _SIDE[side][0], _SIDE["AP" if side == "AR" else "AR"][0]
    bill = _SIDE[side][2]
    own = f"FROM dbo.{table} AS d WHERE d.cCancelNo = N'{key}' AND d.cFlag = N'{side}'"
    row_b = f"{own} AND d.cVouchType = N'{bill}' AND d.cCoVouchType = N'{bill}' AND d.iFlag = 0"
    row_n = f"{own} AND d.cVouchType = N'50' AND d.cCoVouchType = N'50' AND d.iFlag = 3"
    parts = (
        _count(f"SELECT COUNT(*) {own}"),
        _count(f"SELECT COUNT(*) {row_b}"),
        _money(f"(SELECT SUM(d.iDAmount) {row_b})"),
        _money(f"(SELECT SUM(d.iCAmount) {row_b})"),
        _top("d.cCode", row_b + " ORDER BY d.Auto_ID"),
        _top("d.cVouchID", row_b + " ORDER BY d.Auto_ID"),
        _count(f"SELECT COUNT(*) {row_n}"),
        _money(f"(SELECT SUM(d.iDAmount) {row_n})"),
        _money(f"(SELECT SUM(d.iCAmount) {row_n})"),
        _top("d.cCode", row_n + " ORDER BY d.Auto_ID"),
        _top("d.cVouchID", row_n + " ORDER BY d.Auto_ID"),
        _count(f"SELECT COUNT(*) FROM dbo.{other} AS d WHERE d.cCancelNo = N'{key}'"),
        _top("d.cDigest", row_b + " ORDER BY d.Auto_ID"),
        _top("d.cDigest", row_n + " ORDER BY d.Auto_ID"),
    )
    return _row(_join(parts))


def bill_sql(flag: str, code: str) -> str:
    """一行（退回生成的应收单 R0 / 应付单 P0，按单号）：<表头张数>|<cCoVouchType>|<cSrcNo>|<已审核张数>|<iAmount>|<iRAmount>
    |<往来单位>|<表头科目>|<表头 bd_c>|<cPZid 不空的张数>|<表体行数（cLink = 类型 + 单号）>|<审核行（cProcStyle = 类型）行数>
    |<审核日期 yyyy-mm-dd>|<审核时间 dverifysystime 不空的张数>。

    退回生成的单据经 U8 组件保存后由桥补写为已审核（审核人 = 操作员、审核日期 = 退回日期），但往来明细里没有审核行。"""
    side, key = safe_flag(flag), safe_bill(code)
    table, _close, kind = _SIDE[side]
    frm = f"FROM dbo.Ap_Vouch AS v WHERE v.cFlag = N'{side}' AND v.cVouchType = N'{kind}' AND v.cVouchID = N'{key}'"
    parts = (
        _count(f"SELECT COUNT(*) {frm}"),
        _top("v.cCoVouchType", frm),
        _top("v.cSrcNo", frm),
        _count(f"SELECT COUNT(*) {frm} AND LTRIM(RTRIM(ISNULL(v.cCheckMan, N''))) <> N''"),
        _money(f"(SELECT TOP (1) v.iAmount {frm})"),
        _money(f"(SELECT TOP (1) v.iRAmount {frm})"),
        _top("v.cDwCode", frm),
        _top("v.cCode", frm),
        _top("CONVERT(int, v.bd_c)", frm),
        _count(f"SELECT COUNT(*) {frm} AND ISNULL(v.cPZid, N'') <> N''"),
        _count(f"SELECT COUNT(*) FROM dbo.Ap_Vouchs AS b WHERE b.cLink = N'{kind}{key}'"),
        _count(
            f"SELECT COUNT(*) FROM dbo.{table} AS d WHERE d.cFlag = N'{side}' AND d.cVouchType = N'{kind}'"
            f" AND d.cVouchID = N'{key}' AND d.cProcStyle = N'{kind}'"
        ),
        _top("CONVERT(char(10), v.dverifydate, 23)", frm),
        _count(f"SELECT COUNT(*) {frm} AND v.dverifysystime IS NOT NULL"),
    )
    return _row(_join(parts))


def gl_sql(flag: str, pz: str) -> str:
    """一行：<凭证行数>|<凭证张数>|<来源不是 flag / PJ 的行数>|<借方合计>|<贷方合计>。"""
    side, key = safe_flag(flag), safe_pz(pz)
    bad = (
        f"SUM(CASE WHEN ISNULL(g.coutsysname, N'') = N'{side}' AND ISNULL(g.coutsign, N'') = N'PJ' THEN 0 ELSE 1 END)"
    )
    vouchers = "COUNT(DISTINCT CONVERT(varchar(4), g.iperiod) + g.csign + CONVERT(varchar(12), g.ino_id))"
    expr = _join((_count("COUNT(*)"), _count(vouchers), _count(f"ISNULL({bad}, 0)"), _money("SUM(g.md)"), _money("SUM(g.mc)")))
    return _row(expr, f"FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{key}'")


def marks_sql(flag: str, no: str, pz: str) -> str:
    """一行：<该处理号的本方往来明细行数>|<其中 cPZid = pz 且 cGLSign、iGLno_id、dPZDate 都有值的行数>
    |<票据处理行里 cPzID = pz 的行数>|<GL_accvouch 里 coutno_id = pz 的行数>|<cPZid = pz 的应收（应付）单张数>。"""
    side, key, ref = safe_flag(flag), safe_proc(no), safe_pz(pz)
    own = f"FROM dbo.{_SIDE[side][0]} AS d WHERE d.cCancelNo = N'{key}' AND d.cFlag = N'{side}'"
    marked = (
        f"{own} AND d.cPZid = N'{ref}' AND d.cGLSign IS NOT NULL AND d.iGLno_id IS NOT NULL AND d.dPZDate IS NOT NULL"
    )
    parts = (
        _count(f"SELECT COUNT(*) {own}"),
        _count(f"SELECT COUNT(*) {marked}"),
        _count(f"SELECT COUNT(*) FROM dbo.AP_Note_Sub AS s WHERE s.cCancelNo = N'{key}' AND s.cPzID = N'{ref}'"),
        _count(f"SELECT COUNT(*) FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{ref}'"),
        _count(f"SELECT COUNT(*) FROM dbo.Ap_Vouch AS v WHERE v.cFlag = N'{side}' AND v.cPZid = N'{ref}'"),
    )
    return _row(_join(parts))


def notes_more_samples() -> list[tuple[str, str]]:
    no = "E2E2026100209000006"
    return [
        ("ntr-ap-setup", ap_setup_sql("2026-10-02")),
        ("ntr-note-ar", note_state_sql("AR", no)),
        ("ntr-note-ap", note_state_sql("AP", no)),
        ("ntr-close", close_sql(100)),
        ("ntr-sub", proc_sub_sql("CLAR0000000900002")),
        ("ntr-detail-ar", proc_detail_sql("AR", "CLAR0000000900002")),
        ("ntr-detail-ap", proc_detail_sql("AP", "PJJAP0000000000001")),
        ("ntr-bill-ar", bill_sql("AR", "YS202610020001")),
        ("ntr-bill-ap", bill_sql("AP", "YF202610020001")),
        ("ntr-gl", gl_sql("AP", "AP0000000000001")),
        ("ntr-marks", marks_sql("AR", "CLAR0000000900002", "AR0000000000001")),
    ]
