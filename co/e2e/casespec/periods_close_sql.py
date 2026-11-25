"""月末结账用例（periods_close）的挑数 SQL。只读；标识符是常量，没有拼进调用方的值。

periods_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

# GL_mend 第 1 到 12 期各模块的结账标志，一期一行：年度|期间|采购|销售|库存|存货|应收|应付|总账（0 / 1，空值按 0），
# 同桥 PeriodClose 读的列（总账是 bflag）。第 0 期（期初）不在内。
_FLAGS = (
    "SET NOCOUNT ON;\n"
    "SELECT N'ROW:' + CONVERT(nvarchar(4), m.iyear) + N'|' + CONVERT(nvarchar(2), m.iperiod)"
    " + N'|' + CONVERT(nvarchar(1), CONVERT(int, ISNULL(m.bflag_PU, 0)))"
    " + N'|' + CONVERT(nvarchar(1), CONVERT(int, ISNULL(m.bflag_SA, 0)))"
    " + N'|' + CONVERT(nvarchar(1), CONVERT(int, ISNULL(m.bflag_ST, 0)))"
    " + N'|' + CONVERT(nvarchar(1), CONVERT(int, ISNULL(m.bflag_IA, 0)))"
    " + N'|' + CONVERT(nvarchar(1), CONVERT(int, ISNULL(m.bflag_AR, 0)))"
    " + N'|' + CONVERT(nvarchar(1), CONVERT(int, ISNULL(m.bflag_AP, 0)))"
    " + N'|' + CONVERT(nvarchar(1), CONVERT(int, ISNULL(m.bflag, 0))) AS v\n"
    "FROM dbo.GL_mend m WHERE m.iperiod BETWEEN 1 AND 12 ORDER BY m.iyear, m.iperiod;\n"
)


def period_flags_sql() -> str:
    """每行：<年度>|<期间>|<七个模块的标志>，例如 2026|9|1|1|1|1|1|1|0。"""
    return _FLAGS


def periods_samples() -> list[tuple[str, str]]:
    return [("periods-flags", period_flags_sql())]
