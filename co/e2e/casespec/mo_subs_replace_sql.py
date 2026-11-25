"""生产订单修改替换替代料用例（mo_subs_replace）的挑单 SQL。只读。

母件编码来自生产订单新增挑单（moc_inv），只接受 [0-9A-Za-z._-] 再拼进 SQL（safe_code）；其余标识符都是常量。
mo_subs_replace_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_code

_NO_FREE = " AND ".join(f"ISNULL(bp.Free{n}, N'') = N''" for n in range(1, 11))


def subs_pick_sql(parent: str) -> str:
    """一行：<替代存货>|<不允许做 BOM 子件的存货>。

    替代存货：允许做 BOM 子件、未停用、有不带自由项的物料，不是母件本身，也不是母件任何标准 BOM 的子件（不会撞上订单的子件）。
    另一个是 bBomSub=0、不是母件本身的存货（反例：替代料不允许做 BOM 子件 400）；没有时第二段为空。
    """
    inv = safe_code(parent)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + RTRIM(i.cInvCode) + N'|' + ISNULL((SELECT TOP (1) RTRIM(n.cInvCode) FROM dbo.Inventory AS n
    WHERE ISNULL(n.bBomSub, 0) = 0 AND LEN(n.cInvCode) <= 12 AND n.cInvCode <> N'{inv}' ORDER BY n.cInvCode), N'') AS v
FROM dbo.Inventory AS i
WHERE i.bBomSub = 1 AND LEN(i.cInvCode) <= 12 AND i.cInvCode <> N'{inv}'
  AND (i.dEDate IS NULL OR i.dEDate > DATEADD(day, 1, CONVERT(date, GETDATE())))
  AND EXISTS (SELECT 1 FROM dbo.bas_part AS bp WHERE bp.InvCode = i.cInvCode AND {_NO_FREE})
  AND NOT EXISTS (SELECT 1 FROM dbo.bom_opcomponent AS c
    JOIN dbo.bas_part AS cp ON cp.PartId = c.ComponentId
    JOIN dbo.bom_parent AS p ON p.BomId = c.BomId
    JOIN dbo.bas_part AS pp ON pp.PartId = p.ParentId
    WHERE pp.InvCode = N'{inv}' AND cp.InvCode = i.cInvCode)
ORDER BY i.cInvCode;
"""


def mo_subs_replace_samples() -> list[tuple[str, str]]:
    return [("mo-subs-replace-pick", subs_pick_sql("DEMO-001"))]
