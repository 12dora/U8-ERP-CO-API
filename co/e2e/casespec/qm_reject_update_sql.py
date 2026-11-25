"""不良品处理单修改用例（qm_reject_update）的回读 SQL。只读；主键只接受正整数（safe_id）再拼进 SQL。
qm_reject_update_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

from co.e2e.casespec.mo_close_sql import safe_id

# 修改用例写、读的表头自定义项（标准 nvarchar 列）。
DEFINE = "CDEFINE11"


def _qty(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,4), ISNULL({expr}, 0)))"


def first_line_sql(doc: str) -> str:
    """一行：<处理单第一行 AUTOID>|<数量>。没有表体时没有行。"""
    made = safe_id(doc)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), s.AUTOID) + N'|' + {_qty("s.FQUANTITY")} AS v
FROM dbo.QMREJECTVOUCHERS AS s
WHERE s.ID = {made}
ORDER BY s.AUTOID;
"""


def edit_state_sql(doc: str) -> str:
    """一行：<UFTS 十进制>|<自定义项>|<修改人>|<审核人>|<表体行数>|<不同的不良原因 + 处理方式数>|<数量合计>。"""
    made = safe_id(doc)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(20), CONVERT(bigint, r.UFTS))
  + N'|' + ISNULL(LTRIM(RTRIM(r.{DEFINE})), N'')
  + N'|' + ISNULL(LTRIM(RTRIM(r.CMODIFIER)), N'')
  + N'|' + ISNULL(LTRIM(RTRIM(r.CVERIFIER)), N'')
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.QMREJECTVOUCHERS AS s WHERE s.ID = r.ID))
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(DISTINCT ISNULL(s.CREASONCODE, N'') + N'/' + ISNULL(s.CSCRAPDISCODE, N''))
      FROM dbo.QMREJECTVOUCHERS AS s WHERE s.ID = r.ID))
  + N'|' + {_qty("(SELECT SUM(s.FQUANTITY) FROM dbo.QMREJECTVOUCHERS AS s WHERE s.ID = r.ID)")} AS v
FROM dbo.QMREJECTVOUCHER AS r
WHERE r.ID = {made};
"""


def qm_reject_update_samples() -> list[tuple[str, str]]:
    return [
        ("qm-reject-update-line", first_line_sql("100")),
        ("qm-reject-update-state", edit_state_sql("100")),
    ]
