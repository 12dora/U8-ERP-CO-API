"""票据用例（notes / notes_more：登记、结算、贴现、背书、分包结算）的挑单与回读 SQL。

只读；表名、列名是常量。拼进 SQL 的值都先校验：票据号只接受用例自己编的 E2E 加数字（safe_note），处理号只接受
PJJAR / PJTAR / PJBAR 加数字，外部业务号只接受 AR / AP 加数字，往来单位只接受 [0-9A-Za-z._-]，主键只接受正整数，
日期只接受 yyyy-mm-dd。每条 SQL 输出一行 ROW:<值>，多个值用 | 分隔；文本值里的 | 换成 /。
"""

from __future__ import annotations

import re

from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.arap_proc_sql import _HOME, _count, _day, _line, _money, _no_contract, _open_period, _row, safe_pz
from co.e2e.casespec.notes_util import FACE

_NOTE = re.compile(r"^E2E[0-9]{6,40}\Z")
_PROC = re.compile(r"^(PJJAR|PJTAR|PJBAR)[0-9]{1,20}\Z")
# 背书挑的发票行至少要有的余额：用例登记的票据不分包、只能整张背书，取票面。
_ENDORSE_LEAST = format(FACE, "f")


# ---- 校验与片段 ----


def safe_note(raw: str) -> str:
    if _NOTE.fullmatch(raw or "") is None:
        raise ValueError("票据号不合法")
    return raw


def safe_proc(raw: str) -> str:
    if _PROC.fullmatch(raw or "") is None:
        raise ValueError("处理号不合法")
    return raw


def _text(expr: str) -> str:
    return f"REPLACE(LTRIM(RTRIM(ISNULL(CONVERT(nvarchar(200), {expr}), N''))), N'|', N'/')"


def _top(expr: str, frm: str) -> str:
    # 取 frm 的第一行的 expr（文本）；没有行时空串。
    return f"ISNULL((SELECT TOP (1) {_text(expr)} {frm}), N'')"


def _join(parts: tuple[str, ...] | list[str]) -> str:
    return " + N'|' + ".join(parts)


# ---- 挑单 ----


def setup_sql(day: str) -> str:
    """一行：<客户>|<部门>|<业务员（可空）>|<收款人>|<票据科目 pjkm>|<结算银行科目>|<贴现费用科目 pjyfrzkm（可空）>。

    取最近一张可作样本的应收票据（非期初、结算方式 301、已生成收款单、交票客户未停用、部门末级、业务员存在）的往来信息；
    票据科目取 day 所在年度的基本科目 pjkm；银行科目取结算（9A）处理行用得最多、在该年度仍是末级银行科目的那个；
    结算方式 301 是末级；day 所在期间应收、应付都未结账。
    """
    text, year, _month = _day(day)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {_text("n.cEndorser")} + N'|' + {_text("n.cDeptCode")} + N'|' + {_text("n.cPerson")}
  + N'|' + {_text("n.cReceiver")} + N'|' + k.km + N'|' + b.code + N'|' + ISNULL(f.km, N'') AS v
FROM dbo.AP_Note AS n
INNER JOIN dbo.Customer AS c ON c.cCusCode = n.cEndorser AND c.dEndDate IS NULL
INNER JOIN dbo.Department AS dp ON dp.cDepCode = n.cDeptCode AND dp.bDepEnd = 1
CROSS APPLY (SELECT TOP (1) LTRIM(RTRIM(i.cArCode)) AS km FROM dbo.Ap_InputCode AS i
  WHERE i.cNote_f = N'pjkm' AND i.iyear = {year} AND ISNULL(i.cArCode, N'') <> N'') AS k
CROSS APPLY (SELECT TOP (1) LTRIM(RTRIM(s.cCode)) AS code FROM dbo.AP_Note_Sub AS s
  INNER JOIN dbo.code AS a ON a.ccode = s.cCode AND a.iyear = {year} AND a.bend = 1 AND a.bbank = 1
  WHERE s.cProcStyle = N'9A' AND s.cFlag = N'AR'
  GROUP BY s.cCode ORDER BY COUNT(*) DESC, s.cCode) AS b
