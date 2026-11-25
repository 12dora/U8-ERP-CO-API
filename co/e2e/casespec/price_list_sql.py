"""价格表用例（price_list）的 SQL：挑客户、存货、供应商，往三张价格表各插几行带标记的价格，回读主键，再按标记删掉。

价格表没有可调用的接口（客户调价单、存货调价单、供应商存货调价单都未接入），只能用 SQL 造行。三张表的主键列
（SA_CusUPrice / SA_InvUPrice 的 AutoID、Ven_Inv_Price 的 Autoid）是不是自增按 COLUMNPROPERTY 判断：自增时不写主键，
否则取当前最大值往上编。行先写进临时表 #p 再整批插，一个事务、XACT_ABORT；出错时回滚并回一行 ERR:<原文>，不留半批。
备注列 cMemo 写 MARK，删除只按这个标记删（不碰别的行）。拼进去的编码都先过 _code 校验，日期按 CONVERT 样式 23 写。
price_list_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re
from datetime import date

MARK = "e2e:price"
_CODE = re.compile(r"^[0-9A-Za-z._-]{1,20}\Z")
_SAFE = "NOT LIKE N'%[^0-9A-Za-z._-]%'"
_HOME = "(SELECT TOP (1) f.cexch_name FROM dbo.foreigncurrency AS f WHERE f.iotherused = -1)"
TABLES = (("customer", "SA_CusUPrice", "AutoID"), ("inventory", "SA_InvUPrice", "AutoID"), ("vendor", "Ven_Inv_Price", "Autoid"))

# 各表插入的列（顺序同临时表 #p 的列）。
CUS_COLS = (
    "cCusCode, cCusCCode, cInvCode, dStartDate, dEndDate, binvalid, cexch_name, fminquantity, bsaleprice,"
    " iInvSCost, iCusDisRate, iInvNowCost, fcusminprice, bsales, cMemo"
)
INV_COLS = (
    "cInvCode, dStartDate, dEndDate, binvalid, cexch_name, fminquantity, bsaleprice, iUPrice1, ISalePrice1,"
    " iUPrice3, bsales, cMemo"
)
VEN_COLS = (
    "cVenCode, cInvCode, dEnableDate, dDisableDate, cExch_Name, iLowerLimit, iUpperLimit, btaxcost, iUnitPrice,"
    " iTaxUnitPrice, iTaxRate, bPromotion, iSupplyType, cMemo"
)


def _code(text: str) -> str:
    if type(text) is not str or _CODE.fullmatch(text) is None:
        raise ValueError("编码不能拼进 SQL")
    return "N'" + text + "'"


def _day(text: str) -> str:
    # 用 CONVERT 样式 23 明确 yyyy-mm-dd，不受会话 DATEFORMAT 影响；VALUES 里的列类型也就是 date。
    date.fromisoformat(text)
    return "CONVERT(date, N'" + text + "', 23)"


def pick_sql() -> str:
    """一行：<客户>|<客户分类>|<存货>|<供应商>|<本位币>。编码都只含字母数字和 ._-；挑不全时没有行。"""
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1) N'ROW:' + c.code + N'|' + c.cls + N'|' + i.code + N'|' + v.code + N'|' + " + _HOME + " AS v\n"
        "FROM (SELECT TOP (1) LTRIM(RTRIM(x.cCusCode)) AS code, ISNULL(LTRIM(RTRIM(x.cCCCode)), N'') AS cls FROM dbo.Customer AS x\n"
        "  WHERE x.dEndDate IS NULL AND LTRIM(RTRIM(x.cCusCode)) " + _SAFE + " AND LEN(LTRIM(RTRIM(x.cCusCode))) <= 20\n"
        "    AND ISNULL(LTRIM(RTRIM(x.cCCCode)), N'') " + _SAFE + " AND LEN(ISNULL(x.cCCCode, N'')) <= 20\n"
        "  ORDER BY x.cCusCode) AS c\n"
        "CROSS JOIN (SELECT TOP (1) LTRIM(RTRIM(n.cInvCode)) AS code FROM dbo.Inventory AS n\n"
        "  WHERE n.dEDate IS NULL AND ISNULL(n.bSale, 0) = 1 AND ISNULL(n.bPurchase, 0) = 1\n"
        "    AND LTRIM(RTRIM(n.cInvCode)) " + _SAFE + " AND LEN(LTRIM(RTRIM(n.cInvCode))) <= 20\n"
        "  ORDER BY n.cInvCode) AS i\n"
        "CROSS JOIN (SELECT TOP (1) LTRIM(RTRIM(w.cVenCode)) AS code FROM dbo.Vendor AS w\n"
        "  WHERE w.dEndDate IS NULL AND LTRIM(RTRIM(w.cVenCode)) " + _SAFE + " AND LEN(LTRIM(RTRIM(w.cVenCode))) <= 20\n"
        "  ORDER BY w.cVenCode) AS v\n"
        "WHERE " + _HOME + " IS NOT NULL;\n"
    )


def _insert(table: str, key: str, cols: str) -> str:
    # 主键自增时不写主键；否则从当前最大值往上编（#p.n 从 1 起）。
    return (
        f"  IF COLUMNPROPERTY(OBJECT_ID(N'dbo.{table}'), N'{key}', 'IsIdentity') = 1\n"
        f"    INSERT INTO dbo.{table} ({cols}) SELECT {cols} FROM #p ORDER BY n;\n"
        "  ELSE\n"
        f"    INSERT INTO dbo.{table} ({key}, {cols})\n"
        f"    SELECT (SELECT ISNULL(MAX(t.{key}), 0) FROM dbo.{table} AS t WITH (UPDLOCK, HOLDLOCK)) + n, {cols} FROM #p ORDER BY n;\n"
    )


def _batch(fill: str, table: str, key: str, cols: str) -> str:
    return (
        "SET NOCOUNT ON;\nSET XACT_ABORT ON;\n"
        f"DECLARE @home nvarchar(8) = {_HOME};\n"
        "BEGIN TRY\n  BEGIN TRANSACTION;\n"
        + fill
        + _insert(table, key, cols)
        + "  COMMIT TRANSACTION;\n"
        f"  SELECT N'ROW:OK|' + CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.{table} WHERE cMemo = N'{MARK}')) AS v;\n"
        "END TRY\nBEGIN CATCH\n  IF XACT_STATE() <> 0 ROLLBACK TRANSACTION;\n"
        "  SELECT N'ROW:ERR|' + LEFT(REPLACE(ERROR_MESSAGE(), N'|', N'/'), 300) AS v;\nEND CATCH;\n"
    )


def customer_insert_sql(cus: str, cls: str, inv: str, today: str) -> str:
    """客户价格三行：本客户有效（价格 114）、客户分类有效（110，cCusCode 为空；分类为空时不插）、本客户已失效（99）。"""
    start, old, end = _day(_shift(today, -10)), _day(_shift(today, -30)), _day(_shift(today, -1))
    rows = [
        f"(1, {_code(cus)}, NULL, {_code(inv)}, {start}, NULL, 0, @home, 2, 1, 120, 95, 114, 100, 0, N'{MARK}')",
        f"(3, {_code(cus)}, NULL, {_code(inv)}, {old}, {end}, 0, @home, 1, 0, 100, 100, 99, NULL, 0, N'{MARK}')",
    ]
    if cls:
        rows.insert(1, f"(2, NULL, {_code(cls)}, {_code(inv)}, {start}, NULL, 0, @home, 1, 0, 115, 96, 110, NULL, 1, N'{MARK}')")
    fill = (
        "  SELECT * INTO #p FROM (VALUES\n    " + ",\n    ".join(rows) + "\n  ) AS r (n, " + CUS_COLS + ");\n"
    )
    return _batch(fill, "SA_CusUPrice", "AutoID", CUS_COLS)


def inventory_insert_sql(inv: str, today: str) -> str:
    """存货价格两行：有效（1 级 10 / 11.3，3 级只有无税 9）、已失效。"""
    start, end = _day(_shift(today, -10)), _day(_shift(today, -1))
    rows = [
        f"(1, {_code(inv)}, {start}, NULL, 0, @home, 1, 0, 10, 11.3, 9, 0, N'{MARK}')",
        f"(2, {_code(inv)}, {start}, {end}, 0, @home, 1, 0, 8, NULL, NULL, 0, N'{MARK}')",
    ]
    fill = "  SELECT * INTO #p FROM (VALUES\n    " + ",\n    ".join(rows) + "\n  ) AS r (n, " + INV_COLS + ");\n"
    return _batch(fill, "SA_InvUPrice", "AutoID", INV_COLS)


def vendor_insert_sql(ven: str, inv: str, today: str) -> str:
    """供应商存货价格两行：有效（无税 50、含税 56.5、税率 13、数量 1 到 100）、已失效。"""
    start, end = _day(_shift(today, -10)), _day(_shift(today, -1))
    rows = [
        f"(1, {_code(ven)}, {_code(inv)}, {start}, NULL, @home, 1, 100, 1, 50, 56.5, 13, 0, 0, N'{MARK}')",
        f"(2, {_code(ven)}, {_code(inv)}, {start}, {end}, @home, 1, NULL, 0, 40, 45.2, 13, 0, 0, N'{MARK}')",
    ]
    fill = "  SELECT * INTO #p FROM (VALUES\n    " + ",\n    ".join(rows) + "\n  ) AS r (n, " + VEN_COLS + ");\n"
    return _batch(fill, "Ven_Inv_Price", "Autoid", VEN_COLS)


def _shift(today: str, days: int) -> str:
    return date.fromordinal(date.fromisoformat(today).toordinal() + days).isoformat()


def ids_sql() -> str:
    """本用例的价格行，一行一个：<kind>|<主键>|<价格>（客户取 iInvNowCost，存货取 iUPrice1，供应商取 iUnitPrice）。"""
    parts = (
        ("customer", "SA_CusUPrice", "AutoID", "iInvNowCost"),
        ("inventory", "SA_InvUPrice", "AutoID", "iUPrice1"),
        ("vendor", "Ven_Inv_Price", "Autoid", "iUnitPrice"),
    )
    selects = [
        f"SELECT N'ROW:{kind}|' + CONVERT(varchar(20), x.{key}) + N'|' + ISNULL(CONVERT(varchar(40), CONVERT(decimal(28, 6), x.{price})), N'') AS v"
        f" FROM dbo.{table} AS x WHERE x.cMemo = N'{MARK}'"
        for kind, table, key, price in parts
    ]
    return "SET NOCOUNT ON;\n" + "\nUNION ALL\n".join(selects) + ";\n"


def delete_sql() -> str:
    """按标记删掉三张表里本用例的行；一行：<删的总行数>|<删后剩下的标记行数>。"""
    deletes = "".join(f"DELETE FROM dbo.{table} WHERE cMemo = N'{MARK}';\nSET @n = @n + @@ROWCOUNT;\n" for _k, table, _key in TABLES)
    left = " + ".join(f"(SELECT COUNT(*) FROM dbo.{table} WHERE cMemo = N'{MARK}')" for _k, table, _key in TABLES)
    return (
        "SET NOCOUNT ON;\nDECLARE @n int = 0;\n" + deletes
        + f"SELECT N'ROW:' + CONVERT(varchar(12), @n) + N'|' + CONVERT(varchar(12), {left}) AS v;\n"
    )


def price_list_samples() -> list[tuple[str, str]]:
    return [
        ("price-pick", pick_sql()),
        ("price-cus", customer_insert_sql("C0001", "01", "I0001", "2026-10-02")),
        ("price-inv", inventory_insert_sql("I0001", "2026-10-02")),
        ("price-ven", vendor_insert_sql("S0001", "I0001", "2026-10-02")),
        ("price-ids", ids_sql()),
        ("price-delete", delete_sql()),
    ]
