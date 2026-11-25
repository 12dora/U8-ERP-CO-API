"""应收应付处理用例（arap_proc：转账、并账，以及处理制单的共用回读）的挑单与回读 SQL。

只读；表名、列名是常量。拼进 SQL 的值都先校验：往来单位编码、单据号只接受 [0-9A-Za-z._-]（safe_lot），
行主键只接受正整数，处理号只接受登记过的前缀加数字，外部业务号只接受 AR / AP 加数字，日期只接受 yyyy-mm-dd，
币种名只接受字母、数字和汉字，金额只接受最多两位小数的非负数。每条 SQL 输出一行 ROW:<值>，多个值用 | 分隔。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation

from co.e2e.casespec.stock_plus_sql import safe_lot

_INT_MAX = 2147483647
_DAY = re.compile(r"^([0-9]{4})-([0-9]{2})-[0-9]{2}\Z")
_NO = re.compile(r"^(YCFAP|FCYAR|BZAR|BZAP|HRAR|HPAP|SYRAR|SYPAP)[0-9]{1,20}\Z")
_PZ = re.compile(r"^(AR|AP)[0-9]{1,20}\Z")
_CUR = re.compile(r"^[0-9A-Za-z一-鿿]{1,20}\Z")
_STYLES = ("9I", "9J", "BZ", "9N", "9M")
_SIDES = ("AR", "AP")
_GL_COLS = ("ccus_id", "csup_id", "ccode")
_HOME = "(SELECT TOP (1) f.cexch_name FROM dbo.foreigncurrency AS f WHERE f.iotherused = -1)"
_PROC_LIST = "N'9I', N'9J', N'BZ', N'9N', N'9M'"


# ---- 校验 ----


def safe_no(raw: str) -> str:
    if _NO.fullmatch(raw or "") is None:
        raise ValueError("处理号不合法")
    return raw


def safe_pz(raw: str) -> str:
    if _PZ.fullmatch(raw or "") is None:
        raise ValueError("外部业务号不合法")
    return raw


def safe_cur(raw: str) -> str:
    if _CUR.fullmatch(raw or "") is None:
        raise ValueError("币种名不合法")
    return raw


def _line(value: int) -> int:
    if type(value) is not int or not 1 <= value <= _INT_MAX:
        raise ValueError("行主键不合法")
    return value


def _day(day: str) -> tuple[str, int, int]:
    found = _DAY.fullmatch(day or "")
    if found is None:
        raise ValueError("日期不合法")
    return day, int(found.group(1)), int(found.group(2))


def _amount(value: Decimal) -> str:
    try:
        num = Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("金额不合法") from exc
    if not num.is_finite() or num < 0 or num != num.quantize(Decimal("0.01")) or num > Decimal("1000000000000"):
        raise ValueError("金额不合法")
    return format(num.quantize(Decimal("0.01")), "f")


def _nos(nos: Sequence[str]) -> str:
    if not nos or len(nos) > 200:
        raise ValueError("处理号清单不合法")
    return ", ".join("N'" + safe_no(no) + "'" for no in nos)


# ---- 片段 ----


def _row(expr: str, frm: str = "") -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v" + ("\n" + frm if frm else "") + ";\n"


def _money(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(28, 2), ISNULL({expr}, 0)))"


def _count(expr: str) -> str:
    return f"CONVERT(varchar(12), ({expr}))"


def _open_period(day: str) -> str:
    # 该日期所在期间应收、应付都未结账（GL_mend 的 bflag_AR / bflag_AP）。
    _text, year, month = _day(day)
    return (
        f"NOT EXISTS (SELECT 1 FROM dbo.GL_mend AS m WHERE m.iyear = {year} AND m.iperiod = {month}"
        " AND (ISNULL(m.bflag_AR, 0) <> 0 OR ISNULL(m.bflag_AP, 0) <> 0))"
    )


def _no_contract(table: str, flag: str, vtype: str, code: str) -> str:
    # 审核行（cProcStyle = cVouchType）没有关联合同（桥拒绝带合同的单据转账）。
    return (
        f"NOT EXISTS (SELECT 1 FROM dbo.{table} AS g WHERE g.cFlag = N'{flag}' AND g.cVouchType = {vtype}"
        f" AND g.cVouchID = {code} AND g.cProcStyle = g.cVouchType AND ISNULL(g.cContractID, N'') <> N'')"
    )


def _sale_open(where: str, least: str) -> str:
    # 应收往来明细上销售发票行（iBVid）的原币余额（借 − 贷，iFlag < 3）不小于 least 的行。
    return (
        "SELECT d.cDwCode AS dw, d.cCoVouchType AS vt, d.cCoVouchID AS code, d.iBVid AS line,"
        " SUM(ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0)) AS rem\n"
        "  FROM dbo.Ar_Detail AS d\n"
        f"  WHERE d.cFlag = N'AR' AND d.cCoVouchType IN (N'26', N'27') AND d.iFlag < 3 AND ISNULL(d.iBVid, 0) > 0{where}\n"
        "  GROUP BY d.cDwCode, d.cCoVouchType, d.cCoVouchID, d.iBVid\n"
        f"  HAVING SUM(ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0)) >= {least}"
    )


def _sale_head_ok(alias: str, day: str) -> str:
    # 蓝字、已应收审核、不受审批流控制、本位币汇率 1、日期不晚于 day 的销售发票表头。
    return (
        f"LTRIM(RTRIM(ISNULL({alias}.cVerifier, N''))) <> N'' AND ISNULL({alias}.iswfcontrolled, 0) = 0"
        f" AND ISNULL({alias}.bReturnFlag, 0) = 0 AND ISNULL({alias}.cexch_name, {_HOME}) = {_HOME}"
        f" AND ISNULL({alias}.iExchRate, 1) = 1 AND {alias}.dDate <= '{day}'"
    )


# ---- 应收冲应付 / 应付冲应收（9I / 9J） ----


def transfer_pick_sql(day: str) -> str:
    """一行：<客户>|<销售发票号>|<发票行 AutoID>|<供应商>|<采购发票号>|<发票行 ID>。

    已审核、本位币、余额 >= 2 的蓝字销售专用发票（26）行和采购专用发票（01）行，都不受审批流控制、没有网络锁、
    没有关联合同，日期不晚于 day，day 所在期间应收、应付都未结账。

    两侧之间没有关联条件，各自取一行（销售发票取最新的 SBVID，采购发票取最新的 PBVID），不做两侧全部行的笛卡尔积。
    """
    text, _year, _month = _day(day)
    return f"""SET NOCOUNT ON;
