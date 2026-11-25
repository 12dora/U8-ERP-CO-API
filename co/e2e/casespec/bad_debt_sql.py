"""坏账用例（bad_debt）的挑单、回读，以及坏账准备参数行的临时补建、恢复 SQL。

读语句输出一行 ROW:<值>，多个值用 | 分隔。写语句只有三条，都只动 Ar_BadPara：
- para_insert_sql：登录年度没有参数行时补一行（有别的年度的行就照最新一行抄，iYear 换成登录年度、计提比率 0.005、
  计提方法固定 3、本年计提额 0、计提日期和凭证号为空；一行都没有就用科目表里 1231 下、6701 / 6702 下的末级科目新建），
  回新行的 autoid（没有补就是 0）；
- para_delete_sql：按 autoid 删掉补建的那一行（同时要求年度相同）；
- para_restore_sql：计提取消后 U8 留下的列（iJtAmount、dJtDate、cCancelNo、cProcStyle）按处理前的值改回。
拼进 SQL 的值都先校验：往来单位、单据号、科目只接受 [0-9A-Za-z._-]，处理号只接受 HZAR 加数字，外部业务号只接受 AR 加数字，
行主键只接受正整数，日期只接受 yyyy-mm-dd，金额只接受最多两位小数的数。走用例的 SQL 通道（过 sqlguard），快照还原兜底。
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.arap_proc_sql import _count, _day, _line, _money, _no_contract, _open_period, _row, _sale_head_ok, _sale_open

_NO = re.compile(r"^HZAR[0-9]{1,20}\Z")
_PZ = re.compile(r"^AR[0-9]{1,20}\Z")
_STYLE = re.compile(r"^[0-9A-Za-z]{1,4}\Z")
_CANCEL = re.compile(r"^[0-9A-Za-z]{1,30}\Z")
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
# 空值在回读里写成 ~（与空串区分）。
NULL = "~"
# 参数行回读的列（para_sql 的输出顺序）。
PARA_COLS = (
    "max_id", "max_year", "year_rows", "id", "remain", "jt_amount", "jt_date", "pz", "cancel_no", "proc_style",
    "jt_style", "rate", "hz", "dy",
)
_LEAF = (
    "SELECT TOP (1) LTRIM(RTRIM(c.ccode)) AS code FROM dbo.code AS c WHERE c.iyear = {y} AND c.bend = 1 AND ({like})"
    " AND ISNULL(c.bcus, 0) = 0 AND ISNULL(c.bsup, 0) = 0 AND ISNULL(c.bperson, 0) = 0 AND ISNULL(c.bdept, 0) = 0"
    " AND ISNULL(c.bitem, 0) = 0 ORDER BY c.ccode"
)
_PARA_INS = "INSERT INTO dbo.Ar_BadPara (iJtStyle, nJtRate, nQcYe, cHzCode, cDyCode, dJtDate, iJtAmount, iRemainAmount,"
_PARA_INS += " cProcStyle, cCancelNo, iYear, FormatXML, DataXML)"


# ---- 校验 ----


def safe_no(raw: str) -> str:
    if _NO.fullmatch(raw or "") is None:
        raise ValueError("处理号不合法")
    return raw


def safe_pz(raw: str) -> str:
    if _PZ.fullmatch(raw or "") is None:
        raise ValueError("外部业务号不合法")
    return raw


def _year(year: int) -> int:
    if type(year) is not int or not 1000 <= year <= 9999:
        raise ValueError("年度不合法")
    return year


def _opt(raw: str, pattern: re.Pattern[str], what: str) -> str:
    """恢复用的可空值：~ 写成 NULL，其余按 pattern 校验后加引号。"""
    if raw == NULL:
        return "NULL"
    if pattern.fullmatch(raw or "") is None:
        raise ValueError(what + "不合法")
    return "N'" + raw + "'"


def _opt_money(raw: str) -> str:
    if raw == NULL:
        return "NULL"
    try:
        num = Decimal(raw)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("金额不合法") from exc
    if not num.is_finite() or num != num.quantize(Decimal("0.01")) or abs(num) > Decimal("1000000000000"):
        raise ValueError("金额不合法")
    return format(num.quantize(Decimal("0.01")), "f")


def _nullable(expr: str) -> str:
    return f"ISNULL({expr}, N'{NULL}')"


# ---- 坏账准备参数行 ----


def para_sql(year: int) -> str:
    """一行，列见 PARA_COLS：<最新一行 autoid>|<它的年度>|<该年度行数>|<该年度最新一行 autoid>|<iRemainAmount>
    |<iJtAmount>|<dJtDate>|<cPZID>|<cCancelNo>|<cProcStyle>|<iJtStyle>|<nJtRate>|<cHzCode>|<cDyCode>（空值写 ~）。
    该年度没有行时后十项都是 ~。"""
    y = _year(year)
    cols = " + N'|' + ".join(
        (
            _nullable("CONVERT(varchar(12), p.autoid)"),
            _nullable("CONVERT(varchar(40), CONVERT(decimal(28, 2), p.iRemainAmount))"),
            _nullable("CONVERT(varchar(40), CONVERT(decimal(28, 2), p.iJtAmount))"),
            _nullable("CONVERT(varchar(10), p.dJtDate, 23)"),
            _nullable("LTRIM(RTRIM(p.cPZID))"),
            _nullable("LTRIM(RTRIM(p.cCancelNo))"),
            _nullable("LTRIM(RTRIM(p.cProcStyle))"),
            _nullable("CONVERT(varchar(12), p.iJtStyle)"),
            _nullable("CONVERT(varchar(40), CONVERT(decimal(28, 6), p.nJtRate))"),
            _nullable("LTRIM(RTRIM(p.cHzCode))"),
            _nullable("LTRIM(RTRIM(p.cDyCode))"),
        )
    )
    head = " + N'|' + ".join(
        (
            _nullable("CONVERT(varchar(12), (SELECT MAX(m.autoid) FROM dbo.Ar_BadPara AS m))"),
            _nullable(
                "CONVERT(varchar(12), (SELECT TOP (1) m.iYear FROM dbo.Ar_BadPara AS m ORDER BY m.autoid DESC))"
            ),
            _count(f"SELECT COUNT(*) FROM dbo.Ar_BadPara AS m WHERE m.iYear = {y}"),
        )
    )
    frm = (
        "FROM (SELECT 1 AS one) AS o\n"
        f"OUTER APPLY (SELECT TOP (1) * FROM dbo.Ar_BadPara AS b WHERE b.iYear = {y} ORDER BY b.autoid DESC) AS p"
    )
    return _row(head + " + N'|' + " + cols, frm)


def para_insert_sql(year: int) -> str:
    """登录年度没有参数行时补一行；一行：<新行 autoid>（没补就是 0）。"""
    y = _year(year)
    hz = _LEAF.format(y=y, like="c.ccode LIKE N'1231%'")
    dy = _LEAF.format(y=y, like="c.ccode LIKE N'6701%' OR c.ccode LIKE N'6702%'")
    return f"""SET NOCOUNT ON;
