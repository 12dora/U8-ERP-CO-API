"""客户专管业务员用例的 SQL：整行快照、另一个业务员、已填专管的客户、行版本列。标识符是常量，拼进 SQL 的值先过白名单。"""

from __future__ import annotations

import re

from co.e2e.casespec.gl_voucher_sql import safe_code

_ALNUM = "NOT LIKE N'%[^0-9A-Za-z]%'"
_ID = re.compile(r"^[1-9][0-9]{0,18}\Z")
_VOLATILE = (
    "N'cmodifyperson', N'dmodifydate', N'pubufts', N'ufts'"
)


# 这几列要原值（比对与还原）；其余列只比哈希：sqlcmd 不带 -y 时长文本列截到 256 字符，整行 XML 会被截断。
PLAIN_COLUMNS = ("cCusPPerson", "cCusDepart", "cCusAbbName")
NIL = "<nil>"


def customer_row_sql(code: str, columns: tuple[str, ...] = PLAIN_COLUMNS) -> str:
    """整行快照，每列一行 ``ROW:列名<TAB>值``：NULL 为 ``<nil>``，不在 ``columns`` 里的列给 ``h:`` + SHA-256。"""
    safe = safe_code(code)
    plain = ", ".join(f"N'{name}'" for name in columns)
    return (
        "SET NOCOUNT ON;\n"
        "SET QUOTED_IDENTIFIER ON;\n"  # XML 数据类型方法要求;sqlcmd 默认关
        "SELECT N'ROW:' + n.value(N'local-name(.)', N'nvarchar(128)') + NCHAR(9) + CASE\n"
        f"  WHEN n.value(N'(@*:nil)[1]', N'nvarchar(5)') = N'true' THEN N'{NIL}'\n"
        f"  WHEN n.value(N'local-name(.)', N'nvarchar(128)') IN ({plain}) THEN n.value(N'.', N'nvarchar(200)')\n"
        "  ELSE N'h:' + CONVERT(nvarchar(64), HASHBYTES('SHA2_256', n.value(N'.', N'nvarchar(max)')), 2)\n"
        "END AS v\n"
        "FROM (SELECT (SELECT t.* FROM dbo.Customer AS t "
        f"WHERE t.cCusCode = N'{safe}' FOR XML RAW(N'r'), ELEMENTS XSINIL, TYPE) AS x) AS s\n"
        "CROSS APPLY x.nodes(N'/r/*') AS q(n);\n"
    )


def other_person_sql(current: str) -> str:
    skip = ""
    try:
        if current:
            skip = f" AND LTRIM(RTRIM(p.cPersonCode)) <> N'{safe_code(current)}'"
    except ValueError:
        skip = ""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(p.cPersonCode)) AS v
FROM dbo.Person AS p
INNER JOIN dbo.Department AS d ON d.cDepCode = p.cDepCode
  AND d.bDepEnd = 1 AND d.dDepEndDate IS NULL
WHERE LTRIM(RTRIM(p.cPersonCode)) {_ALNUM}
{skip}
ORDER BY p.cPersonCode;
"""


def person_one_sql(code: str) -> str:
    safe = safe_code(code)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(p.cPersonCode)) AS v
FROM dbo.Person AS p
WHERE p.cPersonCode = N'{safe}'
  AND LTRIM(RTRIM(p.cPersonCode)) {_ALNUM};
"""


def customer_with_person_sql() -> str:
    """专管业务员非空的客户：customer_person 的 person 不能为空，还原不能清掉原值。"""
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(c.cCusCode)) AS v
FROM dbo.Customer AS c
WHERE LTRIM(RTRIM(c.cCusCode)) {_ALNUM}
  AND NULLIF(LTRIM(RTRIM(c.cCusPPerson)), N'') IS NOT NULL
  AND LTRIM(RTRIM(c.cCusPPerson)) {_ALNUM}
ORDER BY c.cCusCode;
"""


def volatile_sql() -> str:
    return f"""SET NOCOUNT ON;
SELECT N'ROW:' + c.name AS v
FROM sys.columns AS c
WHERE c.object_id = OBJECT_ID(N'dbo.Customer', N'U')
  AND (
    TYPE_NAME(c.system_type_id) IN (N'timestamp', N'rowversion')
    OR LOWER(c.name) IN ({_VOLATILE})
  )
