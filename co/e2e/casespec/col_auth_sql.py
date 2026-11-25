"""字段权限遮蔽（col_auth）的 SQL：查字段权限开关、临时打开并恢复原值、查已有的本人行、临时插入字段权限行、按原样删掉。

只动 AA_ColumnAuth 里「测试操作员本人（isUserGroup = 0）、指定对象、指定字段、cFuncID = N」的行；插入前另查已有行，
有就不插（调用方记 N/A），所以删除删到的只会是本用例插入的行。开关只 UPDATE 已有的 AA_BusObject_base 行
（langid zh-CN、iAuthType = 1），不插入对象行。拼进 SQL 的只有经 nlit 校验的操作员编码和
本模块校验过的对象号、字段名、开关原值 0/1。每行输出 ROW:…（flip_util.row 解析）。
col_auth_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re

from co.e2e.casespec.perm_check_sql import nlit

_KEY = re.compile(r"^[0-9A-Za-z_]{1,50}\Z")
_FLD = re.compile(r"^[A-Za-z][0-9A-Za-z_]{0,99}\Z")


def _key(text: str) -> str:
    if type(text) is not str or _KEY.fullmatch(text) is None:
        raise ValueError("对象号不能拼进 SQL")
    return "N'" + text + "'"


def _fields(fields: tuple[str, ...]) -> list[str]:
    if not fields or any(type(f) is not str or _FLD.fullmatch(f) is None for f in fields):
        raise ValueError("字段名不能拼进 SQL")
    return ["N'" + f + "'" for f in fields]


def _mine(alias: str, op: str, key: str, fields: tuple[str, ...]) -> str:
    return (
        f"{alias}.isUserGroup = 0 AND {alias}.cUserId = {nlit(op)} AND {alias}.cKey = {_key(key)}"
        f" AND {alias}.cFld IN ({', '.join(_fields(fields))})"
    )


def _switch_where(alias: str, key: str) -> str:
    prefix = alias + "." if alias else ""
    return f"{prefix}langid = N'zh-CN' AND {prefix}iAuthType = 1 AND {prefix}cBusObId = {_key(key)}"


def _flag(value: int) -> str:
    if type(value) is not int or value not in (0, 1):
        raise ValueError("字段权限开关原值不能拼进 SQL")
    return str(value)


def switch_sql(key: str) -> str:
    """一行：<该对象的列表 / 卡片对象行数>|<其中打开字段权限控制的行数>（iAuthType = 1）。"""
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(nvarchar(12), COUNT(*)) + N'|'\n"
        "  + CONVERT(nvarchar(12), ISNULL(SUM(CASE WHEN b.bAuthControl = 1 THEN 1 ELSE 0 END), 0)) AS v\n"
        "FROM AA_BusObject_base b\n"
        f"WHERE {_switch_where('b', key)};\n"
    )


def switch_on_sql(key: str) -> str:
    """打开该对象的字段权限开关。一行：<改动行数>|<改后 bAuthControl=1 的行数>。"""
    return (
        "SET NOCOUNT ON;\n"
        "UPDATE AA_BusObject_base SET bAuthControl = 1\n"
        f"WHERE {_switch_where('', key)};\n"
        "SELECT N'ROW:' + CONVERT(nvarchar(12), @@ROWCOUNT) + N'|'\n"
        "  + CONVERT(nvarchar(12), (SELECT COUNT(*) FROM AA_BusObject_base b\n"
        f"     WHERE {_switch_where('b', key)} AND b.bAuthControl = 1)) AS v;\n"
    )


def switch_restore_sql(key: str, value: int) -> str:
    """把该对象的字段权限开关恢复成原值。一行：<改动行数>。"""
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE AA_BusObject_base SET bAuthControl = {_flag(value)}\n"
        f"WHERE {_switch_where('', key)};\n"
        "SELECT N'ROW:' + CONVERT(nvarchar(12), @@ROWCOUNT) AS v;\n"
    )


def existing_sql(op: str, key: str, fields: tuple[str, ...]) -> str:
    """一行：<测试操作员本人在这些字段上已有的行数>（不论 cFuncID）。"""
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(nvarchar(12), COUNT(*)) AS v FROM AA_ColumnAuth a\n"
        "WHERE " + _mine("a", op, key, fields) + ";\n"
    )


def insert_sql(op: str, key: str, fields: tuple[str, ...]) -> str:
    """插入本人的不可见行（cFuncID = N），已有的不插；一个事务。一行：OK|<插入行数> 或 ERR|<原文>。"""
    values = ", ".join("(" + f + ")" for f in _fields(fields))
    return (
        "SET NOCOUNT ON;\nSET XACT_ABORT ON;\nDECLARE @n int = 0;\n"
        "BEGIN TRY\n  BEGIN TRANSACTION;\n"
        "  INSERT INTO AA_ColumnAuth (cUserId, cKey, cFld, isUserGroup, cFuncID)\n"
        f"  SELECT {nlit(op)}, {_key(key)}, f.fld, 0, N'N' FROM (VALUES {values}) AS f (fld)\n"
        "  WHERE NOT EXISTS (SELECT 1 FROM AA_ColumnAuth x WHERE x.isUserGroup = 0\n"
        f"    AND x.cUserId = {nlit(op)} AND x.cKey = {_key(key)} AND x.cFld = f.fld);\n"
        "  SET @n = @@ROWCOUNT;\n"
        "  COMMIT TRANSACTION;\n"
        "  SELECT N'ROW:OK|' + CONVERT(varchar(12), @n) AS v;\n"
        "END TRY\nBEGIN CATCH\n  IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;\n"
        "  SELECT N'ROW:ERR|' + LEFT(REPLACE(ERROR_MESSAGE(), N'|', N'/'), 300) AS v;\nEND CATCH;\n"
    )


def delete_sql(op: str, key: str, fields: tuple[str, ...]) -> str:
    """删掉本人在这些字段上的不可见行；一行：<删的行数>|<删后本人在这些字段上剩下的行数>。"""
    return (
        "SET NOCOUNT ON;\nDECLARE @n int = 0;\n"
        "DELETE a FROM AA_ColumnAuth a WHERE " + _mine("a", op, key, fields) + " AND a.cFuncID = N'N';\n"
        "SET @n = @@ROWCOUNT;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @n) + N'|' + CONVERT(varchar(12),\n"
        "  (SELECT COUNT(*) FROM AA_ColumnAuth a WHERE " + _mine("a", op, key, fields) + ")) AS v;\n"
    )


def col_auth_samples() -> list[tuple[str, str]]:
    fields = ("iPrice", "iUnitCost")
    return [
        ("col-switch", switch_sql("0303")),
        ("col-switch-on", switch_on_sql("07")),
        ("col-switch-restore", switch_restore_sql("24", 0)),
        ("col-existing", existing_sql("op-001", "0303", fields)),
        ("col-insert", insert_sql("op-001", "0303", fields)),
        ("col-delete", delete_sql("op-001", "24", ("iPrice", "iorimoney", "isum"))),
    ]