DECLARE @id int;
SET @id = 0;
IF NOT EXISTS (SELECT 1 FROM dbo.Ar_BadPara WHERE iYear = {y})
BEGIN
  IF EXISTS (SELECT 1 FROM dbo.Ar_BadPara)
    {_PARA_INS}
    SELECT TOP (1) 3, 0.005, p.nQcYe, p.cHzCode, p.cDyCode, NULL, 0, p.iRemainAmount, N'9F', p.cCancelNo, {y}, p.FormatXML, p.DataXML
    FROM dbo.Ar_BadPara AS p ORDER BY p.autoid DESC;
  ELSE
    {_PARA_INS}
    SELECT 3, 0.005, 0, hz.code, dy.code, NULL, 0, 0, N'9F', N'', {y}, NULL, NULL
    FROM ({hz}) AS hz CROSS JOIN ({dy}) AS dy;
  SET @id = ISNULL(CONVERT(int, SCOPE_IDENTITY()), 0);
END
SELECT N'ROW:' + CONVERT(varchar(12), @id) AS v;
"""


def para_delete_sql(autoid: int, year: int) -> str:
    """删掉补建的参数行；一行：<删除行数>。"""
    return (
        "SET NOCOUNT ON;\n"
        f"DELETE FROM dbo.Ar_BadPara WHERE autoid = {_line(autoid)} AND iYear = {_year(year)};\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def para_restore_sql(autoid: int, saved: dict[str, str]) -> str:
    """把计提取消后 U8 留下的四列改回处理前的值（saved 是 para_sql 的回读，空值 ~）；一行：<改动行数>。"""
    sets = ", ".join(
        (
            "iJtAmount = " + _opt_money(saved["jt_amount"]),
            "dJtDate = " + _opt(saved["jt_date"], _DATE, "计提日期"),
            "cCancelNo = " + _opt(saved["cancel_no"], _CANCEL, "处理号"),
            "cProcStyle = " + _opt(saved["proc_style"], _STYLE, "处理方式"),
        )
    )
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE dbo.Ar_BadPara SET {sets} WHERE autoid = {_line(autoid)};\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


# ---- 坏账发生（9G） ----


def occur_pick_sql(day: str) -> str:
    """一行：<客户>|<发票类型>|<发票号>|<发票行 AutoID>|<可处理余额>|<应收科目>。

    已审核、本位币、不受审批流控制的蓝字销售发票（26 / 27）行：往来明细余额和 iNatSum − iMoneySum 都不小于 10，
    没有关联合同、不是现结；日期不晚于 day，day 所在期间应收、应付都未结账。可处理余额取两者中小的。
    """
    text, _y, _m = _day(day)
    ctrl = (
        "(SELECT TOP (1) LTRIM(RTRIM(a.cCode)) FROM dbo.Ar_Detail AS a WHERE a.cFlag = N'AR' AND a.cVouchType = s.vt"
        " AND a.cVouchID = s.code AND a.cProcStyle = a.cVouchType ORDER BY a.Auto_ID)"
    )
    return f"""SET NOCOUNT ON;
