"""采购手工结算用例（pu_settle_manual）的挑单与回读 SQL。

只读。挑单与回读都返回一行 ROW:a|b|…，挑不到返回空。id 只接受正整数（safe_id），日期只接受 yyyy-MM-dd，再拼进 SQL。
仓库、供应商、部门、存货、采购类型沿用无来源红字采购入库的挑单（stock_plus_sql.red_pin_sql：有可用量的外购存货）；
「普通业务必有订单」打开时用同一组档案建采购订单（订单行用 stock_plus_sql.po_line_sql 回读）。
"""

from __future__ import annotations

import re

from co.e2e.casespec.stock_misc_sql import safe_id
from co.e2e.casespec.stock_plus_sql import red_pin_sql

_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}\Z")
# 回读的列数。
RD_COLS = 2
PRICE_COLS = 2
BILL_COLS = 3
SETTLE_COLS = 5


def safe_day(raw: str) -> str:
    if _DAY.fullmatch(raw or "") is None:
        raise ValueError("日期不合法")
    return raw


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def pick_sql() -> str:
    """仓库|供应商|部门|存货|采购类型（同 red_pin_sql）。"""
    return red_pin_sql()


def prior_open_sql(day: str) -> str:
    # 结算日期所在月以前还有采购未结账的期间（按自然月）时 U8 不许结算：返回未结账期间数，0 才能跑。
    d = safe_day(day)
    expr = "CONVERT(varchar(10), COUNT(*))"
    frm = (
        "FROM dbo.GL_mend AS g WHERE ISNULL(g.bflag_PU, 0) = 0 AND g.iperiod BETWEEN 1 AND 12"
        f" AND g.iyear * 100 + g.iperiod < YEAR(CONVERT(date, '{d}', 23)) * 100 + MONTH(CONVERT(date, '{d}', 23))"
    )
    return _one(expr, frm)


def rd_line_sql(doc: int) -> str:
    """采购入库单的第一行 AutoID。"""
    return _one("CONVERT(varchar(20), MIN(b.AutoID))", f"FROM dbo.rdrecords01 AS b WHERE b.ID = {safe_id(str(doc))}")


def arrival_line_sql(doc: int) -> str:
    """到货单 / 采购退货单的第一行 Autoid。"""
    return _one("CONVERT(varchar(20), MIN(s.Autoid))", f"FROM dbo.PU_ArrivalVouchs AS s WHERE s.ID = {safe_id(str(doc))}")


def rd_price_sql(line: int) -> str:
    """入库行：金额 iPrice|单价 iUnitCost（部分结算不应改写，删除结算单后应还原）。"""
    expr = (
        "CONVERT(varchar(40), CONVERT(decimal(18,2), ISNULL(b.iPrice, 0))) + N'|'"
        " + CONVERT(varchar(40), CONVERT(decimal(18,6), ISNULL(b.iUnitCost, 0)))"
    )
    return _one(expr, f"FROM dbo.rdrecords01 AS b WHERE b.AutoID = {safe_id(str(line))}")


def bill_line_sql(doc: int) -> str:
    """采购发票的第一行 ID。"""
    return _one("CONVERT(varchar(20), MIN(b.ID))", f"FROM dbo.PurBillVouchs AS b WHERE b.PBVID = {safe_id(str(doc))}")


def rd_state_sql(line: int) -> str:
    """入库行：已结算数量 iSQuantity|结算日期。"""
    expr = _qty("b.iSQuantity") + " + N'|' + ISNULL(CONVERT(varchar(10), b.dSDate, 23), N'')"
    return _one(expr, f"FROM dbo.rdrecords01 AS b WHERE b.AutoID = {safe_id(str(line))}")


def bill_state_sql(line: int) -> str:
    """发票行：已结算数量合计|行的结算日期|表头的结算日期。"""
    total = f"(SELECT SUM(s.iSVQuantity) FROM dbo.PurSettleVouchs AS s WHERE s.iBsID = {safe_id(str(line))})"
    expr = (
        _qty(total) + " + N'|' + ISNULL(CONVERT(varchar(10), b.dSDate, 23), N'')"
        " + N'|' + ISNULL(CONVERT(varchar(10), h.dSDate, 23), N'')"
    )
    frm = f"FROM dbo.PurBillVouchs AS b JOIN dbo.PurBillVouch AS h ON h.PBVID = b.PBVID WHERE b.ID = {safe_id(str(line))}"
    return _one(expr, frm)


def settle_state_sql(psvid: int) -> str:
    """结算单：行数|数量合计|结算金额合计|没有发票行的行数|没有入库行的行数。"""
    doc = safe_id(str(psvid))
    expr = (
        "CONVERT(varchar(10), COUNT(*)) + N'|' + " + _qty("SUM(s.iSVQuantity)")
        + " + N'|' + CONVERT(varchar(40), CONVERT(decimal(18,2), ISNULL(SUM(s.iSVPrice), 0)))"
        " + N'|' + CONVERT(varchar(10), ISNULL(SUM(CASE WHEN ISNULL(s.iBsID, 0) = 0 THEN 1 ELSE 0 END), 0))"
        " + N'|' + CONVERT(varchar(10), ISNULL(SUM(CASE WHEN ISNULL(s.iRdsID, 0) = 0 THEN 1 ELSE 0 END), 0))"
    )
    return _one(expr, f"FROM dbo.PurSettleVouchs AS s WHERE s.PSVID = {doc}")


def pu_settle_man_samples() -> list[tuple[str, str]]:
    return [
        ("pu-settle-man-prior-open", prior_open_sql("2026-09-30")),
        ("pu-settle-man-rd-line", rd_line_sql(100)),
        ("pu-settle-man-bill-line", bill_line_sql(100)),
        ("pu-settle-man-rd-state", rd_state_sql(100)),
        ("pu-settle-man-arrival-line", arrival_line_sql(100)),
        ("pu-settle-man-rd-price", rd_price_sql(100)),
        ("pu-settle-man-bill-state", bill_state_sql(100)),
        ("pu-settle-man-settle-state", settle_state_sql(100)),
    ]
