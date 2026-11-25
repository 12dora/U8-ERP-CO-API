"""应收应付处理用例的后两类（arap_proc_more：红票对冲、汇兑损益）的挑单与回读 SQL。

校验规则与输出格式同 arap_proc_sql（共用它的校验和片段）。arap_proc_more_samples() 进样例 SQL。
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.arap_proc_sql import (
    _HOME,
    _amount,
    _count,
    _day,
    _line,
    _money,
    _nos,
    _open_period,
    _row,
    _sale_head_ok,
    _sale_open,
    safe_cur,
    safe_no,
)
from co.e2e.casespec.flow_pick_sql import no_gathering_plan

# ---- 红票对冲（9N） ----


def red_src_sql(day: str) -> str:
    """一行：<发货单 DLID>|<发货行 iDLsID>|<客户>。

    同 sale_red_invoice.rs_src_sql 的口径（已审核、未关闭、挂已开票订单行、可按已开票退 1 个的蓝字发货行），
    另要求本位币汇率 1，且该客户有一行已审核、本位币、余额不小于这一行单价（含税，向上取整）加 1 的蓝字销售发票。
    """
    text, _year, _month = _day(day)
    blue = _sale_open(" AND d.cDwCode = h.cCusCode", "CEILING(ABS(ISNULL(x.iSum, 0)) / NULLIF(x.iQuantity, 0)) + 1")
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.DLID) + N'|' + CONVERT(varchar(20), x.iDLsID)
  + N'|' + LTRIM(RTRIM(h.cCusCode)) AS v
FROM dbo.DispatchList AS h
JOIN dbo.DispatchLists AS x ON x.DLID = h.DLID
JOIN dbo.SO_SODetails AS so ON so.iSOsID = x.iSOsID
CROSS APPLY (SELECT ISNULL(x.iQuantity, 0) AS q, ISNULL(x.iSettleQuantity, 0) AS s, ISNULL(x.fretqtywkp, 0) AS w,
  ISNULL(x.fretqtyykp, 0) AS y, ISNULL(x.iRetQuantity, 0) AS r) AS n
WHERE h.cVouchType = N'05'
  AND ISNULL(h.bReturnFlag, 0) = 0
  AND ISNULL(h.bFirst, 0) = 0
  AND LTRIM(RTRIM(ISNULL(h.cVerifier, N''))) <> N''
  AND LTRIM(RTRIM(ISNULL(h.cCloser, N''))) = N''
  AND {no_gathering_plan("h")}
  AND LTRIM(RTRIM(ISNULL(x.cSCloser, N''))) = N''
  AND LTRIM(RTRIM(ISNULL(so.cSCloser, N''))) = N''
  AND ISNULL(so.iFHQuantity, 0) >= 1
  AND ISNULL(so.iKPQuantity, 0) >= 1
  AND ISNULL(x.iQuantity, 0) > 0
  AND n.q - n.r >= 1
  AND (CASE WHEN n.s - (n.q - n.w) > 0 THEN n.q - n.w ELSE n.s END) - n.y >= 1
  AND ISNULL(h.cexch_name, {_HOME}) = {_HOME} AND ISNULL(h.iExchRate, 1) = 1
  AND EXISTS (
  {blue}
  )
  AND {_open_period(text)}
ORDER BY h.DLID DESC, x.iDLsID;
"""


