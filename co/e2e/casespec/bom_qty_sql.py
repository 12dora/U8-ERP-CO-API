"""物料清单用量精度与表头修改（bom_qty，对应 `bom-load-qty`）的挑选和回读 SQL。

只读。id、行号只收正整数，日期只收 yyyy-MM-dd；挑出的存货编码只进请求正文，不拼进 SQL。
每条 SQL 输出 ROW:<值>，多列用 | 连接；挑不到、读不到就没有 ROW 行。
"""

from __future__ import annotations

import re

from co.e2e.casespec.mo_close_sql import safe_id

# 本用例新建版本的版本说明（回读按它认出本用例建的版本，也用来核对表头没被改掉）。
MEMO = "u8co e2e 物料清单用量精度"
_DATE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_SEQ_MAX = 99999
_NO_FREE = " AND ".join(f"ISNULL(i.bFree{n}, 0) = 0" for n in range(1, 11))
_PART_BASE = "COALESCE(" + ", ".join(f"NULLIF(bp.Free{n}, N'')" for n in range(1, 11)) + ") IS NULL"
_ALIVE = "(i.dEDate IS NULL OR i.dEDate > DATEADD(day, 1, CONVERT(date, GETDATE())))"


def safe_date(text: str) -> str:
    if _DATE.fullmatch(text or "") is None:
        raise ValueError("日期不合法")
    return text


def safe_seq(seq: int) -> int:
    if isinstance(seq, bool) or not isinstance(seq, int) or not 1 <= seq <= _SEQ_MAX:
        raise ValueError("行号不合法")
    return seq


def digits_sql() -> str:
    """账套的存货数量小数位（AccInformation AA iStrsQuanDecDgt），读不到按 6。"""
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(10), ISNULL(MAX(CONVERT(int, o.cValue)), 6)) AS v\n"
        "FROM dbo.AccInformation AS o WHERE o.cSysID = N'AA' AND o.cName = N'iStrsQuanDecDgt';\n"
    )


def aux_pick_sql() -> str:
    """辅计量候选子件（固定换算率 iGroupType = 1）：允许 BOM 子件、没停用、没有自由项、有不带自由项的物料，
    缺省仓库为空或未停用。取前 5 个，由用例排除母件和已用的子件。"""
    return f"""SET NOCOUNT ON;
SELECT TOP (5) N'ROW:' + RTRIM(i.cInvCode) AS v
FROM dbo.Inventory AS i
WHERE ISNULL(i.iGroupType, 0) = 1 AND i.bBomSub = 1 AND {_ALIVE} AND {_NO_FREE}
  AND LEN(RTRIM(i.cInvCode)) <= 12
  AND EXISTS (SELECT 1 FROM dbo.bas_part AS bp WHERE bp.InvCode = i.cInvCode AND {_PART_BASE})
  AND (ISNULL(i.cDefWareHouse, N'') = N''
    OR EXISTS (SELECT 1 FROM dbo.Warehouse AS w WHERE w.cWhCode = i.cDefWareHouse
               AND (w.dWhEndDate IS NULL OR w.dWhEndDate > DATEADD(day, 1, CONVERT(date, GETDATE())))))
ORDER BY i.cInvCode;
"""


def max_bom_sql() -> str:
    """当前最大的 BomId（没有为 0）。"""
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20), ISNULL(MAX(b.BomId), 0)) AS v FROM dbo.bom_bom AS b;\n"


def orphan_sql(after: str, eff: str) -> str:
    """新增没拿到 id 时：BomId 大于 after、生效日期是本用例的日期、版本说明是 MEMO 的版本（没有为 0）。"""
    floor = safe_id(after) if after != "0" else "0"
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20), ISNULL(MAX(b.BomId), 0)) AS v\n"
        f"FROM dbo.bom_bom AS b WHERE b.BomId > {floor} AND b.BomType = 1"
        f" AND b.VersionEffDate = CONVERT(datetime, '{safe_date(eff)}', 23) AND b.VersionDesc = N'{MEMO}';\n"
    )


def head_sql(doc: str) -> str:
    """状态|CreateUser|ModifyUser|生效日期|母件损耗率|版本说明是否仍是 MEMO（1/0）|子件行数。"""
    bom = safe_id(doc)
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(10), b.Status)"
        " + N'|' + ISNULL(RTRIM(b.CreateUser), N'') + N'|' + ISNULL(RTRIM(b.ModifyUser), N'')"
        " + N'|' + ISNULL(CONVERT(varchar(10), b.VersionEffDate, 23), N'')"
        " + N'|' + ISNULL(CONVERT(varchar(40), p.ParentScrap), N'')"
        f" + N'|' + CASE WHEN b.VersionDesc = N'{MEMO}' THEN N'1' ELSE N'0' END"
        f" + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.bom_opcomponent AS c WHERE c.BomId = {bom})) AS v\n"
        f"FROM dbo.bom_bom AS b JOIN dbo.bom_parent AS p ON p.BomId = b.BomId WHERE b.BomId = {bom};\n"
    )


def line_sql(doc: str, seq: int) -> str:
    """一行子件：用量分子|用量分母|子件损耗率|变动用量标志 FVFlag|辅计量单位|子件生效日期。"""
    return (
        "SET NOCOUNT ON;\nSELECT TOP (1) N'ROW:' + CONVERT(varchar(40), c.BaseQtyN) + N'|' + CONVERT(varchar(40), c.BaseQtyD)"
        " + N'|' + CONVERT(varchar(40), ISNULL(c.CompScrap, 0)) + N'|' + CONVERT(varchar(10), ISNULL(c.FVFlag, 0))"
        " + N'|' + ISNULL(RTRIM(c.AuxUnitCode), N'') + N'|' + ISNULL(CONVERT(varchar(10), c.EffBegDate, 23), N'') AS v\n"
        f"FROM dbo.bom_opcomponent AS c WHERE c.BomId = {safe_id(doc)} AND c.SortSeq = {safe_seq(seq)}"
        " ORDER BY c.OpComponentId;\n"
    )


def gone_sql(doc: str) -> str:
    """删除后这个 BomId 在版本、母件、子件三张表里的行数合计。"""
    bom = safe_id(doc)
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(20),"
        f" (SELECT COUNT(*) FROM dbo.bom_bom AS b WHERE b.BomId = {bom})"
        f" + (SELECT COUNT(*) FROM dbo.bom_parent AS p WHERE p.BomId = {bom})"
        f" + (SELECT COUNT(*) FROM dbo.bom_opcomponent AS c WHERE c.BomId = {bom})) AS v;\n"
    )


def bom_more_samples() -> list[tuple[str, str]]:
    return [
        ("bom-digits", digits_sql()),
        ("bom-aux-pick", aux_pick_sql()),
        ("bom-max", max_bom_sql()),
        ("bom-orphan", orphan_sql("100", "2034-12-01")),
        ("bom-head", head_sql("100")),
        ("bom-line", line_sql("100", 10)),
        ("bom-gone", gone_sql("100")),
    ]
