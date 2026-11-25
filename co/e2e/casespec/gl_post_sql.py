"""记账用例（gl_post）的回读 SQL。标识符是常量；拼进 SQL 的年度、期间、类别、凭证号先过白名单。

glpost_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re

from co.e2e.casespec.gl_voucher_sql import GlKey, gl_key
from co.e2e.testdata import DEFAULT_TRANSFER_SIGN

_YEAR = re.compile(r"^20[0-9]{2}\Z")


def _year(year: str) -> str:
    if _YEAR.fullmatch(str(year or "")) is None:
        raise ValueError("年度不合法")
    return str(year)


def _one(expr: str, frm: str) -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def _key_where(year: str, key: GlKey) -> str:
    return f"iyear = {_year(year)} AND iperiod = {key.period} AND csign = N'{key.sign}' AND ino_id = {key.no}"


def first_open_sql(year: str) -> str:
    # 本年第一个未结账的总账期间（桥只许记这一期）；没有时 0。
    expr = "CONVERT(varchar(4), ISNULL(MIN(iperiod), 0))"
    return _one(expr, f"FROM dbo.GL_mend WHERE iyear = {_year(year)} AND iperiod BETWEEN 1 AND 12 AND ISNULL(bflag, 0) = 0")


def prior_open_sql(year: str) -> str:
    # 上年度还有未结账的总账期间时 1，否则 0（桥记本年 1 期要求上年度已结账，同 GlPostCheck.Period）。
    prior = str(int(_year(year)) - 1)
    expr = "CONVERT(varchar(1), COUNT(*))"
    frm = "FROM (SELECT TOP 1 1 AS x FROM dbo.GL_mend WHERE iyear = " + prior
    return _one(expr, frm + " AND iperiod BETWEEN 1 AND 12 AND ISNULL(bflag, 0) = 0) t")


def master_sign_sql() -> str:
    # 总账选项「凭证须主管签字」：开着时用例记 N/A（桥不做主管签字）。
    expr = "ISNULL(MAX(CONVERT(nvarchar(10), cValue)), N'')"
    return _one(expr, "FROM dbo.AccInformation WHERE cSysID = N'GL' AND cName = N'bMasterSign'")


def posted_sql(year: str, key: GlKey) -> str:
    # 行数|最小 ibook|最小 cbook|最大 cbook。
    expr = (
        "CONVERT(varchar(10), COUNT(*)) + N'|' + CONVERT(varchar(4), MIN(ISNULL(CONVERT(int, ibook), 0)))"
        " + N'|' + MIN(ISNULL(cbook, N'')) + N'|' + MAX(ISNULL(cbook, N''))"
    )
    return _one(expr, "FROM dbo.GL_accvouch WHERE " + _key_where(year, key))


def others_sql(year: str, key: GlKey) -> str:
    # 本凭证之外全部凭证行的记账标志校验和（行数|校验和）：U8 回写 ibook 的 UPDATE 不带年度，桥要把误写的行改回。
    expr = (
        "CONVERT(varchar(20), COUNT_BIG(*)) + N'|' + CONVERT(varchar(20), ISNULL(CHECKSUM_AGG(CHECKSUM(i_id,"
        " ISNULL(CONVERT(int, ibook), -1), ISNULL(cbook, N'~'))), 0))"
    )
    return _one(expr, "FROM dbo.GL_accvouch WHERE NOT (" + _key_where(year, key) + ")")


def glpost_samples() -> list[tuple[str, str]]:
    key = gl_key(9, DEFAULT_TRANSFER_SIGN, 3)
    return [
        ("glpost-open", first_open_sql("2026")),
        ("glpost-prior", prior_open_sql("2026")),
        ("glpost-master", master_sign_sql()),
        ("glpost-posted", posted_sql("2026", key)),
        ("glpost-others", others_sql("2026", key)),
    ]
