"""`archive-customer-address-rows`（客户收货地址新增 / 删除）要补的 SQL。

其余 SQL（挑客户、整户地址行快照、被引用地址、兜底删除与写回）复用 arc_cus_sql。不出现 sqlguard 拒绝的词。
"""

from __future__ import annotations

from co.e2e.casespec.arc_cus_sql import addr_customer_sql

_ALNUM = "NOT LIKE N'%[^0-9A-Za-z]%'"


def addr_used_free_sql() -> str:
    """被发货单引用的收货地址，优先挑不是默认地址的：输出 客户编码<TAB>地址编码。

    桥先查默认地址、后查引用：既是默认又被引用的地址报 address_is_default，不报 address_in_use；
    所以挑非默认的，拒绝原因只可能是被引用。
    """
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + LTRIM(RTRIM(a.cCusCode)) + NCHAR(9) + a.cAddCode AS v
FROM dbo.CusDeliverAdd AS a
WHERE LTRIM(RTRIM(a.cCusCode)) {_ALNUM}
  AND LEN(a.cAddCode) BETWEEN 1 AND 30 AND a.cAddCode = LTRIM(RTRIM(a.cAddCode))
  AND EXISTS (SELECT 1 FROM dbo.DispatchList AS d WHERE d.cCusCode = a.cCusCode AND d.cAddCode = a.cAddCode)
ORDER BY a.bDefault, a.id;
"""


def cusrows_samples() -> list[tuple[str, str]]:
    return [
        ("core-cusrows-customer", addr_customer_sql()),
        ("core-cusrows-used", addr_used_free_sql()),
    ]
