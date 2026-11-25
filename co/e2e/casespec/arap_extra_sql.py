"""应收 / 应付待实测用例（arap_extra）的回读 SQL。只读；标识符是按侧登记的常量，
拼进去的只有校验过范围的正整数主键、核销号 / 外部业务号的数字和登记过的 coutsign。

挑单、余额、核销行沿用 writeoff_prepay_fx_sql（prepay_pick_sql、balance_sql、batch_sql）。
arap_extra_samples() 进样例 SQL（samples.sample_sql），供离线测试过 SQL 守卫。
"""

from __future__ import annotations

import re

_INT_MAX = 2147483647
_DIGITS = re.compile(r"^[0-9]{1,20}\Z")
_SIDES = ("AR", "AP")
# coutsign 只会是这几种（ArapVoucherRule.OutSign）。
_SIGNS = ("AR", "AP", "RP")
_MONEY = "CONVERT(varchar(40), CONVERT(decimal(28, 2), ISNULL({expr}, 0)))"


def _id(value: int) -> int:
    if type(value) is not int or not 1 <= value <= _INT_MAX:
        raise ValueError("主键不对")
    return value


def _money(expr: str) -> str:
    return _MONEY.format(expr=expr)


def head_sql(receipt: int) -> str:
    """一行：<往来单位编码>|<表头 iRAmount_f>|<表头 iRAmount>|<表体 iRAmt_f 合计>|<表体 iRAmt 合计>|<触发器状态>。

    收付款单 Ap_CloseBill.iID = receipt。触发器状态是 TR_Ap_CloseBills 的 sys.triggers.is_disabled（0 启用、1 停用），
    没有这个触发器为 -。
    """
    rid = _id(receipt)
    lines = "(SELECT SUM(b.{col}) FROM dbo.Ap_CloseBills AS b WHERE b.iID = h.iID)"
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + ISNULL(LTRIM(RTRIM(h.cDwCode)), N'')"
        f" + N'|' + {_money('h.iRAmount_f')} + N'|' + {_money('h.iRAmount')}"
        f" + N'|' + {_money(lines.format(col='iRAmt_f'))} + N'|' + {_money(lines.format(col='iRAmt'))}"
        " + N'|' + ISNULL((SELECT TOP (1) CONVERT(varchar(1), CONVERT(int, t.is_disabled)) FROM sys.triggers AS t"
        " WHERE t.name = N'TR_Ap_CloseBills'), N'-') AS v\n"
        f"FROM dbo.Ap_CloseBill AS h WHERE h.iID = {rid};\n"
    )


def so_sql(invoice_line: int) -> str:
    """一行：<销售订单行 iSOsID，没有为 0>|<订单行累计收款原币 iExchSum>|<本币 iMoneySum>。

    销售发票行 SaleBillVouchs.AutoID = invoice_line；订单行取发票行自己的 iSOsID，没有时取来源发货单行的 iSOsID。
    """
    line = _id(invoice_line)
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(20), ISNULL(s.iSOsID, 0))"
        f" + N'|' + {_money('s.iExchSum')} + N'|' + {_money('s.iMoneySum')} AS v\n"
        "FROM dbo.SaleBillVouchs AS b\n"
        "LEFT JOIN dbo.DispatchLists AS l ON l.iDLsID = b.iDLsID\n"
        "LEFT JOIN dbo.SO_SODetails AS s ON s.iSOsID = COALESCE(NULLIF(b.iSOsID, 0), l.iSOsID)\n"
        f"WHERE b.AutoID = {line};\n"
    )


def pz_src_sql(side: str, digits: str, sign: str) -> str:
    """一行：<凭证行数>|<coutsysname 不是 side 的行数>|<coutsign 不是 sign 的行数>|<受控行（bvalueedit=0）数>
    |<AccInformation bUseYSKM>|<bUseYFKM>（没有该选项为 -）。

    凭证按外部业务号 coutno_id = side + digits 找（不论年度，外部业务号不重复）。
    """
    if side not in _SIDES or sign not in _SIGNS:
        raise ValueError("side / sign 不对")
    if _DIGITS.fullmatch(digits or "") is None:
        raise ValueError("外部业务号数字不对")
    option = (
        "ISNULL((SELECT MAX(LTRIM(RTRIM(CONVERT(nvarchar(40), a.cValue)))) FROM dbo.AccInformation AS a"
        " WHERE a.cName = N'{name}'), N'-')"
    )
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*))"
        f" + N'|' + CONVERT(varchar(12), ISNULL(SUM(CASE WHEN ISNULL(g.coutsysname, N'') = N'{side}' THEN 0 ELSE 1 END), 0))"
        f" + N'|' + CONVERT(varchar(12), ISNULL(SUM(CASE WHEN ISNULL(g.coutsign, N'') = N'{sign}' THEN 0 ELSE 1 END), 0))"
        " + N'|' + CONVERT(varchar(12), ISNULL(SUM(CASE WHEN ISNULL(g.bvalueedit, 1) = 0 THEN 1 ELSE 0 END), 0))"
        f" + N'|' + {option.format(name='bUseYSKM')} + N'|' + {option.format(name='bUseYFKM')} AS v\n"
        f"FROM dbo.GL_accvouch AS g WHERE g.coutno_id = N'{side}{digits}';\n"
    )


def arap_extra_samples() -> list[tuple[str, str]]:
    return [
        ("closebill-head", head_sql(100)),
        ("so-received", so_sql(200)),
        ("pz-src-ar", pz_src_sql("AR", "0000000000001", "AR")),
        ("pz-src-ap", pz_src_sql("AP", "0000000000001", "AR")),
    ]