WITH s AS (
  {_sale_open("", "10")}
)
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(s.dw)) + N'|' + s.vt + N'|' + s.code + N'|' + CONVERT(varchar(20), s.line)
  + N'|' + CONVERT(varchar(40), CONVERT(decimal(28, 2), CASE WHEN n.free < s.rem THEN n.free ELSE s.rem END))
  + N'|' + k.code AS v
FROM s
INNER JOIN dbo.SaleBillVouch AS sh ON sh.cSBVCode = s.code AND sh.cVouchType = s.vt
INNER JOIN dbo.SaleBillVouchs AS sb ON sb.AutoID = s.line AND sb.SBVID = sh.SBVID
CROSS APPLY (SELECT ISNULL(sb.iNatSum, 0) - ISNULL(sb.iMoneySum, 0) AS free) AS n
CROSS APPLY (SELECT {ctrl} AS code) AS k
WHERE sh.cCusCode = s.dw AND {_sale_head_ok("sh", text)} AND n.free >= 10
  AND k.code IS NOT NULL AND k.code NOT LIKE N'%[^0-9A-Za-z._-]%' AND LTRIM(RTRIM(s.dw)) NOT LIKE N'%[^0-9A-Za-z._-]%'
  AND {_no_contract("Ar_Detail", "AR", "s.vt", "s.code")}
  AND NOT EXISTS (SELECT 1 FROM dbo.Ar_Detail AS x WHERE x.cFlag = N'AR' AND x.cVouchID = s.code
    AND x.cVouchType = s.vt AND x.cProcStyle = N'XJ')
  AND {_open_period(text)}