WITH s AS (
  {_sale_open("", "2")}
), p AS (
  SELECT d.cDwCode AS dw, d.cCoVouchID AS code, d.iBVid AS line
  FROM dbo.Ap_Detail AS d
  WHERE d.cFlag = N'AP' AND d.cCoVouchType = N'01' AND d.iFlag < 3 AND ISNULL(d.iBVid, 0) > 0
  GROUP BY d.cDwCode, d.cCoVouchID, d.iBVid
  HAVING SUM(ISNULL(d.iCAmount_f, 0) - ISNULL(d.iDAmount_f, 0)) >= 2
), sp AS (
  SELECT TOP (1) LTRIM(RTRIM(s.dw)) + N'|' + s.code + N'|' + CONVERT(varchar(20), s.line) AS v
  FROM s
  INNER JOIN dbo.SaleBillVouch AS sh ON sh.cSBVCode = s.code AND sh.cVouchType = s.vt
  INNER JOIN dbo.SaleBillVouchs AS sb ON sb.AutoID = s.line AND sb.SBVID = sh.SBVID
  WHERE s.vt = N'26' AND sh.cCusCode = s.dw AND {_sale_head_ok("sh", text)}
    AND {_no_contract("Ar_Detail", "AR", "N'26'", "s.code")}
  ORDER BY sh.SBVID DESC
), pp AS (
  SELECT TOP (1) LTRIM(RTRIM(p.dw)) + N'|' + p.code + N'|' + CONVERT(varchar(20), p.line) AS v
  FROM p
  INNER JOIN dbo.PurBillVouch AS ph ON ph.cPBVCode = p.code AND ph.cPBVBillType = N'01'
  INNER JOIN dbo.PurBillVouchs AS pb ON pb.ID = p.line AND pb.PBVID = ph.PBVID
  WHERE ph.cVenCode = p.dw AND LTRIM(RTRIM(ISNULL(ph.cPBVVerifier, N''))) <> N'' AND ISNULL(ph.IsWfControlled, 0) = 0
    AND ISNULL(ph.iNetLock, 0) = 0 AND ISNULL(ph.cexch_name, {_HOME}) = {_HOME} AND ph.dPBVDate <= '{text}'
    AND {_no_contract("Ap_Detail", "AP", "N'01'", "p.code")}
  ORDER BY ph.PBVID DESC
)
SELECT N'ROW:' + sp.v + N'|' + pp.v AS v
FROM sp CROSS JOIN pp
WHERE {_open_period(text)};
"""


def transfer_snap_sql(sale_line: int, pur_line: int, customer: str, vendor: str) -> str:
    """一行：<销售发票行 iExchSum>|<采购发票行 iOriTotal>|<销售发票行在客户名下的原币余额>
    |<采购发票行在供应商名下的原币余额>|<两张往来明细上 9I、9J 行数合计>。"""
    sl, pl = _line(sale_line), _line(pur_line)
    cus, ven = safe_lot(customer), safe_lot(vendor)
    procs = (
        "(SELECT COUNT(*) FROM dbo.Ar_Detail WHERE cProcStyle IN (N'9I', N'9J'))"
        " + (SELECT COUNT(*) FROM dbo.Ap_Detail WHERE cProcStyle IN (N'9I', N'9J'))"
    )
    expr = (
        _money(f"(SELECT x.iExchSum FROM dbo.SaleBillVouchs AS x WHERE x.AutoID = {sl})")
        + " + N'|' + "
        + _money(f"(SELECT y.iOriTotal FROM dbo.PurBillVouchs AS y WHERE y.ID = {pl})")
        + " + N'|' + "
        + _money(
            "(SELECT SUM(ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0)) FROM dbo.Ar_Detail AS d"
            f" WHERE d.cFlag = N'AR' AND d.cCoVouchType IN (N'26', N'27') AND d.iBVid = {sl}"
            f" AND d.cDwCode = N'{cus}' AND d.iFlag < 3)"
        )
        + " + N'|' + "
        + _money(
            "(SELECT SUM(ISNULL(d.iCAmount_f, 0) - ISNULL(d.iDAmount_f, 0)) FROM dbo.Ap_Detail AS d"
            f" WHERE d.cFlag = N'AP' AND d.cCoVouchType IN (N'01', N'02') AND d.iBVid = {pl}"
            f" AND d.cDwCode = N'{ven}' AND d.iFlag < 3)"
        )
        + " + N'|' + "
        + _count(procs)
    )
    return _row(expr)


def proc_rows_sql(no: str, style: str) -> str:
    """一行：<应收明细行数>|<借方原币合计>|<贷方原币合计>|<应付明细行数>|<借方原币合计>|<贷方原币合计>
    |<处理方式不是 style 的行数>|<应收一侧第一行摘要（| 换成 /）>。按处理号 cCancelNo 找。"""
    key = safe_no(no)
    if style not in _STYLES:
        raise ValueError("处理方式不对")
    parts = []
    for table in ("Ar_Detail", "Ap_Detail"):
        frm = f"FROM dbo.{table} AS d WHERE d.cCancelNo = N'{key}'"
        parts.append(_count(f"SELECT COUNT(*) {frm}"))
        parts.append(_money(f"(SELECT SUM(d.iDAmount_f) {frm})"))
        parts.append(_money(f"(SELECT SUM(d.iCAmount_f) {frm})"))
    bad = (
        f"(SELECT COUNT(*) FROM dbo.Ar_Detail AS d WHERE d.cCancelNo = N'{key}' AND ISNULL(d.cProcStyle, N'') <> N'{style}')"
        f" + (SELECT COUNT(*) FROM dbo.Ap_Detail AS d WHERE d.cCancelNo = N'{key}'"
        f" AND ISNULL(d.cProcStyle, N'') <> N'{style}')"
    )
    parts.append(_count(bad))
    parts.append(
        "ISNULL((SELECT TOP (1) REPLACE(ISNULL(d.cDigest, N''), N'|', N'/') FROM dbo.Ar_Detail AS d"
        f" WHERE d.cCancelNo = N'{key}' ORDER BY d.Auto_ID), N'')"
    )
    return _row(" + N'|' + ".join(parts))


# ---- 处理制单与取消制单 ----


def marks_sql(nos: Sequence[str], pz: str) -> str:
    """一行：<这些处理号的往来明细行数>|<其中 cPZid = pz 且 cGLSign、iGLno_id、dPZDate 都有值的行数>
    |<两张往来明细里 cPZid = pz 的行数>|<GL_accvouch 里 coutno_id = pz 的行数>。"""
    keys, key = _nos(nos), safe_pz(pz)
    own = "FROM dbo.{t} AS d WHERE d.cCancelNo IN (" + keys + ") AND d.cProcStyle IN (" + _PROC_LIST + ")"
    marked = (
        own + f" AND d.cPZid = N'{key}' AND d.cGLSign IS NOT NULL AND d.iGLno_id IS NOT NULL AND d.dPZDate IS NOT NULL"
    )
    refs = "FROM dbo.{t} AS d WHERE d.cPZid = N'" + key + "'"

    def both(frm: str) -> str:
        return _count(
            "(SELECT COUNT(*) " + frm.format(t="Ar_Detail") + ") + (SELECT COUNT(*) " + frm.format(t="Ap_Detail") + ")"
        )

    gl = _count(f"SELECT COUNT(*) FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{key}'")
    return _row(" + N'|' + ".join((both(own), both(marked), both(refs), gl)))


def gl_sql(pz: str, system: str, sign: str) -> str:
    """一行：<凭证行数>|<凭证张数>|<coutsysname 不是 system 或 coutsign 不是 sign 的行数>|<借方合计>|<贷方合计>。"""
    key = safe_pz(pz)
    if system not in _SIDES or sign not in ("ZZ", "BZ", "SY", "AR", "AP"):
        raise ValueError("凭证来源不对")
    bad = (
        f"SUM(CASE WHEN ISNULL(g.coutsysname, N'') = N'{system}' AND ISNULL(g.coutsign, N'') = N'{sign}'"
        " THEN 0 ELSE 1 END)"
    )
    vouchers = "COUNT(DISTINCT CONVERT(varchar(4), g.iperiod) + g.csign + CONVERT(varchar(12), g.ino_id))"
    expr = " + N'|' + ".join(
        (_count("COUNT(*)"), _count(vouchers), _count(f"ISNULL({bad}, 0)"), _money("SUM(g.md)"), _money("SUM(g.mc)"))
    )
    return _row(expr, f"FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{key}'")


def gl_net_sql(pz: str, column: str, value: str) -> str:
    """一行：<该列等于 value 的凭证行借方减贷方合计>|<行数>|<最小科目编码>|<最大科目编码>。column 是 ccus_id、csup_id 或 ccode。"""
    key = safe_pz(pz)
    if column not in _GL_COLS:
        raise ValueError("凭证列不对")
    code = safe_lot(value)
    expr = " + N'|' + ".join(
        (
            _money("SUM(ISNULL(g.md, 0) - ISNULL(g.mc, 0))"),
            _count("COUNT(*)"),
            "ISNULL(MIN(LTRIM(RTRIM(g.ccode))), N'')",
            "ISNULL(MAX(LTRIM(RTRIM(g.ccode))), N'')",
        )
    )
    frm = f"FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{key}' AND LTRIM(RTRIM(ISNULL(g.{column}, N''))) = N'{code}'"
    return _row(expr, frm)


# ---- 并账（BZ） ----


def merge_pick_sql(day: str) -> str:
    """一行：<原客户>|<销售发票号>|<发票行 AutoID>|<并入客户>。

    已审核、本位币、余额 >= 2 的蓝字销售专用发票行（同 transfer_pick_sql 的口径），并入客户是编码最小的另一个
    未停用客户；day 所在期间应收、应付都未结账。
    """
    text, _year, _month = _day(day)
    return f"""SET NOCOUNT ON;
