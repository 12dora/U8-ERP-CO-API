"""应收汇兑损益（9M）重做用例（exgain_replay）的挑单与快照 SQL。

只读；表名、列名是常量。拼进 SQL 的值都先校验：日期只接受 yyyy-mm-dd，往来单位编码只接受 [0-9A-Za-z._-]（safe_lot），
处理号只接受 SYRAR / SYPAP 等登记过的前缀加数字（safe_no），外部业务号只接受字母和数字。
挑单输出一行 ROW:<值>，快照每个对象一行 ROW:<值>（多个值用 | 分隔），调用方排序后比对。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from co.e2e.casespec.stock_plus_sql import safe_lot
from co.e2e.casespec.arap_proc_sql import _count, _day, _money, _nos, _row, safe_cur

_PZ = re.compile(r"^[0-9A-Za-z]{1,40}\Z")
# 桥做的应收单据类型（exgain_create.sql）：销售发票、收款单、应收单；只有表头的登记发票（iBVid 为 0）桥跳过。
_UNSUPPORTED = (
    "NOT (d.cCoVouchType IN (N'26', N'27', N'48') OR d.cCoVouchType LIKE N'R[0-9]')"
    " OR (d.cCoVouchType IN (N'26', N'27') AND ISNULL(d.iBVid, 0) = 0)"
)
# 审核登记（Sign）行的处理方式就是单据类型（同 exgain_cancel.sql 的 50028 判断）。
_SIGN_STYLES = "N'26', N'27', N'R0', N'48', N'49', N'01', N'02', N'P0'"
DAY_COLS = 11


def safe_pz(raw: str) -> str:
    if _PZ.fullmatch(raw or "") is None:
        raise ValueError("外部业务号不合法")
    return raw


def _dws(partners: Sequence[str]) -> str:
    if not partners or len(partners) > 200:
        raise ValueError("往来单位清单不合法")
    return ", ".join("N'" + safe_lot(dw) + "'" for dw in partners)


def _nine_m(day: str) -> str:
    text, _year, _month = _day(day)
    return f"d.cFlag = N'AR' AND d.cProcStyle = N'9M' AND d.dRegDate = '{text}'"


def exgain_day_sql(today: str) -> str:
    """一行：<登记日期>|<该期应收已结账 1/0>|<币种个数>|<币种>|<桥不做的行数>|<原币不为 0 的行数>|<不同凭证个数>|<凭证号>
    |<没有制单的行数>|<行数>|<期间>。登记日期取与 today 同一年度、不晚于 today 的最近一次应收汇兑损益；没有时为空。"""
    text, year, _month = _day(today)
    pick = (
        "SELECT TOP (1) d.dRegDate AS reg, d.iPeriod AS per FROM dbo.Ar_Detail AS d"
        f" WHERE d.cFlag = N'AR' AND d.cProcStyle = N'9M' AND YEAR(d.dRegDate) = {year} AND d.dRegDate <= '{text}'"
        " ORDER BY d.dRegDate DESC"
    )
    closed = (
        f"CASE WHEN EXISTS (SELECT 1 FROM dbo.GL_mend AS m WHERE m.iyear = {year} AND m.iperiod = x.per"
        " AND ISNULL(m.bflag_AR, 0) <> 0) THEN N'1' ELSE N'0' END"
    )
    rows = "FROM dbo.Ar_Detail AS d WHERE d.cFlag = N'AR' AND d.cProcStyle = N'9M' AND d.dRegDate = x.reg"
    empty = "LTRIM(RTRIM(ISNULL(d.cPZid, N''))) = N''"
    expr = " + N'|' + ".join(
        (
            "CONVERT(varchar(10), x.reg, 23)",
            closed,
            _count(f"SELECT COUNT(DISTINCT ISNULL(d.cexch_name, N'')) {rows}"),
            f"ISNULL((SELECT MIN(d.cexch_name) {rows}), N'')",
            _count(f"SELECT COUNT(*) {rows} AND ({_UNSUPPORTED})"),
            _count(f"SELECT COUNT(*) {rows} AND (ISNULL(d.iDAmount_f, 0) <> 0 OR ISNULL(d.iCAmount_f, 0) <> 0)"),
            _count(f"SELECT COUNT(DISTINCT d.cPZid) {rows} AND NOT ({empty})"),
            f"ISNULL((SELECT MIN(LTRIM(RTRIM(d.cPZid))) {rows} AND NOT ({empty})), N'')",
            _count(f"SELECT COUNT(*) {rows} AND {empty}"),
            _count(f"SELECT COUNT(*) {rows}"),
            "CONVERT(varchar(4), x.per)",
        )
    )
    return f"SET NOCOUNT ON;\nWITH x AS ({pick})\nSELECT N'ROW:' + {expr} AS v FROM x;\n"


def exgain_batches_sql(day: str) -> str:
    """每个处理号一行：<处理号>|<客户>|<之后还有别的处理 1/0>。「之后」同桥取消时的闸门：同一单据、Auto_ID 更大、
    处理号不同、审核登记以外的任何处理（含后来的汇兑损益），有就不能取消。"""
    later = (
        "SELECT 1 FROM dbo.Ar_Detail AS a WHERE a.cFlag = N'AR' AND a.cCoVouchType = d.cCoVouchType"
        " AND a.cCoVouchID = d.cCoVouchID AND a.cDwCode = d.cDwCode AND a.Auto_ID > d.Auto_ID"
        " AND ISNULL(a.cCancelNo, N'') <> d.cCancelNo AND ISNULL(a.cProcStyle, N'') <> ISNULL(a.cVouchType, N'')"
        f" AND ISNULL(a.cProcStyle, N'') NOT IN ({_SIGN_STYLES})"
    )
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + t.no + N'|' + t.dw + N'|' + CONVERT(varchar(1), MAX(t.blk)) AS v
FROM (
  SELECT d.cCancelNo AS no, LTRIM(RTRIM(d.cDwCode)) AS dw, CASE WHEN EXISTS ({later}) THEN 1 ELSE 0 END AS blk
  FROM dbo.Ar_Detail AS d WHERE {_nine_m(day)}
) AS t
GROUP BY t.no, t.dw
ORDER BY t.no;
"""