OUTER APPLY (SELECT TOP (1) LTRIM(RTRIM(i.cArCode)) AS km FROM dbo.Ap_InputCode AS i
  WHERE i.cNote_f = N'pjyfrzkm' AND i.iyear = {year} AND ISNULL(i.cArCode, N'') <> N'') AS f
WHERE n.cFlag = N'AR' AND n.cVouchType = N'50' AND ISNULL(n.bStartFlag, 0) = 0 AND ISNULL(n.iCloseID, 0) > 0
  AND n.cSettleCode = N'301' AND ISNULL(n.cReceiver, N'') <> N''
  AND (ISNULL(n.cPerson, N'') = N'' OR EXISTS (SELECT 1 FROM dbo.Person AS p WHERE p.cPersonCode = n.cPerson))
  AND EXISTS (SELECT 1 FROM dbo.SettleStyle AS t WHERE t.cSSCode = N'301' AND t.bSSEnd = 1)
  AND {_open_period(text)}
ORDER BY n.Auto_ID DESC;
"""


def ap_pick_sql(day: str) -> str:
    """一行：<供应商>|<采购专用发票号>|<发票行 ID>。

    已审核、本位币、余额 >= 票面 100.00（背书整张票据）的采购专用发票（01）行（同 arap_proc_sql.transfer_pick_sql 的应付一侧口径）：
    不受审批流控制、没有网络锁、没有关联合同，日期不晚于 day，day 所在期间应收、应付都未结账。
    """
    text, _year, _month = _day(day)
    return f"""SET NOCOUNT ON;
WITH p AS (
  SELECT d.cDwCode AS dw, d.cCoVouchID AS code, d.iBVid AS line
  FROM dbo.Ap_Detail AS d
  WHERE d.cFlag = N'AP' AND d.cCoVouchType = N'01' AND d.iFlag < 3 AND ISNULL(d.iBVid, 0) > 0
  GROUP BY d.cDwCode, d.cCoVouchID, d.iBVid
  HAVING SUM(ISNULL(d.iCAmount_f, 0) - ISNULL(d.iDAmount_f, 0)) >= {_ENDORSE_LEAST}
)
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(p.dw)) + N'|' + p.code + N'|' + CONVERT(varchar(20), p.line) AS v
FROM p
INNER JOIN dbo.PurBillVouch AS ph ON ph.cPBVCode = p.code AND ph.cPBVBillType = N'01'
INNER JOIN dbo.PurBillVouchs AS pb ON pb.ID = p.line AND pb.PBVID = ph.PBVID
WHERE ph.cVenCode = p.dw AND LTRIM(RTRIM(ISNULL(ph.cPBVVerifier, N''))) <> N'' AND ISNULL(ph.IsWfControlled, 0) = 0
  AND ISNULL(ph.iNetLock, 0) = 0 AND ISNULL(ph.cexch_name, {_HOME}) = {_HOME} AND ph.dPBVDate <= '{text}'
  AND {_no_contract("Ap_Detail", "AP", "N'01'", "p.code")}
  AND {_open_period(text)}
