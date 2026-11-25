"""生产订单新增探查（mo_probe）的挑选和回读 SQL。只读；挑出的存货编码只收字母数字，id 只收正整数。

每条 SQL 输出 ROW:<值>，挑不到就没有 ROW 行。
"""

from __future__ import annotations

import re

from co.e2e.casespec.mo_close_sql import safe_id

_INV = re.compile(r"^[0-9A-Za-z]{1,12}\Z")
_ALNUM = "NOT LIKE N'%[^0-9A-Za-z]%'"
_NO_FREE = " AND ".join(f"ISNULL(i.bFree{n}, 0) = 0" for n in range(1, 11))
_ALIVE = "(i.dEDate IS NULL OR i.dEDate > DATEADD(day, 1, CONVERT(date, GETDATE())))"


def safe_inv(text: str) -> str:
    if _INV.fullmatch(text or "") is None:
        raise ValueError("挑出的存货编码不合法")
    return text


def _pick(where: str) -> str:
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + RTRIM(i.cInvCode) AS v
FROM dbo.Inventory AS i
WHERE {where} AND {_ALIVE} AND {_NO_FREE}
  AND LEN(RTRIM(i.cInvCode)) <= 12 AND RTRIM(i.cInvCode) {_ALNUM}
ORDER BY i.cInvCode;
"""


def bought_inv_sql() -> str:
    """非自制件（bSelf = 0）：桥在调用 U8 之前按「不是自制件」400。"""
    return _pick("ISNULL(i.bSelf, 0) = 0")


def bomless_inv_sql() -> str:
    """有物料（bas_part）、却没有任何物料清单（bom_parent）的自制件。"""
    return _pick(
        "i.bSelf = 1 AND EXISTS (SELECT 1 FROM dbo.bas_part AS bp WHERE bp.InvCode = i.cInvCode)"
        " AND NOT EXISTS (SELECT 1 FROM dbo.bom_parent AS p JOIN dbo.bas_part AS bp ON bp.PartId = p.ParentId"
        " WHERE bp.InvCode = i.cInvCode)"
    )


def max_mo_sql() -> str:
    """当前最大的 MoId（没有为 0）。"""
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20), ISNULL(MAX(o.MoId), 0)) AS v FROM dbo.mom_order AS o;\n"


def new_mo_sql(after: str, inv: str) -> str:
    """MoId 大于 after、行上有该存货的生产订单张数（被拒的新增应为 0）。"""
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20), COUNT(DISTINCT d.MoId)) AS v\n"
        f"FROM dbo.mom_orderdetail AS d WHERE d.MoId > {safe_id(after) if after != '0' else '0'}"
        f" AND d.InvCode = N'{safe_inv(inv)}';\n"
    )


def mo_rows_sql(doc: str) -> str:
    """该订单的 表头行数|明细行数|子件行数。"""
    mo = safe_id(doc)
    return (
        f"SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.mom_order AS o WHERE o.MoId = {mo}))"
        f" + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.mom_orderdetail AS d WHERE d.MoId = {mo}))"
        " + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.mom_moallocate AS a JOIN dbo.mom_orderdetail AS d"
        f" ON d.MoDId = a.MoDId WHERE d.MoId = {mo})) AS v;\n"
    )


def mo_probe_samples() -> list[tuple[str, str]]:
    return [
        ("mo-bought-inv", bought_inv_sql()),
        ("mo-bomless-inv", bomless_inv_sql()),
        ("mo-max", max_mo_sql()),
        ("mo-new", new_mo_sql("100", "0001")),
        ("mo-rows", mo_rows_sql("100")),
    ]
