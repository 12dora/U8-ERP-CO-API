"""客户、供应商子档案用例（arc_partner）的挑选 SQL。表名、列名只来自下面的常量；挑出来的编码只收字母数字
（SQL 里过滤，Python 再用 safe_part 核一遍）。

每条 SQL 输出一行 ROW:<值>[|<值>]，挑不到就没有 ROW 行（用例记 N/A）。SQL 已实测。
"""

from __future__ import annotations

# (上级表, 编码列, 银行账户表, 联系人表, 上级档案上的银行账号列)
_SIDES = {
    "customer": ("Customer", "cCusCode", "CustomerBank", "Crm_Contact", "cCusAccount"),
    "vendor": ("Vendor", "cVenCode", "VendorBank", "Ven_Contact", "cVenAccount"),
}
_ALNUM = "NOT LIKE N'%[^0-9A-Za-z]%'"


def bank_partner(side: str) -> str:
    # 没停用、还没有任何银行账户的客户（供应商）：用例建的第一个账户就是默认账户，不碰已有账户。
    # 优先挑档案上银行账号也为空的（默认账户同步不覆盖真实数据；样本账套里没有这样的客户，退而求其次）。
    table, col, bank, _contact, account = _SIDES[side]
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + p.{col} AS v
FROM dbo.{table} AS p
WHERE p.{col} {_ALNUM} AND LEN(p.{col}) <= 20 AND p.dEndDate IS NULL
  AND NOT EXISTS (SELECT 1 FROM dbo.{bank} AS b WHERE b.{col} = p.{col})
ORDER BY CASE WHEN ISNULL(p.{account}, N'') = N'' THEN 0 ELSE 1 END, p.{col};
"""


def contact_customer(side: str = "customer") -> str:
    # 没停用的客户（也给供应商用）：联系人编码由 U8 自动编号，不用挑编码。
    table, col, _bank, _contact, _account = _SIDES[side]
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + p.{col} AS v
FROM dbo.{table} AS p
WHERE p.{col} {_ALNUM} AND LEN(p.{col}) <= 20 AND p.dEndDate IS NULL
ORDER BY p.{col};
"""


def vendor_contact_any() -> str:
    # 任一供应商联系人的 <供应商编码>:<联系人编码>（读取用例；测试账套可能没有，挑不到时只测列表）。
    return f"""SET NOCOUNT ON;
SELECT TOP (1) N'ROW:' + c.cVenCode + N'|' + c.cContactCode AS v
FROM dbo.Ven_Contact AS c
WHERE c.cVenCode {_ALNUM} AND c.cContactCode {_ALNUM}
ORDER BY c.cVenCode, c.cContactCode;
"""