ORDER BY c.column_id;
"""


def person_dept_sql(code: str) -> str:
    safe = safe_code(code)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(ISNULL(p.cDepCode, N''))) AS v
FROM dbo.Person AS p
WHERE p.cPersonCode = N'{safe}';
"""


def dept_usable_sql(code: str) -> str:
    safe = safe_code(code)
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(d.cDepCode)) AS v
FROM dbo.Department AS d
WHERE d.cDepCode = N'{safe}'
  AND d.bDepEnd = 1 AND d.dDepEndDate IS NULL;
"""


def customer_auth_sql(code: str) -> str:
    safe = safe_code(code)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(nvarchar(20), a.autoId) + NCHAR(9)"
        " + LTRIM(RTRIM(ISNULL(a.Privilege_ID, N''))) + NCHAR(9)"
        " + CONVERT(nvarchar(10), a.Privilege_Type) AS v\n"
        "FROM dbo.Customer_Auth AS a\n"
        f"WHERE a.Account_ID = N'{safe}'\n"
        "ORDER BY a.autoId;\n"
    )


def _safe_id(value: str) -> str:
    if _ID.fullmatch(value or "") is None:
        raise ValueError("autoId 不合法")
    return value


def clear_person_sql(code: str) -> str:
    """测试账套还原：原值为空时把专管业务员清回 NULL（customer_person 接口不能写空值）。"""
    safe = safe_code(code)
    return (
        "SET NOCOUNT ON;\n"
        "UPDATE dbo.Customer SET cCusPPerson = NULL\n"
        f"WHERE cCusCode = N'{safe}';\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def restore_dept_sql(code: str, dept: str) -> str:
    safe = safe_code(code)
    if not dept:
        return (
            "SET NOCOUNT ON;\n"
            "UPDATE dbo.Customer SET cCusDepart = NULL\n"
            f"WHERE cCusCode = N'{safe}'\n"
            "  AND NULLIF(LTRIM(RTRIM(ISNULL(cCusDepart, N''))), N'') IS NOT NULL;\n"
            "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
        )
    want = safe_code(dept)
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE dbo.Customer SET cCusDepart = N'{want}'\n"
        f"WHERE cCusCode = N'{safe}'\n"
        f"  AND LTRIM(RTRIM(ISNULL(cCusDepart, N''))) <> N'{want}';\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def restore_auth_sql(code: str, auto_id: str, priv: str) -> str:
    safe = safe_code(code)
    ident = _safe_id(auto_id)
    want = safe_code(priv) if priv else ""
    value = "NULL" if want == "" else f"N'{want}'"
    return (
        "SET NOCOUNT ON;\n"
        f"UPDATE dbo.Customer_Auth SET Privilege_ID = {value}\n"
        f"WHERE Account_ID = N'{safe}' AND autoId = {ident} AND Privilege_Type = 0;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def delete_auth_sql(code: str, auto_id: str) -> str:
    safe = safe_code(code)
    ident = _safe_id(auto_id)
    return (
        "SET NOCOUNT ON;\n"
        "DELETE FROM dbo.Customer_Auth\n"
        f"WHERE Account_ID = N'{safe}' AND autoId = {ident} AND Privilege_Type = 0;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def pperson_samples() -> list[tuple[str, str]]:
    return [
        ("core-pperson-row", customer_row_sql("C900001")),
        ("core-pperson-other", other_person_sql("EX040001")),
        ("core-pperson-person", person_one_sql("EX040002")),
        ("core-pperson-held", customer_with_person_sql()),
        ("core-pperson-volatile", volatile_sql()),
        ("core-pperson-auth", customer_auth_sql("C900001")),
        ("core-pperson-dept", person_dept_sql("EX040001")),
        ("core-pperson-usable", dept_usable_sql("D901")),
        ("core-pperson-restore-dept", restore_dept_sql("C900001", "D901")),
        ("core-pperson-restore-auth", restore_auth_sql("C900001", "1", "D901")),
        ("core-pperson-delete-auth", delete_auth_sql("C900001", "2")),
    ]
