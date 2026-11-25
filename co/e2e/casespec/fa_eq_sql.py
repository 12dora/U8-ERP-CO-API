"""固定资产卡片与设备台账用例（fa_eq）的挑单与回读 SQL。

只读。每条 SQL 输出一行 ROW:<值>（多列用 | 分隔）。拼进 SQL 的编码只接受 [0-9A-Za-z._-]（safe_lot），
挑出的编码也在 SQL 里按同一字符集过滤，免得回到 Python 后再拼进下一条 SQL 时出错。
"""

from __future__ import annotations

from co.e2e.casespec.stock_plus_sql import safe_lot

_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"


def _one(expr: str, frm: str = "") -> str:
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v" + ("\n" + frm if frm else "") + ";\n"


def _txt(expr: str, width: int = 40) -> str:
    return f"ISNULL(LTRIM(RTRIM(CONVERT(nvarchar({width}), {expr}))), N'')"


def _acc(name: str) -> str:
    return f"(SELECT MAX(LTRIM(RTRIM(CONVERT(nvarchar(40), a.cValue)))) FROM dbo.AccInformation AS a WHERE a.cSysID = N'FA' AND a.cName = N'{name}')"


def fa_state_sql() -> str:
    """固定资产当前期间：iLastPeriod|dWritableDate（yyyy-mm-dd）|该期间 GL_mend.bflag_FA（0 / 1，没有这一行为空）|
    同年度里早于当前期间、已结账的最大期间（没有为空）。没启用固定资产时前两段为空。"""
    flag = (
        "(SELECT TOP (1) CONVERT(varchar(1), CONVERT(int, ISNULL(g.bflag_FA, 0))) FROM dbo.GL_mend AS g"
        " WHERE g.iyear = YEAR(x.d) AND g.iperiod = x.p)"
    )
    closed = (
        "(SELECT CONVERT(varchar(4), MAX(g.iperiod)) FROM dbo.GL_mend AS g"
        " WHERE g.iyear = YEAR(x.d) AND g.iperiod BETWEEN 1 AND 12 AND g.iperiod < x.p AND ISNULL(g.bflag_FA, 0) = 1)"
    )
    expr = (
        "ISNULL(CONVERT(varchar(4), x.p), N'')\n  + N'|' + ISNULL(CONVERT(varchar(10), x.d, 23), N'')"
        f"\n  + N'|' + ISNULL({flag}, N'')\n  + N'|' + ISNULL({closed}, N'')"
    )
    frm = (
        f"FROM (SELECT TRY_CONVERT(int, {_acc('iLastPeriod')}) AS p,"
        f" CONVERT(date, TRY_CONVERT(datetime, {_acc('dWritableDate')})) AS d) AS x"
    )
    return _one(expr, frm)


def fa_depr_sql(year: int, period: int) -> str:
    """固定资产 year 年第 period 期已计提折旧的行数（fa_DeprList；桥撤销本期新增前查同一张表）。"""
    if not (2000 <= int(year) <= 2099 and 1 <= int(period) <= 12):
        raise ValueError("年度或期间不合法")
    where = f"l.iyear = {int(year)} AND l.iPeriod = {int(period)}"
    return _one(f"CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.fa_DeprList AS l WHERE {where}))")


def fa_ref_sql() -> str:
    """参照卡片：最新一张在役、单部门（比例 1）的卡片的当前版本，类别是末级（没有以它为前缀的下级类别）、部门是末级。
    类别|增加方式|使用状况|折旧方法|使用部门|使用年限（月）；没有为空。"""
    cols = ("h.sTypeNum", "h.sOrgAddID", "h.sStatusID", "h.sDeprMethodID", "sc.sDeptNum")
    expr = "\n  + N'|' + ".join(_txt(col) for col in cols) + "\n  + N'|' + CONVERT(varchar(12), ISNULL(h.lLife, 0))"
    safe = "".join(f"\n  AND {_txt(col)} <> N'' AND {_txt(col)} {_SAFE}" for col in cols)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + {expr} AS v
FROM dbo.fa_Cards AS h
JOIN (SELECT v.sCardNum AS num, MAX(v.sCardID) AS id FROM dbo.fa_Cards AS v GROUP BY v.sCardNum) AS cur ON cur.id = h.sCardID
JOIN dbo.fa_DeptScale AS sc ON sc.sCardNum = h.sCardNum AND sc.lOptID = h.lOptID
WHERE h.dDisposeDate IS NULL AND ISNULL(sc.dblScale, 0) = 1{safe}
  AND (SELECT COUNT(*) FROM dbo.fa_DeptScale AS s2 WHERE s2.sCardNum = h.sCardNum AND s2.lOptID = h.lOptID) = 1
  AND NOT EXISTS (SELECT 1 FROM dbo.fa_AssetTypes AS t WHERE t.sNum LIKE LTRIM(RTRIM(h.sTypeNum)) + N'_%')
  AND EXISTS (SELECT 1 FROM dbo.Department AS d WHERE d.cDepCode = sc.sDeptNum AND ISNULL(d.bDepEnd, 0) = 1)
ORDER BY h.sCardID DESC;
"""


def fa_mark_sql(asset: str) -> str:
    """卡片表最大 sCardID（空表为 0）|资产编号是 asset 的行数|其中最新一行的卡片编号（没有为空）。"""
    key = safe_lot(asset)
    expr = (
        "CONVERT(varchar(20), ISNULL((SELECT MAX(c.sCardID) FROM dbo.fa_Cards AS c), 0))"
        f"\n  + N'|' + CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.fa_Cards AS c WHERE c.sAssetNum = N'{key}'))"
        "\n  + N'|' + ISNULL((SELECT TOP (1) LTRIM(RTRIM(c.sCardNum)) FROM dbo.fa_Cards AS c"
        f" WHERE c.sAssetNum = N'{key}' ORDER BY c.sCardID DESC), N'')"
    )
    return _one(expr)


def eq_ref_sql() -> str:
    """设备台账：行数|最新一行（AutoID 最大）的设备类别|使用部门|设备状态（编码不合字符集时为空）。"""
    cols = ("e.cEQTypeCode", "e.cDepCode", "e.cStaCode")
    picked = "\n  + N'|' + ".join(f"CASE WHEN {_txt(col)} {_SAFE} THEN {_txt(col)} ELSE N'' END" for col in cols)
    expr = f"CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.EQ_EQData))\n  + N'|' + ISNULL((SELECT TOP (1) {picked}"
    return _one(expr + "\n  FROM dbo.EQ_EQData AS e ORDER BY e.AutoID DESC), N'||')")


def eq_count_sql(code: str) -> str:
    """设备编码是 code 的行数。"""
    return _one(f"CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.EQ_EQData AS e WHERE e.cEQCode = N'{safe_lot(code)}'))")


def fa_eq_samples() -> list[tuple[str, str]]:
    return [
        ("fa-state", fa_state_sql()),
        ("fa-ref", fa_ref_sql()),
        ("fa-depr", fa_depr_sql(2026, 9)),
        ("fa-mark", fa_mark_sql("903011230")),
        ("eq-ref", eq_ref_sql()),
        ("eq-count", eq_count_sql("ZZ03112300")),
    ]