def red_line_sql(invoice: int) -> str:
    """一行：<红字发票类型>|<发票号>|<发票行 AutoID>|<该行未对冲的原币金额（正数）>。发票只有一行（取最小 AutoID）。"""
    ref = _line(invoice)
    rem = (
        "(SELECT SUM(ISNULL(d.iCAmount_f, 0) - ISNULL(d.iDAmount_f, 0)) FROM dbo.Ar_Detail AS d"
        " WHERE d.cFlag = N'AR' AND d.cCoVouchType = h.cVouchType AND d.cCoVouchID = h.cSBVCode AND d.iBVid = b.AutoID"
        " AND d.iFlag < 3)"
    )
    expr = (
        f"h.cVouchType + N'|' + h.cSBVCode + N'|' + CONVERT(varchar(20), b.AutoID) + N'|' + {_money(rem)}"
    )
    frm = (
        "FROM dbo.SaleBillVouch AS h JOIN dbo.SaleBillVouchs AS b ON b.SBVID = h.SBVID"
        f" WHERE h.SBVID = {ref} AND b.AutoID = (SELECT MIN(z.AutoID) FROM dbo.SaleBillVouchs AS z WHERE z.SBVID = h.SBVID)"
    )
    return _row(expr, frm)


def blue_pick_sql(customer: str, amount: Decimal, day: str) -> str:
    """一行：<蓝字发票类型>|<发票号>|<发票行 AutoID>。该客户已审核、本位币、余额不小于 amount 的蓝字销售发票行。"""
    text, _year, _month = _day(day)
    cus = safe_lot(customer)
    return f"""SET NOCOUNT ON;
WITH s AS (
  {_sale_open(f" AND d.cDwCode = N'{cus}'", _amount(amount))}
)
SELECT TOP (1) N'ROW:' + s.vt + N'|' + s.code + N'|' + CONVERT(varchar(20), s.line) AS v
FROM s
INNER JOIN dbo.SaleBillVouch AS sh ON sh.cSBVCode = s.code AND sh.cVouchType = s.vt
INNER JOIN dbo.SaleBillVouchs AS sb ON sb.AutoID = s.line AND sb.SBVID = sh.SBVID
WHERE sh.cCusCode = s.dw AND {_sale_head_ok("sh", text)}
ORDER BY sh.SBVID DESC, s.line;
"""


def red_snap_sql(red_line: int, blue_line: int, customer: str) -> str:
    """一行：<红票行 iExchSum>|<蓝票行 iExchSum>|<红票行原币余额（借 − 贷，负数）>|<蓝票行原币余额>|<应收明细上 9N 行数>。"""
    red, blue, cus = _line(red_line), _line(blue_line), safe_lot(customer)
    acc = "(SELECT x.iExchSum FROM dbo.SaleBillVouchs AS x WHERE x.AutoID = {ref})"
    rem = (
        "(SELECT SUM(ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0)) FROM dbo.Ar_Detail AS d"
        " WHERE d.cFlag = N'AR' AND d.cCoVouchType IN (N'26', N'27') AND d.iBVid = {ref}"
        f" AND d.cDwCode = N'{cus}' AND d.iFlag < 3)"
    )
    expr = " + N'|' + ".join(
        (
            _money(acc.format(ref=red)),
            _money(acc.format(ref=blue)),
            _money(rem.format(ref=red)),
            _money(rem.format(ref=blue)),
            _count("SELECT COUNT(*) FROM dbo.Ar_Detail WHERE cProcStyle = N'9N'"),
        )
    )
    return _row(expr)


def red_rows_sql(no: str, red_line: int, blue_line: int) -> str:
    """一行：<该处理号的 9N 行数>|<红票行借方原币合计>|<蓝票行借方原币合计>。"""
    key, red, blue = safe_no(no), _line(red_line), _line(blue_line)
    frm = f"FROM dbo.Ar_Detail AS d WHERE d.cCancelNo = N'{key}' AND d.cProcStyle = N'9N'"
    expr = " + N'|' + ".join(
        (
            _count("COUNT(*)"),
            _money(f"SUM(CASE WHEN d.iBVid = {red} THEN d.iDAmount_f ELSE 0 END)"),
            _money(f"SUM(CASE WHEN d.iBVid = {blue} THEN d.iDAmount_f ELSE 0 END)"),
        )
    )
    return _row(expr, frm)