def exgain_later_sql(day: str, currency: str) -> str:
    """该币种在登记日期之后还有往来明细的客户，每个一行：<客户>。桥算汇兑损益取的是当前余额（不按登记日期截止），
    这些客户重做时会把之后的单据和核销算进去，与原批次比不了，整个跳过。「之后」：登记日期或单据日期晚于该日，
    或同一天、Auto_ID 大于该日最后一行 9M 的非 9M 行。"""
    text, _year, _month = _day(day)
    cur = safe_cur(currency)
    last = f"(SELECT MAX(d.Auto_ID) FROM dbo.Ar_Detail AS d WHERE {_nine_m(day)})"
    return f"""SET NOCOUNT ON;
SELECT DISTINCT N'ROW:' + LTRIM(RTRIM(a.cDwCode)) AS v
FROM dbo.Ar_Detail AS a
WHERE a.cFlag = N'AR' AND a.cexch_name = N'{cur}'
  AND (a.dRegDate > '{text}' OR a.dVouchDate > '{text}'
    OR (a.dRegDate = '{text}' AND ISNULL(a.cProcStyle, N'') <> N'9M' AND a.Auto_ID > {last}));
"""


def exgain_rows_sql(day: str, partners: Sequence[str]) -> str:
    """快照：这些客户在该登记日期的 9M 行，每个（客户、单据类型、单号）一行：<客户>|<类型>|<单号>|<行数>|<本币借方>
    |<本币贷方>|<原币借贷差>|<摘要>|<期间>|<处理号个数>。处理号、Auto_ID、凭证号不比。"""
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + LTRIM(RTRIM(d.cDwCode)) + N'|' + d.cCoVouchType + N'|' + LTRIM(RTRIM(d.cCoVouchID))
  + N'|' + {_count("COUNT(*)")} + N'|' + {_money("SUM(d.iDAmount)")} + N'|' + {_money("SUM(d.iCAmount)")}
  + N'|' + {_money("SUM(ISNULL(d.iDAmount_f, 0) - ISNULL(d.iCAmount_f, 0))")}
  + N'|' + REPLACE(ISNULL(MIN(d.cDigest), N''), N'|', N'/') + N'|' + {_count("MIN(d.iPeriod)")}
  + N'|' + {_count("COUNT(DISTINCT d.cCancelNo)")} AS v
FROM dbo.Ar_Detail AS d
WHERE {_nine_m(day)} AND d.cDwCode IN ({_dws(partners)})
GROUP BY d.cDwCode, d.cCoVouchType, d.cCoVouchID;
"""


def exgain_balances_sql(day: str, partners: Sequence[str]) -> str:
    """快照：这些客户该日 9M 涉及的单据，每张一行。应收单 R|类型|单号|本币余额|原币余额；收款单 C|单号|本币余额|原币余额
    （表体合计）；销售发票 S|类型|单号|iExchSum 合计|iMoneySum 合计。"""
    touched = (
        "EXISTS (SELECT 1 FROM dbo.Ar_Detail AS d WHERE " + _nine_m(day) + f" AND d.cDwCode IN ({_dws(partners)})"
        " AND d.cCoVouchType = {vt} AND d.cCoVouchID = {vid})"
    )
    bill = touched.format(vt="v.cVouchType", vid="v.cVouchID")
    close = touched.format(vt="h.cVouchType", vid="h.cVouchID")
    sale = touched.format(vt="s.cVouchType", vid="s.cSBVCode")
    return f"""SET NOCOUNT ON;