WITH s AS (
  {_sale_open("", "2")}
)
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(s.dw)) + N'|' + s.code + N'|' + CONVERT(varchar(20), s.line) + N'|' + t.code AS v
FROM s
INNER JOIN dbo.SaleBillVouch AS sh ON sh.cSBVCode = s.code AND sh.cVouchType = s.vt
INNER JOIN dbo.SaleBillVouchs AS sb ON sb.AutoID = s.line AND sb.SBVID = sh.SBVID
CROSS APPLY (SELECT TOP (1) LTRIM(RTRIM(c.cCusCode)) AS code FROM dbo.Customer AS c
  WHERE c.cCusCode <> s.dw AND c.dEndDate IS NULL ORDER BY c.cCusCode) AS t
WHERE s.vt = N'26' AND sh.cCusCode = s.dw AND {_sale_head_ok("sh", text)}
  AND {_open_period(text)}
ORDER BY sh.SBVID DESC;
"""


def merge_snap_sql(line: int, source: str, target: str) -> str:
    """一行：<发票行在原客户名下的原币余额>|<在并入客户名下的原币余额>|<应收明细上 BZ 行数>。"""
    ref = _line(line)
    rem = (
        "(SELECT SUM(ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0)) FROM dbo.Ar_Detail AS d"
        f" WHERE d.cFlag = N'AR' AND d.cCoVouchType IN (N'26', N'27') AND d.iBVid = {ref}"
        " AND d.cDwCode = N'{dw}' AND d.iFlag < 3)"
    )
    expr = " + N'|' + ".join(
        (
            _money(rem.format(dw=safe_lot(source))),
            _money(rem.format(dw=safe_lot(target))),
            _count("SELECT COUNT(*) FROM dbo.Ar_Detail WHERE cProcStyle = N'BZ'"),
        )
    )
    return _row(expr)


def merge_rows_sql(no: str, source: str, target: str) -> str:
    """一行：<该并账号的应收明细行数>|<原客户行借方原币合计>|<并入客户行借方原币合计>|<不同 iBVid 个数>|<最小 iBVid>。"""
    key = safe_no(no)
    src, dst = safe_lot(source), safe_lot(target)
    frm = f"FROM dbo.Ar_Detail AS d WHERE d.cCancelNo = N'{key}' AND d.cProcStyle = N'BZ'"
    expr = " + N'|' + ".join(
        (
            _count("COUNT(*)"),
            _money(f"SUM(CASE WHEN d.cDwCode = N'{src}' THEN d.iDAmount_f ELSE 0 END)"),
            _money(f"SUM(CASE WHEN d.cDwCode = N'{dst}' THEN d.iDAmount_f ELSE 0 END)"),
            _count("COUNT(DISTINCT d.iBVid)"),
            _count("ISNULL(MIN(d.iBVid), 0)"),
        )
    )
    return _row(expr, frm)


def arap_proc_samples() -> list[tuple[str, str]]:
    day = "2026-10-02"
    return [
        ("apx-transfer-pick", transfer_pick_sql(day)),
        ("apx-transfer-snap", transfer_snap_sql(100, 200, "C0001", "S0001")),
        ("apx-proc-rows", proc_rows_sql("YCFAP000000000001", "9I")),
        ("apx-marks", marks_sql(("YCFAP000000000001",), "AR0000000000001")),
        ("apx-gl", gl_sql("AR0000000000001", "AR", "ZZ")),
        ("apx-gl-net", gl_net_sql("AR0000000000001", "ccus_id", "C0001")),
        ("apx-merge-pick", merge_pick_sql(day)),
        ("apx-merge-snap", merge_snap_sql(100, "C0001", "C0002")),
        ("apx-merge-rows", merge_rows_sql("BZAR0000000000001", "C0001", "C0002")),
    ]
