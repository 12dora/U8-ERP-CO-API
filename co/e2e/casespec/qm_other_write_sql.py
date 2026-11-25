"""其他报检单新增、其他检验单生单用例（qm_other_write）的挑单与回读 SQL。只读；标识符是常量或按正则核过的
自定义项列名，主键只接受正整数（safe_id）再拼进 SQL。

挑单只认账套里真实的「其他报检单 → 其他检验单」（QM11 → QM15）：存货取最近一张其他检验单所在报检单行的存货，
检验员、检验部门、检验方案照抄这张检验单；模板必输的自定义项照抄最近一张同类单据的值（defines_sql）。
qm_other_write_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re

from co.e2e.casespec.mo_close_sql import safe_id

INSPECT_VT = 361
CHECK_VT = 365
_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"
_DEFINE = re.compile(r"^(c|ch)define([1-9][0-9]?)\Z")
# 模板 VT → (表头表, 单据类型)。
_TABLES = {INSPECT_VT: ("QMINSPECTVOUCHER", "QM11"), CHECK_VT: ("QMCHECKVOUCHER", "QM15")}


def pick_sql() -> str:
    """一行：<存货>|<检验员>|<检验部门>|<检验方案编码>。最近一张有检验方案的其他检验单及其报检单行；两个模板（VT 361 / 365）
    都要在账套里。挑不到时没有行。"""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(b.CINVCODE)) + N'|' + LTRIM(RTRIM(c.CCHECKPERSONCODE))
  + N'|' + LTRIM(RTRIM(c.CDEPCODE)) + N'|' + LTRIM(RTRIM(p.CPROJECTCODE)) AS v
FROM dbo.QMCHECKVOUCHER AS c
JOIN dbo.QMINSPECTVOUCHERS AS b ON b.AUTOID = c.INSPECTAUTOID
JOIN dbo.QMINSPECTVOUCHER AS i ON i.ID = b.ID AND i.CVOUCHTYPE = N'QM11'
JOIN dbo.QMCHECKPROJECT AS p ON p.ID = c.PROJECTID AND ISNULL(p.BSTOPUSE, 0) = 0
JOIN dbo.Inventory AS n ON n.cInvCode = b.CINVCODE
JOIN dbo.Person AS s ON s.cPersonCode = c.CCHECKPERSONCODE
JOIN dbo.Department AS d ON d.cDepCode = c.CDEPCODE
WHERE c.CVOUCHTYPE = N'QM15'
  AND LTRIM(RTRIM(b.CINVCODE)) {_SAFE}
  AND LTRIM(RTRIM(c.CCHECKPERSONCODE)) {_SAFE}
  AND LTRIM(RTRIM(c.CDEPCODE)) {_SAFE}
  AND LTRIM(RTRIM(p.CPROJECTCODE)) {_SAFE}
  AND EXISTS (SELECT 1 FROM dbo.voucheritems AS t WHERE t.VT_ID = {INSPECT_VT})
  AND EXISTS (SELECT 1 FROM dbo.voucheritems AS t WHERE t.VT_ID = {CHECK_VT})
ORDER BY c.ID DESC;
"""


def _column(name: str) -> str:
    found = _DEFINE.fullmatch(name.lower())
    if found is None:
        raise ValueError("不是自定义项列名：" + name)
    return ("x." if found.group(1) == "ch" else "h.") + name.lower()


def defines_sql(vt: int, names: list[str]) -> str:
    """一行：最近一张同类单据上这些自定义项的值（按 names 次序以 | 连接）。只挑每一项都非空、不含 | 和换行的单据；
    扩展自定义项 chdefine* 在 <表头表>_extradefine。挑不到时没有行。"""
    if vt not in _TABLES or not names:
        raise ValueError("模板 VT 或字段不对")
    table, biz = _TABLES[vt]
    cols = [_column(name) for name in names]
    joined = " + N'|' + ".join(f"LTRIM(RTRIM(CONVERT(nvarchar(120), {col})))" for col in cols)
    extra = f"\nLEFT JOIN dbo.{table}_extradefine AS x ON x.ID = h.ID" if any(col.startswith("x.") for col in cols) else ""
    checks = "".join(
        f"\n  AND LTRIM(RTRIM(ISNULL(CONVERT(nvarchar(120), {col}), N''))) <> N''"
        f"\n  AND CHARINDEX(N'|', CONVERT(nvarchar(120), {col})) = 0"
        f"\n  AND CHARINDEX(CHAR(10), CONVERT(nvarchar(120), {col})) = 0"
        for col in cols
    )
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {joined} AS v
FROM dbo.{table} AS h{extra}
WHERE h.CVOUCHTYPE = N'{biz}'{checks}
ORDER BY h.ID DESC;
"""


def max_sql() -> str:
    """一行：<其他报检单最大 ID>（没有时 0）。新增前记下，结果不明时按它找回本用例建的单据。"""
    return """SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(20), ISNULL(MAX(h.ID), 0)) AS v
