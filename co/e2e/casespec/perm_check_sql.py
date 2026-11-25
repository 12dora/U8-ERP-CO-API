"""权限核对（perm_check）的对照 SQL：只读账套库 AA_*（含字段权限 AA_ColumnAuth）、AccInformation，与桥 PermLoad 的写法不同、按同一口径独立再算。

拼进 SQL 的只有本模块常量（对象名）和经 nlit 校验、转义的操作员 / 角色编码。每行输出 ROW:<标记>|…（perm_oracle 解析）。
编码本身不输出：受控对象只输出「去空格、转大写、去重」后的个数和 SHA-256 前 7 字节之和（perm_oracle.digest 同一规则）。
"""

from __future__ import annotations

import re

from co.e2e.perm_oracle import CODE_SOURCES, COLUMN_TAG, ITEM

_BAD = re.compile(r"[\x00-\x1f\x7f]|\$\(")
_MAX = 40


def nlit(text: str) -> str:
    """N'…' 字面量：去空格，拒绝空串、控制字符、超长；单引号成对转义。"""
    value = (text or "").strip()
    if value == "" or len(value) > _MAX or _BAD.search(value):
        raise ValueError("操作员或角色编码不能拼进 SQL")
    return "N'" + value.replace("'", "''") + "'"


def lane_sql() -> str:
    """S|<打开数据权限的对象>，G|<总账 bQryCtlSubj>（没有这行选项时 G 为空）。"""
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:S|' + x.o AS v FROM (SELECT DISTINCT LOWER(LTRIM(RTRIM(b.cBusObId))) AS o FROM AA_BusObject_base b\n"
        "  WHERE b.langid = N'zh-CN' AND b.iAuthType = 0 AND b.bAuthControl = 1) x;\n"
        "SELECT N'ROW:G|' + ISNULL((SELECT TOP 1 LTRIM(RTRIM(CONVERT(nvarchar(20), i.cValue))) FROM AccInformation i\n"
        "  WHERE i.cSysID = N'GL' AND i.cName = N'bQryCtlSubj'), N'') AS v;\n"
    )


def _who(alias: str, group_col: str, user_col: str, op: str, roles: list[str]) -> str:
    own = f"({alias}.{group_col} = 0 AND {alias}.{user_col} = {nlit(op)})"
    if not roles:
        return "(" + own + ")"
    listed = ", ".join(nlit(role) for role in roles)
    return f"({own} OR ({alias}.{group_col} = 1 AND {alias}.{user_col} IN ({listed})))"


def op_sql(op: str, roles: list[str]) -> str:
    """A|<数据权限管理员对象>，C|<对象>|<个数>|<摘要和>（只出现有编码的对象）。"""
    objs = ", ".join("N'" + obj + "'" for obj in CODE_SOURCES)
    key = (
        "CONVERT(nvarchar(450), CASE WHEN LOWER(LTRIM(RTRIM(a.cBusObId))) = N'" + ITEM + "'"
        " THEN UPPER(LTRIM(RTRIM(ISNULL(a.cClassCode, N'')))) + NCHAR(31) ELSE N'' END + UPPER(LTRIM(RTRIM(a.cACCode))))"
    )
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:A|' + x.o AS v FROM (SELECT DISTINCT LOWER(LTRIM(RTRIM(h.CBusObId))) AS o FROM AA_holdBusobject h\n"
        "  WHERE h.iAuthType = 0 AND h.iAdmin = 1 AND " + _who("h", "isUserGroup", "cUserId", op, roles) + ") x;\n"
        "SELECT N'ROW:C|' + x.obj + N'|' + CONVERT(nvarchar(20), COUNT_BIG(*)) + N'|'\n"
        "  + CONVERT(nvarchar(50), SUM(CONVERT(decimal(38, 0), CONVERT(bigint, SUBSTRING(HASHBYTES('SHA2_256', x.k), 1, 7)))))"
        " AS v\n"
        "FROM (SELECT DISTINCT LOWER(LTRIM(RTRIM(a.cBusObId))) AS obj, " + key + " AS k FROM AA_HoldAuth a\n"
        "  WHERE a.cBusObId IN (" + objs + ") AND ISNULL(a.cFuncId, N'') LIKE N'%R%'\n"
        "  AND NULLIF(LTRIM(RTRIM(a.cACCode)), N'') IS NOT NULL\n"
        "  AND " + _who("a", "isUserGroup", "cUserId", op, roles) + ") x\n"
        "GROUP BY x.obj;\n"
    )


def columns_sql(op: str, roles: list[str]) -> str:
    """字段权限的原始行 F|<isUserGroup>|<cKey>|<cFld>|<cFuncID>：本人和所属角色、字段权限开关打开的对象（合并在 perm_oracle）。"""
    def cell(expr: str) -> str:
        return "REPLACE(LTRIM(RTRIM(ISNULL(" + expr + ", N''))), N'|', N'/')"

    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:" + COLUMN_TAG + "|' + CONVERT(nvarchar(1), CONVERT(int, a.isUserGroup)) + N'|' + "
        + cell("a.cKey") + " + N'|' + " + cell("a.cFld") + " + N'|' + " + cell("a.cFuncID") + " AS v\n"
        "FROM AA_ColumnAuth a\n"
        "WHERE EXISTS (SELECT 1 FROM AA_BusObject_base b WHERE b.cBusObId = a.cKey AND b.langid = N'zh-CN'\n"
        "    AND b.iAuthType = 1 AND b.bAuthControl = 1)\n"
        "  AND " + _who("a", "isUserGroup", "cUserId", op, roles) + ";\n"
    )


def stray_users_sql(limit: int) -> str:
    """账套库里出现过、可能没有功能授权的操作员（数据权限行、人员对照）：U|<编码>。perm-empty-ops 从中挑。"""
    top = int(limit)
    if not 1 <= top <= 500:
        raise ValueError("limit 超出范围")
    return (
        "SET NOCOUNT ON;\n"
        f"SELECT TOP ({top}) N'ROW:U|' + x.u AS v FROM (\n"
        "  SELECT DISTINCT LTRIM(RTRIM(a.cUserId)) AS u FROM AA_HoldAuth a WHERE a.isUserGroup = 0\n"
        "  UNION SELECT DISTINCT LTRIM(RTRIM(p.cUser_Id)) FROM UserHrPersonContro p) x\n"
        "WHERE NULLIF(x.u, N'') IS NOT NULL ORDER BY x.u;\n"
    )


def perm_samples() -> list[tuple[str, str]]:
    return [
        ("perm-lane", lane_sql()),
        ("perm-op", op_sql("op-001", ["示例角色", "O'Brien"])),
        ("perm-op-noroles", op_sql("op-002", [])),
        ("perm-stray", stray_users_sql(200)),
        ("perm-cols", columns_sql("op-001", ["示例角色"])),
        ("perm-cols-noroles", columns_sql("op-002", [])),
    ]