SELECT N'ROW:R|' + v.cVouchType + N'|' + LTRIM(RTRIM(v.cVouchID)) + N'|' + {_money("v.iRAmount")}
  + N'|' + {_money("v.iRAmount_f")} AS v
FROM dbo.Ap_Vouch AS v WHERE v.cFlag = N'AR' AND {bill}
UNION ALL
SELECT N'ROW:C|' + LTRIM(RTRIM(h.cVouchID)) + N'|' + {_money("SUM(b.iRAmt)")} + N'|' + {_money("SUM(b.iRAmt_f)")}
FROM dbo.Ap_CloseBill AS h INNER JOIN dbo.Ap_CloseBills AS b ON b.iID = h.iID
WHERE h.cFlag = N'AR' AND h.cVouchType = N'48' AND {close}
GROUP BY h.cVouchID
UNION ALL
SELECT N'ROW:S|' + s.cVouchType + N'|' + LTRIM(RTRIM(s.cSBVCode)) + N'|' + {_money("SUM(x.iExchSum)")}
  + N'|' + {_money("SUM(x.iMoneySum)")}
FROM dbo.SaleBillVouch AS s INNER JOIN dbo.SaleBillVouchs AS x ON x.SBVID = s.SBVID
WHERE {sale}
GROUP BY s.cVouchType, s.cSBVCode;
"""


def exgain_left_sql(nos: Sequence[str]) -> str:
    """一行：<这些处理号在应收往来明细里剩下的行数>（取消后应为 0）。"""
    return _row(_count(f"SELECT COUNT(*) FROM dbo.Ar_Detail AS d WHERE d.cFlag = N'AR' AND d.cCancelNo IN ({_nos(nos)})"))


def gl_info_sql(pz: str) -> str:
    """一行：<凭证行数>|<已记账行数>|<作废行数>|<不带客户的科目个数>|<不带客户的科目>|<凭证类别>|<制单日期>。
    汇兑损益凭证不带客户的行就是损益科目（pl_code）。"""
    key = safe_pz(pz)
    plain = "LTRIM(RTRIM(ISNULL(g.ccus_id, N''))) = N''"
    expr = " + N'|' + ".join(
        (
            _count("COUNT(*)"),
            _count("SUM(CASE WHEN ISNULL(g.ibook, 0) <> 0 THEN 1 ELSE 0 END)"),
            _count("SUM(CASE WHEN ISNULL(g.iflag, 0) = 1 THEN 1 ELSE 0 END)"),
            _count(f"COUNT(DISTINCT CASE WHEN {plain} THEN g.ccode END)"),
            f"ISNULL(MIN(CASE WHEN {plain} THEN LTRIM(RTRIM(g.ccode)) END), N'')",
            "ISNULL(MIN(g.csign), N'')",
            "ISNULL(CONVERT(varchar(10), MIN(g.dbill_date), 23), N'')",
        )
    )
    return _row(expr, f"FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{key}'")


def gl_lines_sql(pz: str) -> str:
    """快照：凭证按（科目、客户）合计，每组一行：<科目>|<客户>|<借方>|<贷方>|<行数>。凭证号、摘要不比。"""
    key = safe_pz(pz)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + LTRIM(RTRIM(g.ccode)) + N'|' + LTRIM(RTRIM(ISNULL(g.ccus_id, N''))) + N'|' + {_money("SUM(g.md)")}
  + N'|' + {_money("SUM(g.mc)")} + N'|' + {_count("COUNT(*)")} AS v
FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{key}'
GROUP BY g.ccode, g.ccus_id;
"""


def exgain_replay_samples() -> list[tuple[str, str]]:
    day, dws = "2026-08-31", ["C0001", "C0002"]
    return [
        ("exgain-replay-day", exgain_day_sql("2026-10-03")),
        ("exgain-replay-batches", exgain_batches_sql(day)),
        ("exgain-replay-later", exgain_later_sql(day, "美元")),
        ("exgain-replay-rows", exgain_rows_sql(day, dws)),
        ("exgain-replay-balances", exgain_balances_sql(day, dws)),
        ("exgain-replay-left", exgain_left_sql(["SYRAR0000000001"])),
        ("exgain-replay-gl-info", gl_info_sql("AR0000000000007")),
        ("exgain-replay-gl-lines", gl_lines_sql("AR0000000000007")),
    ]