ORDER BY ph.PBVID DESC;
"""


def split_pick_sql(day: str) -> str:
    """一行：<票据主键>|<第一段可用区间起号>|<止号>。

    现有的分包应收票据：非期初、本位币、没换过票、没退回过、有余额、交票客户不空，签发、收票日期不晚于 day，
    登记生成的收款单已审核；可用区间合计等于余额、任意两段不首尾相接（取消后合并回去的区间才与原来逐行相同），
    第一段至少 200 个号；day 所在期间应收、应付都未结账。
    """
    text, _year, _month = _day(day)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), n.Auto_ID) + N'|' + CONVERT(varchar(20), r.cavailstart)
  + N'|' + CONVERT(varchar(20), r.cavailend) AS v
FROM dbo.AP_Note AS n
INNER JOIN dbo.Ap_CloseBill AS c ON c.iID = n.iCloseID AND c.cVouchType = N'48'
  AND LTRIM(RTRIM(ISNULL(c.cCheckMan, N''))) <> N''
CROSS APPLY (SELECT TOP (1) a.cavailstart, a.cavailend FROM dbo.Ap_Note_AvailRange AS a
  WHERE a.cNoteLink = n.cLink ORDER BY a.cavailstart) AS r
WHERE n.cFlag = N'AR' AND n.cVouchType = N'50' AND ISNULL(n.bStartFlag, 0) = 0 AND ISNULL(n.bsubpackage, 0) <> 0
  AND n.iRAmount > 0 AND ISNULL(n.iChangeType, 0) = 0 AND ISNULL(n.cEndorser, N'') <> N''
  AND ISNULL(NULLIF(n.cexch_name, N''), {_HOME}) = {_HOME}
  AND ISNULL(n.dSignDate, '19000101') <= '{text}' AND ISNULL(n.dReceiptDate, '19000101') <= '{text}'
  AND r.cavailend - r.cavailstart >= 199
  AND NOT EXISTS (SELECT 1 FROM dbo.AP_Note_Sub AS s WHERE s.cLink = n.cLink AND s.cProcStyle = N'9C')
  AND (SELECT SUM(a.iavailamount) FROM dbo.Ap_Note_AvailRange AS a WHERE a.cNoteLink = n.cLink) = n.iRAmount
  AND NOT EXISTS (SELECT 1 FROM dbo.Ap_Note_AvailRange AS a INNER JOIN dbo.Ap_Note_AvailRange AS z
    ON z.cNoteLink = a.cNoteLink AND z.cavailstart = a.cavailend + 1 WHERE a.cNoteLink = n.cLink)
  AND {_open_period(text)}
ORDER BY n.Auto_ID DESC;
"""


# ---- 回读 ----


def note_sql(no: str) -> str:
    """一行：<票据张数>|<cLink = AR50+票据号的张数>|<结算方式>|<票面>|<余额>|<票据科目>|<交票客户>|<iCloseID>
    |<与票据对上的收款单张数（48、来源 C / 50、来源号和票据号都是票据号）>|<以该票据号为票据号的收付款单张数>
    |<其中已审核的收款单张数>。"""
    key = safe_note(no)
    frm = f"FROM dbo.AP_Note AS n WHERE n.cFlag = N'AR' AND n.cVouchType = N'50' AND n.cVouchID = N'{key}'"
    tied = (
        "SELECT COUNT(*) FROM dbo.AP_Note AS n INNER JOIN dbo.Ap_CloseBill AS c ON c.iID = n.iCloseID"
        f" WHERE n.cFlag = N'AR' AND n.cVouchType = N'50' AND n.cVouchID = N'{key}' AND c.cVouchType = N'48'"
        " AND c.cFlag = N'AR' AND c.cSrcFlag = N'C' AND c.cCoVouchType = N'50' AND c.cCoVouchID = n.cVouchID"
        " AND c.cNoteNo = n.cVouchID"
    )
    parts = (
        _count(f"SELECT COUNT(*) {frm}"),
        _count(f"SELECT COUNT(*) {frm} AND n.cLink = N'AR50' + n.cVouchID"),
        _top("n.cSettleCode", frm),
        _money(f"(SELECT TOP (1) n.iAmount {frm})"),
        _money(f"(SELECT TOP (1) n.iRAmount {frm})"),
        _top("n.cCode", frm),
        _top("n.cEndorser", frm),
        _top("ISNULL(n.iCloseID, 0)", frm),
        _count(tied),
        _count(f"SELECT COUNT(*) FROM dbo.Ap_CloseBill AS c WHERE c.cCoVouchType = N'50' AND c.cNoteNo = N'{key}'"),
        _count(tied + " AND LTRIM(RTRIM(ISNULL(c.cCheckMan, N''))) <> N''"),
    )
    return _row(_join(parts))


def note_id_sql(no: str) -> str:
    """一行：<票据主键 AP_Note.Auto_ID>；没有这张票据时 0。"""
    key = safe_note(no)
    frm = f"FROM dbo.AP_Note AS n WHERE n.cFlag = N'AR' AND n.cVouchType = N'50' AND n.cVouchID = N'{key}'"
    return _row(_count(f"SELECT ISNULL(MAX(n.Auto_ID), 0) {frm}"))


