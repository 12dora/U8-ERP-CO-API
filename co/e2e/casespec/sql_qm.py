"""质量管理挑单。QM04 取最新两张未提交单据。"""

from __future__ import annotations


def qm04_sql() -> str:
    return """SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(20), ID) AS v
FROM (
    SELECT TOP (2) ID
    FROM dbo.QMCHECKVOUCHER
    WHERE CVOUCHTYPE = N'QM04'
      AND IsWfControlled = 1
      AND iVerifyStateNew = 0
      AND IVERIFYSTATE = 0
      AND ISNULL(IBATCHCHKRESULT, 0) = 0
    ORDER BY ID DESC
) AS q
ORDER BY ID DESC;
"""


def qm_state_sql(kind: str) -> str:
    if kind not in ("QM03", "QM06"):
        raise ValueError("只挑 QM03 或 QM06")
    table = "dbo.QMCHECKVOUCHER" if kind == "QM03" else "dbo.QMREJECTVOUCHER"
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), ID) AS v\n"
        f"FROM {table}\n"
        f"WHERE CVOUCHTYPE = N'{kind}'\n"
        "ORDER BY ID DESC;\n"
    )
