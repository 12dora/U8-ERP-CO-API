"""预收 / 预付行核销、外币核销用例（writeoff_prepay_fx）的挑单和回读 SQL。只读；标识符是按侧登记的常量，
拼进去的只有校验过范围的正整数主键和核销号数字。

arap_more_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re

from co.e2e.casespec.writeoff_sql import hx_ap_sql, hx_ar_sql

_INT_MAX = 2147483647
_DIGITS = re.compile(r"^[0-9]{1,20}\Z")
_HOME = "(SELECT TOP (1) f.cexch_name FROM dbo.foreigncurrency AS f WHERE f.iotherused = -1)"
_LOCAL_LINE = "ISNULL(b.bPrePay, 0) = 0"
_PREPAY_LINE = "ISNULL(b.bPrePay, 0) = 1"

# 侧 → 往来明细表、收付款单类型、发票类型、应收应付单类型、余额表达式（应收借减贷，应付贷减借）、结账列。
SIDES = {
    "AR": {
        "detail": "Ar_Detail",
        "flag": "AR",
        "rtype": "48",
        "types": "N'26', N'27'",
        "btype": "R0",
        "open": "ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0)",
        "mend": "bflag_AR",
    },
    "AP": {
        "detail": "Ap_Detail",
        "flag": "AP",
        "rtype": "49",
        "types": "N'01', N'02'",
        "btype": "P0",
        "open": "ISNULL(d.iCAmount_f, 0) - ISNULL(d.iDAmount_f, 0)",
        "mend": "bflag_AP",
    },
}


def _side(side: str) -> dict[str, str]:
    found = SIDES.get(side)
    if found is None:
        raise ValueError("side 只能是 AR 或 AP")
    return found


def _id(value: int) -> int:
    if type(value) is not int or not 1 <= value <= _INT_MAX:
        raise ValueError("主键不对")
    return value


def prepay_pick_sql(side: str) -> str:
    """一行：<收付款单 iID>|<预收 / 预付行 ID>|<发票主键>|<发票行>。

    与 ar-writeoff / ap-writeoff 的挑单（writeoff_sql）同一口径，只把收付款单行换成预收 / 预付行（bPrePay=1）。
    """
    base = hx_ar_sql() if _side(side)["flag"] == "AR" else hx_ap_sql()
    if base.count(_LOCAL_LINE) != 1:
        raise ValueError("核销挑单 SQL 的形状变了，预收 / 预付挑单要跟着改")
    return base.replace(_LOCAL_LINE, _PREPAY_LINE)


def fx_pick_sql(side: str) -> str:
    """一行：<收付款单 iID>|<行 ID>|<应收 / 应付单 Auto_ID>|<汇率>。

    已审核的外币收付款单行（非本位币、汇率不是 0 或 1、不是预收付、余额 >= 2），和同一往来单位、同一币种、同一汇率、
    已审核、原币余额 >= 2 的应收 / 应付单（整单核销）；都不受审批流控制，日期不晚于今天，本月应收 / 应付未结账。
    """
    s = _side(side)
    return f"""SET NOCOUNT ON;
WITH o AS (
  SELECT d.cCoVouchID AS c, d.cDwCode AS dw
  FROM dbo.{s['detail']} AS d
  WHERE d.cFlag = N'{s['flag']}' AND d.cCoVouchType = N'{s['btype']}' AND d.iFlag < 3
  GROUP BY d.cCoVouchID, d.cDwCode
  HAVING SUM({s['open']}) >= 2
)
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.iID) + N'|' + CONVERT(varchar(20), b.ID) + N'|'
  + CONVERT(varchar(20), v.Auto_ID) + N'|' + CONVERT(varchar(40), CONVERT(decimal(18, 6), h.iExchRate)) AS v
FROM o
INNER JOIN dbo.Ap_Vouch AS v ON v.cFlag = N'{s['flag']}' AND v.cVouchType = N'{s['btype']}' AND v.cVouchID = o.c
  AND v.cDwCode = o.dw
INNER JOIN dbo.Ap_CloseBill AS h ON h.cDwCode = o.dw AND h.cFlag = N'{s['flag']}' AND h.cVouchType = N'{s['rtype']}'
INNER JOIN dbo.Ap_CloseBills AS b ON b.iID = h.iID
WHERE b.iRAmt_f >= 2 AND {_LOCAL_LINE}
  AND LTRIM(RTRIM(ISNULL(h.cCheckMan, N''))) <> N'' AND LTRIM(RTRIM(ISNULL(v.cCheckMan, N''))) <> N''
  AND ISNULL(h.IsWfControlled, 0) = 0 AND ISNULL(v.IsWfControlled, 0) = 0
  AND h.cexch_name <> {_HOME} AND v.cexch_name = h.cexch_name
  AND ISNULL(h.iExchRate, 0) NOT IN (0, 1) AND v.iExchRate = h.iExchRate
  AND h.dVouchDate <= CONVERT(date, GETDATE()) AND v.dVouchDate <= CONVERT(date, GETDATE())
  AND NOT EXISTS (SELECT 1 FROM dbo.GL_mend AS m WHERE m.iyear = YEAR(GETDATE()) AND m.iperiod = MONTH(GETDATE())
    AND ISNULL(m.{s['mend']}, 0) <> 0)