def note_proc_sql(no: str) -> str:
    """一行：<该票据的处理行数>|<最近一行（ID 最大）的处理号>；没有处理行时 0|空。
    部分金额请求结果不明时用它找回处理号登记清理（用例登记的票据只有用例自己处理过）。"""
    key = safe_note(no)
    frm = (
        "FROM dbo.AP_Note_Sub AS s INNER JOIN dbo.AP_Note AS n ON n.cLink = s.cLink"
        f" WHERE n.cFlag = N'AR' AND n.cVouchType = N'50' AND n.cVouchID = N'{key}'"
    )
    return _row(_join((_count(f"SELECT COUNT(*) {frm}"), _top("s.cCancelNo", frm + " ORDER BY s.ID DESC"))))


def sub_sql(no: str) -> str:
    """一行（票据处理行 AP_Note_Sub，按处理号）：<行数>|<处理方式>|<金额合计>|<费用合计>|<科目>|<cBank>
    |<cPzID 不空的行数>|<子票起号>|<子票止号>。"""
    key = safe_proc(no)
    frm = f"FROM dbo.AP_Note_Sub AS s WHERE s.cCancelNo = N'{key}'"
    parts = (
        _count(f"SELECT COUNT(*) {frm}"),
        _top("s.cProcStyle", frm),
        _money(f"(SELECT SUM(s.iAmount) {frm})"),
        _money(f"(SELECT SUM(s.iExpense) {frm})"),
        _top("s.cCode", frm),
        _top("s.cBank", frm),
        _count(f"SELECT COUNT(*) {frm} AND ISNULL(s.cPzID, N'') <> N''"),
        _top("s.csbnstart", frm),
        _top("s.csbnend", frm),
    )
    return _row(_join(parts))


def detail_sql(no: str) -> str:
    """一行（按处理号）：<应收明细行数>|<其中票据行（50 / 50、iFlag 3）行数>|<借方合计>|<贷方合计>|<科目>|<单号>
    |<往来单位>|<应付明细行数>|<借方合计>|<贷方合计>|<往来单位>|<来源单据类型>。金额是本币。"""
    key = safe_proc(no)
    ar = f"FROM dbo.Ar_Detail AS d WHERE d.cCancelNo = N'{key}' AND d.cFlag = N'AR'"
    ap = f"FROM dbo.Ap_Detail AS d WHERE d.cCancelNo = N'{key}' AND d.cFlag = N'AP'"
    order = " ORDER BY d.Auto_ID"
    parts = (
        _count(f"SELECT COUNT(*) {ar}"),
        _count(f"SELECT COUNT(*) {ar} AND d.cVouchType = N'50' AND d.cCoVouchType = N'50' AND d.iFlag = 3"),
        _money(f"(SELECT SUM(d.iDAmount) {ar})"),
        _money(f"(SELECT SUM(d.iCAmount) {ar})"),
        _top("d.cCode", ar + order),
        _top("d.cVouchID", ar + order),
        _top("d.cDwCode", ar + order),
        _count(f"SELECT COUNT(*) {ap}"),
        _money(f"(SELECT SUM(d.iDAmount) {ap})"),
        _money(f"(SELECT SUM(d.iCAmount) {ap})"),
        _top("d.cDwCode", ap + order),
        _top("d.cCoVouchType", ap + order),
    )
    return _row(_join(parts))


def gl_sql(pz: str) -> str:
    """一行：<凭证行数>|<凭证张数>|<来源不是 AR / PJ 的行数>|<借方合计>|<贷方合计>。"""
    key = safe_pz(pz)
    bad = (
        "SUM(CASE WHEN ISNULL(g.coutsysname, N'') = N'AR' AND ISNULL(g.coutsign, N'') = N'PJ' THEN 0 ELSE 1 END)"
    )
    vouchers = "COUNT(DISTINCT CONVERT(varchar(4), g.iperiod) + g.csign + CONVERT(varchar(12), g.ino_id))"
    expr = _join((_count("COUNT(*)"), _count(vouchers), _count(f"ISNULL({bad}, 0)"), _money("SUM(g.md)"), _money("SUM(g.mc)")))
    return _row(expr, f"FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{key}'")