ORDER BY sh.SBVID DESC, s.line;
"""


def _cus_balance(cus: str, column: str = "") -> str:
    return (
        f"(SELECT SUM(ISNULL(d.iDAmount{column}, 0) - ISNULL(d.iCAmount{column}, 0)) FROM dbo.Ar_Detail AS d"
        f" WHERE d.cFlag = N'AR' AND d.cDwCode = N'{cus}' AND d.iFlag < 3)"
    )


def _remain(autoid: int) -> str:
    return _money(f"(SELECT p.iRemainAmount FROM dbo.Ar_BadPara AS p WHERE p.autoid = {_line(autoid)})")


def occur_snap_sql(line: int, customer: str, para: int) -> str:
    """一行：<发票行 iMoneySum>|<iExchSum>|<客户应收余额（本币，iFlag<3）>|<该行 9G 行数>|<坏账准备余额>。"""
    ref, cus = _line(line), safe_lot(customer)
    acc = "(SELECT x.{col} FROM dbo.SaleBillVouchs AS x WHERE x.AutoID = " + str(ref) + ")"
    expr = " + N'|' + ".join(
        (
            _money(acc.format(col="iMoneySum")),
            _money(acc.format(col="iExchSum")),
            _money(_cus_balance(cus)),
            _count(f"SELECT COUNT(*) FROM dbo.Ar_Detail AS d WHERE d.cProcStyle = N'9G' AND d.iBVid = {ref}"),
            _remain(para),
        )
    )
    return _row(expr)


def occur_rows_sql(no: str, line: int, ctrl: str) -> str:
    """一行：<该处理号的往来明细行数>|<贷方本币>|<贷方原币>|<借方本币>|<处理方式不是 9G 的行数>|<挂在该发票行的行数>
    |<科目是 ctrl 的行数>。"""
    key, ref, code = safe_no(no), _line(line), safe_lot(ctrl)
    frm = f"FROM dbo.Ar_Detail AS d WHERE d.cCancelNo = N'{key}'"
    expr = " + N'|' + ".join(
        (
            _count("COUNT(*)"),
            _money("SUM(d.iCAmount)"),
            _money("SUM(d.iCAmount_f)"),
            _money("SUM(d.iDAmount)"),
            _count("ISNULL(SUM(CASE WHEN d.cProcStyle = N'9G' THEN 0 ELSE 1 END), 0)"),
            _count(f"ISNULL(SUM(CASE WHEN d.iBVid = {ref} THEN 1 ELSE 0 END), 0)"),
            _count(f"ISNULL(SUM(CASE WHEN LTRIM(RTRIM(d.cCode)) = N'{code}' THEN 1 ELSE 0 END), 0)"),
        )
    )
    return _row(expr, frm)


# ---- 坏账收回（9H） ----


def receipt_sql(receipt: int) -> str:
    """一行：<收款单号>|<表头结算科目>|<表体金额合计>|<表体行数>。"""
    ref = _line(receipt)
    expr = (
        "LTRIM(RTRIM(h.cVouchID)) + N'|' + ISNULL(LTRIM(RTRIM(h.cCode)), N'') + N'|' + "
        + _money("(SELECT SUM(b.iAmt) FROM dbo.Ap_CloseBills AS b WHERE b.iID = h.iID)")
        + " + N'|' + "
        + _count("SELECT COUNT(*) FROM dbo.Ap_CloseBills AS b WHERE b.iID = h.iID")
    )
    return _row(expr, f"FROM dbo.Ap_CloseBill AS h WHERE h.iID = {ref} AND h.cFlag = N'AR' AND h.cVouchType = N'48'")


def recover_snap_sql(receipt: int, code: str, customer: str, para: int) -> str:
    """一行：<客户应收余额本币>|<原币>|<收款单上的往来明细行数>|<其中 9H 行数>|<9H 借方本币合计>|<收款单行余额 iRAmt 合计>
    |<收款单行金额 iAmt 合计>|<已审核 1 / 未审核 0>|<坏账准备余额>。"""
    ref, doc, cus = _line(receipt), safe_lot(code), safe_lot(customer)
    on = f"FROM dbo.Ar_Detail AS d WHERE d.cFlag = N'AR' AND d.cVouchType = N'48' AND d.cVouchID = N'{doc}'"
    lines = f"FROM dbo.Ap_CloseBills AS b WHERE b.iID = {ref}"
    checked = (
        f"SELECT COUNT(*) FROM dbo.Ap_CloseBill AS h WHERE h.iID = {ref}"
        " AND LTRIM(RTRIM(ISNULL(h.cCheckMan, N''))) <> N''"
    )
    expr = " + N'|' + ".join(
        (
            _money(_cus_balance(cus)),
            _money(_cus_balance(cus, "_f")),
            _count(f"SELECT COUNT(*) {on}"),
            _count(f"SELECT COUNT(*) {on} AND d.cProcStyle = N'9H'"),
            _money(f"(SELECT SUM(d.iDAmount) {on} AND d.cProcStyle = N'9H')"),
            _money(f"(SELECT SUM(b.iRAmt) {lines})"),
            _money(f"(SELECT SUM(b.iAmt) {lines})"),
            _count(checked),
            _remain(para),
        )
    )
    return _row(expr)


def receipt_gone_sql(receipt: int) -> str:
    """一行：<收款单表头行数>（删除后应为 0）。"""
    return _row(_count(f"SELECT COUNT(*) FROM dbo.Ap_CloseBill AS h WHERE h.iID = {_line(receipt)}"))


def cash_item_sql() -> str:
    """一行：未关闭、名称含「收到」的第一个现金流量项目编码；没有时为空。"""
    inner = (
        "SELECT TOP (1) LTRIM(RTRIM(f.citemcode)) FROM dbo.fitemss98 AS f WHERE ISNULL(f.bclose, 0) = 0"
        " AND f.citemname LIKE N'%收到%' AND LTRIM(RTRIM(f.citemcode)) NOT LIKE N'%[^0-9A-Za-z._-]%' ORDER BY f.citemcode"
    )
    return _row(f"ISNULL(({inner}), N'')")


# ---- 制单 ----


def bad_gl_sql(pz: str) -> str:
    """一行：<凭证行数>|<凭证张数>|<来源不是 AR / JT 的行数>|<借方合计>|<贷方合计>。"""
    key = safe_pz(pz)
    bad = "SUM(CASE WHEN ISNULL(g.coutsysname, N'') = N'AR' AND ISNULL(g.coutsign, N'') = N'JT' THEN 0 ELSE 1 END)"
    vouchers = "COUNT(DISTINCT CONVERT(varchar(4), g.iperiod) + g.csign + CONVERT(varchar(12), g.ino_id))"
    expr = " + N'|' + ".join(
        (_count("COUNT(*)"), _count(vouchers), _count(f"ISNULL({bad}, 0)"), _money("SUM(g.md)"), _money("SUM(g.mc)"))
    )
    return _row(expr, f"FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{key}'")


def bad_marks_sql(no: str, pz: str) -> str:
    """一行：<该处理号的 9G / 9H 往来明细行数>|<其中 cPZid = pz 且 cGLSign、iGLno_id、dPZDate 都有值的行数>
    |<往来明细里 cPZid = pz 的行数>|<参数行里 cPZID = pz 的行数>|<GL_accvouch 里 coutno_id = pz 的行数>。"""
    key, ref = safe_no(no), safe_pz(pz)
    own = f"FROM dbo.Ar_Detail AS d WHERE d.cCancelNo = N'{key}' AND d.cProcStyle IN (N'9G', N'9H')"
    marked = f" AND d.cPZid = N'{ref}' AND d.cGLSign IS NOT NULL AND d.iGLno_id IS NOT NULL AND d.dPZDate IS NOT NULL"
    expr = " + N'|' + ".join(
        (
            _count(f"SELECT COUNT(*) {own}"),
            _count(f"SELECT COUNT(*) {own}{marked}"),
            _count(f"SELECT COUNT(*) FROM dbo.Ar_Detail AS d WHERE d.cPZid = N'{ref}'"),
            _count(f"SELECT COUNT(*) FROM dbo.Ar_BadPara AS p WHERE p.cPZID = N'{ref}'"),
            _count(f"SELECT COUNT(*) FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{ref}'"),
        )
    )
    return _row(expr)


def bad_debt_samples() -> list[tuple[str, str]]:
    day = "2026-10-02"
    saved = dict.fromkeys(PARA_COLS, NULL)
    saved.update(jt_amount="12.50", jt_date="2026-09-30", cancel_no="HZAR0000000000001", proc_style="9F")
    return [
        ("bd-para", para_sql(2026)),
        ("bd-para-insert", para_insert_sql(2026)),
        ("bd-para-delete", para_delete_sql(100, 2026)),
        ("bd-para-restore", para_restore_sql(100, saved)),
        ("bd-occur-pick", occur_pick_sql(day)),
        ("bd-occur-snap", occur_snap_sql(100, "C0001", 7)),
        ("bd-occur-rows", occur_rows_sql("HZAR0000000000001", 100, "112201")),
        ("bd-receipt", receipt_sql(100)),
        ("bd-recover-snap", recover_snap_sql(100, "0000000012", "C0001", 7)),
        ("bd-receipt-gone", receipt_gone_sql(100)),
        ("bd-cash-item", cash_item_sql()),
        ("bd-gl", bad_gl_sql("AR0000000000001")),
        ("bd-marks", bad_marks_sql("HZAR0000000000001", "AR0000000000001")),
    ]