ORDER BY h.iID DESC, b.ID, v.Auto_ID DESC;
"""


def _target(s: dict[str, str], kind: str, ref: int) -> str:
    # 被核销单据的往来明细条件：发票按行（iBVid），应收 / 应付单按整单（类型 + 单号）。
    if kind == "invoice":
        return f"d.cCoVouchType IN ({s['types']}) AND d.iBVid = {_id(ref)}"
    if kind == "bill":
        return (
            f"d.cCoVouchType = N'{s['btype']}' AND d.cCoVouchID = (SELECT v.cVouchID FROM dbo.Ap_Vouch AS v"
            f" WHERE v.Auto_ID = {_id(ref)} AND v.cFlag = N'{s['flag']}')"
        )
    raise ValueError("被核销单据只能是 invoice 或 bill")


def balance_sql(side: str, line: int, target: tuple[str, int]) -> str:
    """一行：<收付款单行原币余额>|<本币余额>|<表头 bPrePay>|<被核销单据原币余额>|<被核销单据上的 9M 行数>。

    target 为 ("invoice", 发票行 iBVid) 或 ("bill", 应收 / 应付单 Auto_ID)。余额口径同桥（iFlag<3）；9M（汇兑损益）行不分 iFlag。
    """
    s = _side(side)
    where = _target(s, *target)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(40), CONVERT(decimal(28, 2), ISNULL(b.iRAmt_f, 0)))"
        " + N'|' + CONVERT(varchar(40), CONVERT(decimal(28, 2), ISNULL(b.iRAmt, 0)))"
        " + N'|' + CONVERT(varchar(1), CONVERT(int, ISNULL(h.bPrePay, 0)))"
        f" + N'|' + CONVERT(varchar(40), CONVERT(decimal(28, 2), ISNULL((SELECT SUM({s['open']}) FROM dbo.{s['detail']} AS d"
        f" WHERE d.cFlag = N'{s['flag']}' AND d.iFlag < 3 AND {where}), 0)))"
        f" + N'|' + CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.{s['detail']} AS d"
        f" WHERE d.cFlag = N'{s['flag']}' AND d.cProcStyle = N'9M' AND {where})) AS v\n"
        f"FROM dbo.Ap_CloseBills AS b INNER JOIN dbo.Ap_CloseBill AS h ON h.iID = b.iID WHERE b.ID = {_id(line)};\n"
    )


def batch_sql(side: str, digits: str) -> str:
    """一行：<行数>|<收付款单自身行里 bPrePay=1 的行数>|<外币行数>|<对方单据行原币合计>|<对方单据行本币合计>。

    按核销号（HX<侧><数字>）数 9P 行。自身行是 cCoVouchType 等于 cVouchType（48 / 49）的冲减行；外币行是币种不是本位币、
    汇率不是 0 或 1 的行。核销号不存在时各项为 0。
    """
    s = _side(side)
    if _DIGITS.fullmatch(digits or "") is None:
        raise ValueError("核销号数字不对")
    other = "CASE WHEN d.cCoVouchType <> d.cVouchType THEN {expr} ELSE 0 END"
    orig = other.format(expr="ISNULL(d.iDAmount_f, 0) + ISNULL(d.iCAmount_f, 0)")
    local = other.format(expr="ISNULL(d.iDAmount, 0) + ISNULL(d.iCAmount, 0)")
    prepay = "CASE WHEN d.cCoVouchType = d.cVouchType AND ISNULL(d.bPrePay, 0) = 1 THEN 1 ELSE 0 END"
    fx = "CASE WHEN d.cexch_name <> f.home AND ISNULL(d.iExchRate, 0) NOT IN (0, 1) THEN 1 ELSE 0 END"
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*))"
        f" + N'|' + CONVERT(varchar(12), ISNULL(SUM({prepay}), 0))"
        f" + N'|' + CONVERT(varchar(12), ISNULL(SUM({fx}), 0))"
        f" + N'|' + CONVERT(varchar(40), CONVERT(decimal(28, 2), ISNULL(SUM({orig}), 0)))"
        f" + N'|' + CONVERT(varchar(40), CONVERT(decimal(28, 2), ISNULL(SUM({local}), 0))) AS v\n"
        f"FROM dbo.{s['detail']} AS d CROSS JOIN (SELECT TOP (1) c.cexch_name AS home FROM dbo.foreigncurrency AS c"
        " WHERE c.iotherused = -1) AS f\n"
        f"WHERE d.cFlag = N'{s['flag']}' AND d.cProcStyle = N'9P'"
        f" AND d.cCancelNo = N'HX{s['flag']}{digits}';\n"
    )


def arap_more_samples() -> list[tuple[str, str]]:
    rows = []
    for side in SIDES:
        low = side.lower()
        rows.append(("prepay-pick-" + low, prepay_pick_sql(side)))
        rows.append(("fx-pick-" + low, fx_pick_sql(side)))
        rows.append(("hx-balance-inv-" + low, balance_sql(side, 100, ("invoice", 200))))
        rows.append(("hx-balance-bill-" + low, balance_sql(side, 100, ("bill", 300))))
        rows.append(("hx-batch-" + low, batch_sql(side, "0000000001")))
    return rows