def marks_sql(no: str, pz: str) -> str:
    """一行：<该处理号的应收明细行数>|<其中 cPZid = pz 且 cGLSign、iGLno_id、dPZDate 都有值的行数>
    |<票据处理行里 cPzID = pz 的行数>|<GL_accvouch 里 coutno_id = pz 的行数>。"""
    key, ref = safe_proc(no), safe_pz(pz)
    own = f"FROM dbo.Ar_Detail AS d WHERE d.cCancelNo = N'{key}'"
    marked = (
        f"{own} AND d.cPZid = N'{ref}' AND d.cGLSign IS NOT NULL AND d.iGLno_id IS NOT NULL AND d.dPZDate IS NOT NULL"
    )
    parts = (
        _count(f"SELECT COUNT(*) {own}"),
        _count(f"SELECT COUNT(*) {marked}"),
        _count(f"SELECT COUNT(*) FROM dbo.AP_Note_Sub AS s WHERE s.cCancelNo = N'{key}' AND s.cPzID = N'{ref}'"),
        _count(f"SELECT COUNT(*) FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{ref}'"),
    )
    return _row(_join(parts))


def ap_snap_sql(line: int, vendor: str) -> str:
    """一行：<采购发票行 iOriTotal>|<该行在供应商名下的原币余额>|<应付明细上 9E 行数>。"""
    ref, ven = _line(line), safe_lot(vendor)
    parts = (
        _money(f"(SELECT y.iOriTotal FROM dbo.PurBillVouchs AS y WHERE y.ID = {ref})"),
        _money(
            "(SELECT SUM(ISNULL(d.iCAmount_f, 0) - ISNULL(d.iDAmount_f, 0)) FROM dbo.Ap_Detail AS d"
            f" WHERE d.cFlag = N'AP' AND d.cCoVouchType IN (N'01', N'02') AND d.iBVid = {ref}"
            f" AND d.cDwCode = N'{ven}' AND d.iFlag < 3)"
        ),
        _count("SELECT COUNT(*) FROM dbo.Ap_Detail WHERE cProcStyle = N'9E'"),
    )
    return _row(_join(parts))


def split_snap_sql(note_id: int) -> str:
    """一行（按票据主键）：<余额>|<可用区间行数>|<可用区间金额合计>|<最小起号>|<最大止号>
    |<可用区间的校验和（起号、止号、金额）>|<票据处理行数>。"""
    ref = _line(note_id)
    rng = "FROM dbo.Ap_Note_AvailRange AS a WHERE a.cNoteLink = n.cLink"
    parts = (
        _money("n.iRAmount"),
        _count(f"SELECT COUNT(*) {rng}"),
        _money(f"(SELECT SUM(a.iavailamount) {rng})"),
        _top("MIN(a.cavailstart)", rng),
        _top("MAX(a.cavailend)", rng),
        _top("CHECKSUM_AGG(BINARY_CHECKSUM(a.cavailstart, a.cavailend, a.iavailamount))", rng),
        _count("SELECT COUNT(*) FROM dbo.AP_Note_Sub AS s WHERE s.cLink = n.cLink"),
    )
    return _row(_join(parts), f"FROM dbo.AP_Note AS n WHERE n.Auto_ID = {ref} AND n.cFlag = N'AR'")


def notes_samples() -> list[tuple[str, str]]:
    day = "2026-10-02"
    return [
        ("nt-setup", setup_sql(day)),
        ("nt-ap-pick", ap_pick_sql(day)),
        ("nt-split-pick", split_pick_sql(day)),
        ("nt-note", note_sql("E2E2026100209000001")),
        ("nt-note-id", note_id_sql("E2E2026100209000001")),
        ("nt-note-proc", note_proc_sql("E2E2026100209000001")),
        ("nt-sub", sub_sql("PJJAR000000000001")),
        ("nt-detail", detail_sql("PJJAR000000000001")),
        ("nt-gl", gl_sql("AR0000000000001")),
        ("nt-marks", marks_sql("PJJAR000000000001", "AR0000000000001")),
        ("nt-ap-snap", ap_snap_sql(100, "S0001")),
        ("nt-split-snap", split_snap_sql(100)),
    ]