FROM dbo.QMINSPECTVOUCHER AS h WHERE h.CVOUCHTYPE = N'QM11';
"""


def adopt_sql(before: str, inv: str) -> str:
    """每行一个 ID：before 之后新建、表体有该存货的其他报检单（新增结果不明时清理用）。"""
    floor = "0" if before == "0" else safe_id(before)
    if re.fullmatch(r"[0-9A-Za-z._-]{1,60}", inv or "") is None:
        raise ValueError("存货编码不合法")
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(20), h.ID) AS v
FROM dbo.QMINSPECTVOUCHER AS h
WHERE h.CVOUCHTYPE = N'QM11' AND h.ID > {floor}
  AND EXISTS (SELECT 1 FROM dbo.QMINSPECTVOUCHERS AS b WHERE b.ID = h.ID AND b.CINVCODE = N'{inv}')
ORDER BY h.ID;
"""


def state_sql(inspect: str) -> str:
    """一行：<报检单张数>|<报检单审核人>|<第一行 AUTOID>|<该行 BFLAG，NULL 记 N>|<挂在本单上的其他检验单张数>
    |<最新检验单 ID>|<检验单审核人>|<IsWfControlled>|<iVerifyStateNew>|<INSPECTAUTOID>|<报检单表体行数>。"""
    doc = safe_id(inspect)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.QMINSPECTVOUCHER AS a WHERE a.ID = {doc}))
  + N'|' + ISNULL((SELECT LTRIM(RTRIM(a.CVERIFIER)) FROM dbo.QMINSPECTVOUCHER AS a WHERE a.ID = {doc}), N'')
  + N'|' + ISNULL(CONVERT(varchar(20), l.AUTOID), N'')
  + N'|' + ISNULL(CONVERT(varchar(5), CONVERT(int, l.BFLAG)), N'N')
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.QMCHECKVOUCHER AS x WHERE x.INSPECTID = {doc}))
  + N'|' + ISNULL(CONVERT(varchar(20), c.ID), N'')
  + N'|' + ISNULL(LTRIM(RTRIM(c.CVERIFIER)), N'')
  + N'|' + ISNULL(CONVERT(varchar(5), CONVERT(int, c.IsWfControlled)), N'')
  + N'|' + ISNULL(CONVERT(varchar(5), c.iVerifyStateNew), N'')
  + N'|' + ISNULL(CONVERT(varchar(20), c.INSPECTAUTOID), N'')
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.QMINSPECTVOUCHERS AS z WHERE z.ID = {doc})) AS v
FROM (SELECT 1 AS one) AS o
OUTER APPLY (SELECT TOP (1) b.AUTOID, b.BFLAG FROM dbo.QMINSPECTVOUCHERS AS b WHERE b.ID = {doc} ORDER BY b.AUTOID) AS l
OUTER APPLY (
  SELECT TOP (1) y.ID, y.CVERIFIER, y.IsWfControlled, y.iVerifyStateNew, y.INSPECTAUTOID
  FROM dbo.QMCHECKVOUCHER AS y WHERE y.INSPECTID = {doc} ORDER BY y.ID DESC
) AS c;
"""


def gone_sql(inspect: str, check: str) -> str:
    """一行：<报检单表头>|<报检单表体>|<检验单表头>|<检验项目> 的行数（按本用例建的两个 ID）。"""
    ins = safe_id(inspect)
    chk = safe_id(check)
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.QMINSPECTVOUCHER AS a WHERE a.ID = {ins}))
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.QMINSPECTVOUCHERS AS b WHERE b.ID = {ins}))
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.QMCHECKVOUCHER AS c WHERE c.ID = {chk}))
  + N'|' + CONVERT(varchar(10), (SELECT COUNT(*) FROM dbo.QMCHECKVOUCHERS AS d WHERE d.ID = {chk})) AS v;
"""


def qm_other_write_samples() -> list[tuple[str, str]]:
    return [
        ("qm-other-pick", pick_sql()),
        ("qm-other-defines-361", defines_sql(INSPECT_VT, ["CDEFINE10", "chdefine16"])),
        ("qm-other-defines-365", defines_sql(CHECK_VT, ["chdefine11"])),
        ("qm-other-max", max_sql()),
        ("qm-other-adopt", adopt_sql("100", "I01")),
        ("qm-other-state", state_sql("100")),
        ("qm-other-gone", gone_sql("100", "200")),
    ]