def red_gone_sql(invoice: int, ret: int) -> str:
    """一行：<红字发票表头行数>|<退货单表头行数>（清理之后都应为 0）。"""
    inv, back = _line(invoice), _line(ret)
    expr = (
        _count(f"SELECT COUNT(*) FROM dbo.SaleBillVouch AS h WHERE h.SBVID = {inv}")
        + " + N'|' + "
        + _count(f"SELECT COUNT(*) FROM dbo.DispatchList AS h WHERE h.DLID = {back}")
    )
    return _row(expr)


def ret_line_sql(ret: int) -> str:
    """一行：新退货单（只有一行）的行主键。"""
    return _row("CONVERT(varchar(20), MIN(x.iDLsID))", f"FROM dbo.DispatchLists AS x WHERE x.DLID = {_line(ret)}")


# ---- 汇兑损益（9M） ----

_FX = {
    "AR": {"detail": "Ar_Detail", "bill": "R[0-9]", "types": "N'26', N'27', N'48'", "open": "d.iDAmount_f - d.iCAmount_f"},
    "AP": {"detail": "Ap_Detail", "bill": "P[0-9]", "types": "N'01', N'02', N'49'", "open": "d.iCAmount_f - d.iDAmount_f"},
}


def _fx(side: str) -> dict[str, str]:
    found = _FX.get(side)
    if found is None:
        raise ValueError("side 只能是 AR 或 AP")
    return found


def fx_pick_sql(side: str, day: str) -> str:
    """一行：<往来单位>|<外币名称>|<该单位该币种审核行上的最大汇率>。

    该单位在这一侧有外币（非本位币）、原币余额不为 0、日期不晚于 day 的应收单 / 应付单（汇兑损益要改它的本币余额）；
    day 所在期间应收、应付都未结账。往来单位按编码取最小的一个。
    """
    s = _fx(side)
    text, _year, _month = _day(day)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(o.dw)) + N'|' + o.cur + N'|' + CONVERT(varchar(40), CONVERT(decimal(18, 6), o.rate)) AS v
FROM (
  SELECT d.cDwCode AS dw, d.cexch_name AS cur, MAX(d.iExchRate) AS rate
  FROM dbo.{s['detail']} AS d
  WHERE d.cFlag = N'{side}' AND d.cProcStyle = d.cVouchType AND d.cexch_name <> {_HOME} AND ISNULL(d.iExchRate, 0) > 0
    AND (d.cVouchType IN ({s['types']}) OR d.cVouchType LIKE N'{s['bill']}') AND d.dVouchDate <= '{text}'
  GROUP BY d.cDwCode, d.cexch_name
) AS o
WHERE EXISTS (
  SELECT 1 FROM dbo.{s['detail']} AS d
  WHERE d.cFlag = N'{side}' AND d.cDwCode = o.dw AND d.cexch_name = o.cur AND d.cCoVouchType LIKE N'{s['bill']}'
    AND d.iFlag <= 2
  GROUP BY d.cCoVouchID
  HAVING SUM(ISNULL({s['open']}, 0)) <> 0
)
  AND {_open_period(text)}
