"""客户开票单位 `customer-invoice-company-cycle` 的 SQL。

标识符是常量，拼进 SQL 的客户编码先过白名单（safe_code）。整行快照复用 arc_cus_person_sql.customer_row_sql，
这里只定「读原值的列」。SQL 文本里不出现 sqlguard 拒绝的词（还原一词只写在 Python 名字里）。
"""

from __future__ import annotations

from co.e2e.casespec.arc_cus_person_sql import customer_row_sql
from co.e2e.casespec.gl_voucher_sql import safe_code

_ALNUM = "NOT LIKE N'%[^0-9A-Za-z]%'"
# 要原值的客户列：默认开票单位和修改人；其余列只比哈希。
HEADER_COLUMNS = ("cInvoiceCompany", "cModifyPerson")
# 一个客户最多挑几条开票单位（快照输出不要太长）。
MAX_ROWS = 6


def header_sql(code: str) -> str:
    return customer_row_sql(code, HEADER_COLUMNS)


def pick_sql() -> str:
    """挑一个客户和另一个还不是它开票单位的客户：输出 ``客户编码<TAB>另一个客户编码``。

    客户要有 1 到 MAX_ROWS 行开票单位、恰一个默认，且客户表头的开票单位等于这个默认（数据一致，路由前后才能比对）；
    另一个客户优先选没停用的（停用的也允许，路由不查）。
    """
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(c.cCusCode)) + NCHAR(9) + LTRIM(RTRIM(o.cCusCode)) AS v
FROM dbo.Customer AS c
INNER JOIN dbo.Customer AS o ON o.cCusCode <> c.cCusCode
WHERE LTRIM(RTRIM(c.cCusCode)) {_ALNUM} AND LTRIM(RTRIM(o.cCusCode)) {_ALNUM}
  AND (SELECT COUNT(*) FROM dbo.sa_invoicecustomers AS s WHERE s.ccuscode = c.cCusCode) BETWEEN 1 AND {MAX_ROWS}
  AND (SELECT COUNT(*) FROM dbo.sa_invoicecustomers AS s WHERE s.ccuscode = c.cCusCode AND s.bdefault = 1) = 1
  AND EXISTS (SELECT 1 FROM dbo.sa_invoicecustomers AS s WHERE s.ccuscode = c.cCusCode AND s.bdefault = 1
    AND LTRIM(RTRIM(s.cinvoicecompany)) = LTRIM(RTRIM(c.cInvoiceCompany)))
  AND NOT EXISTS (SELECT 1 FROM dbo.sa_invoicecustomers AS s WHERE s.ccuscode = c.cCusCode
    AND LTRIM(RTRIM(s.cinvoicecompany)) = LTRIM(RTRIM(o.cCusCode)))
ORDER BY CASE WHEN o.dEndDate IS NULL THEN 0 ELSE 1 END, c.cCusCode, o.cCusCode;
"""


def rows_sql(code: str) -> str:
    """客户全部开票单位行：``ROW:开票单位编码<TAB>默认标志``，按自增列排。"""
    safe = safe_code(code)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + LTRIM(RTRIM(s.cinvoicecompany)) + NCHAR(9)\n"
        "  + CONVERT(varchar(1), ISNULL(s.bdefault, 0)) AS v\n"
        f"FROM dbo.sa_invoicecustomers AS s WHERE s.ccuscode = N'{safe}' ORDER BY s.autoid;\n"
    )


def missing_sql() -> str:
    """不存在的开票单位编码：Customer 里没有的 ``ZZ`` 加数字，输出一个。"""
    return """SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + n.v AS v
FROM (VALUES (N'ZZNOINV01'), (N'ZZNOINV02'), (N'ZZNOINV03')) AS n(v)
WHERE NOT EXISTS (SELECT 1 FROM dbo.Customer AS c WHERE LTRIM(RTRIM(c.cCusCode)) = n.v)
ORDER BY n.v;
"""


def delete_pair_sql(code: str, company: str) -> str:
    safe, other = safe_code(code), safe_code(company)
    return (
        "SET NOCOUNT ON;\n"
        "DELETE FROM dbo.sa_invoicecustomers\n"
        f"WHERE ccuscode = N'{safe}' AND LTRIM(RTRIM(cinvoicecompany)) = N'{other}';\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def reset_default_sql(code: str, company: str) -> str:
    """路由还原不了时的兜底（测试账套上）：默认标志和客户表头写回快照里的默认开票单位。"""
    safe, other = safe_code(code), safe_code(company)
    return (
        "SET NOCOUNT ON;\n"
        "UPDATE dbo.sa_invoicecustomers SET bdefault = CASE WHEN LTRIM(RTRIM(cinvoicecompany)) = "
        f"N'{other}' THEN 1 ELSE 0 END WHERE ccuscode = N'{safe}';\n"
        f"UPDATE dbo.Customer SET cInvoiceCompany = N'{other}' WHERE cCusCode = N'{safe}';\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), @@ROWCOUNT) AS v;\n"
    )


def cusinvoice_samples() -> list[tuple[str, str]]:
    return [
        ("core-cusinv-pick", pick_sql()),
        ("core-cusinv-rows", rows_sql("C900001")),
        ("core-cusinv-header", header_sql("C900001")),
        ("core-cusinv-missing", missing_sql()),
        ("core-cusinv-delete", delete_pair_sql("C900001", "C900002")),
        ("core-cusinv-reset", reset_default_sql("C900001", "C900001")),
    ]