ORDER BY o.dw, o.cur;
"""


def fx_snap_sql(side: str, partner: str, currency: str) -> str:
    """一行：<该单位 9M 行数>|<应收 / 应付单表头本币余额合计>|<原币余额合计>|<收付款单行本币余额合计>|<原币余额合计>
    |<发票行本币累计合计（销售 iMoneySum，采购 iTotal）>。都只算该单位、该币种。"""
    s = _fx(side)
    dw, cur = safe_lot(partner), safe_cur(currency)
    bill = f"FROM dbo.Ap_Vouch AS v WHERE v.cFlag = N'{side}' AND v.cDwCode = N'{dw}' AND v.cexch_name = N'{cur}'"
    close = (
        "FROM dbo.Ap_CloseBills AS b INNER JOIN dbo.Ap_CloseBill AS h ON h.iID = b.iID"
        f" WHERE h.cFlag = N'{side}' AND h.cDwCode = N'{dw}' AND h.cexch_name = N'{cur}'"
    )
    if side == "AR":
        inv = (
            "(SELECT SUM(x.iMoneySum) FROM dbo.SaleBillVouchs AS x INNER JOIN dbo.SaleBillVouch AS h ON h.SBVID = x.SBVID"
            f" WHERE h.cCusCode = N'{dw}' AND h.cexch_name = N'{cur}')"
        )
    else:
        inv = (
            "(SELECT SUM(x.iTotal) FROM dbo.PurBillVouchs AS x INNER JOIN dbo.PurBillVouch AS h ON h.PBVID = x.PBVID"
            f" WHERE h.cVenCode = N'{dw}' AND h.cexch_name = N'{cur}')"
        )
    expr = " + N'|' + ".join(
        (
            _count(f"SELECT COUNT(*) FROM dbo.{s['detail']} AS d WHERE d.cProcStyle = N'9M' AND d.cDwCode = N'{dw}'"),
            _money(f"(SELECT SUM(v.iRAmount) {bill})"),
            _money(f"(SELECT SUM(v.iRAmount_f) {bill})"),
            _money(f"(SELECT SUM(b.iRAmt) {close})"),
            _money(f"(SELECT SUM(b.iRAmt_f) {close})"),
            _money(inv),
        )
    )
    return _row(expr)


def fx_rows_sql(side: str, nos: Sequence[str]) -> str:
    """一行：<这些处理号的 9M 行数>|<本币借方减贷方合计>|<原币借方减贷方合计>（与桥返回的 diff 同口径）。"""
    s = _fx(side)
    frm = f"FROM dbo.{s['detail']} AS d WHERE d.cProcStyle = N'9M' AND d.cCancelNo IN ({_nos(nos)})"
    expr = " + N'|' + ".join(
        (
            _count("COUNT(*)"),
            _money("SUM(ISNULL(d.iDAmount, 0) - ISNULL(d.iCAmount, 0))"),
            _money("SUM(ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0))"),
        )
    )
    return _row(expr, frm)


def pl_code_sql(day: str) -> str:
    """一行：会计年度里 6603 开头、不带辅助核算的末级科目（名称含「汇兑」的优先）；没有时为空。"""
    _text, year, _month = _day(day)
    inner = (
        f"SELECT TOP (1) LTRIM(RTRIM(c.ccode)) FROM dbo.code AS c WHERE c.iyear = {year} AND c.bend = 1"
        " AND c.ccode LIKE N'6603%' AND ISNULL(c.bcus, 0) = 0 AND ISNULL(c.bsup, 0) = 0 AND ISNULL(c.bperson, 0) = 0"
        " AND ISNULL(c.bdept, 0) = 0 AND ISNULL(c.bitem, 0) = 0"
        " ORDER BY CASE WHEN c.ccode_name LIKE N'%汇兑%' THEN 0 ELSE 1 END, c.ccode"
    )
    return _row(f"ISNULL(({inner}), N'')")


def arap_proc_more_samples() -> list[tuple[str, str]]:
    day = "2026-10-02"
    return [
        ("apx-red-src", red_src_sql(day)),
        ("apx-red-line", red_line_sql(100)),
        ("apx-blue-pick", blue_pick_sql("C0001", Decimal("10.00"), day)),
        ("apx-red-snap", red_snap_sql(100, 200, "C0001")),
        ("apx-red-rows", red_rows_sql("HRAR0000000000001", 100, 200)),
        ("apx-red-gone", red_gone_sql(100, 200)),
        ("apx-ret-line", ret_line_sql(100)),
        ("apx-fx-pick-ar", fx_pick_sql("AR", day)),
        ("apx-fx-pick-ap", fx_pick_sql("AP", day)),
        ("apx-fx-snap-ar", fx_snap_sql("AR", "C0001", "美元")),
        ("apx-fx-snap-ap", fx_snap_sql("AP", "S0001", "美元")),
        ("apx-fx-rows", fx_rows_sql("AR", ("SYRAR0000000001",))),
        ("apx-pl-code", pl_code_sql(day)),
    ]
