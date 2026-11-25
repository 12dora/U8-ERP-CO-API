"""供应商退款 / 客户退款用例（arap_refund）的挑单与回读 SQL。

只读；表名、cFlag、cVouchType 来自常量 REFUND，id 只接受正整数（safe_id），再拼进 SQL。返回一行 ROW:a|b|…，挑不到返回空。
"""

from __future__ import annotations

from co.e2e.casespec.stock_misc_sql import safe_id

# 类型 → (cFlag, cVouchType, 往来明细表, 卡片号小写（条码前缀用）, 另一侧同号的收付款单类型)。
REFUND = {
    "ap_refund": ("AP", "48", "dbo.Ap_Detail", "ap48", "ap_payment"),
    "ar_refund": ("AR", "49", "dbo.Ar_Detail", "ar49", "ar_receipt"),
}


def _spec(kind: str) -> tuple[str, str, str, str, str]:
    if kind not in REFUND:
        raise ValueError("类型只能是 ap_refund 或 ar_refund")
    return REFUND[kind]


def _where(kind: str, doc: str) -> str:
    flag, vouch, _detail, _card, _twin = _spec(kind)
    return f"WHERE h.iID = {safe_id(doc)} AND h.cFlag = N'{flag}' AND h.cVouchType = N'{vouch}'"


def _num(expr: str) -> str:
    return f"CONVERT(varchar(40), CONVERT(decimal(18,2), ISNULL({expr}, 0)))"


def head_sql(kind: str, doc: str) -> str:
    """单号|VT_ID|条码（| 换成 /）|表头金额|审核人|摘要|行数|表体金额合计|表体 iType 非 0 或 bPrePay 非 0 的行数|卡片缺省 VT（vouchers.DEF_ID）。"""
    card = _spec(kind)[3].upper()
    body = "FROM dbo.Ap_CloseBills AS b WHERE b.iID = h.iID"
    expr = (
        "LTRIM(RTRIM(h.cVouchID)) + N'|' + ISNULL(CONVERT(varchar(12), h.VT_ID), N'') + N'|'\n"
        "  + REPLACE(LTRIM(RTRIM(ISNULL(h.csysbarcode, N''))), N'|', N'/') + N'|' + " + _num("h.iAmount") + " + N'|'\n"
        "  + LTRIM(RTRIM(ISNULL(h.cCheckMan, N''))) + N'|' + LTRIM(RTRIM(ISNULL(h.cDigest, N''))) + N'|'\n"
        f"  + (SELECT CONVERT(varchar(12), COUNT(*)) {body}) + N'|'\n"
        f"  + (SELECT {_num('SUM(b.iAmt)')} {body}) + N'|'\n"
        f"  + (SELECT CONVERT(varchar(12), COUNT(*)) {body} AND (ISNULL(b.iType, 0) <> 0 OR ISNULL(b.bPrePay, 0) <> 0))"
        f" + N'|'\n  + ISNULL((SELECT TOP (1) CONVERT(varchar(12), vc.DEF_ID) FROM dbo.Vouchers AS vc WHERE vc.CardNumber = N'{card}'), N'')"
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\nFROM dbo.Ap_CloseBill AS h\n" + _where(kind, doc) + ";\n"


def detail_sql(kind: str, doc: str) -> str:
    """本单审核写进往来明细的行：行数|借或贷为正数的行数|借贷合计。退款的往来明细借贷都是负数（U8 审核时自己写）。"""
    _flag, _vouch, detail, _card, _twin = _spec(kind)
    expr = (
        "CONVERT(varchar(12), COUNT(a.Auto_ID)) + N'|'\n"
        "  + CONVERT(varchar(12), ISNULL(SUM(CASE WHEN ISNULL(a.iDAmount, 0) > 0 OR ISNULL(a.iCAmount, 0) > 0 THEN 1 ELSE 0 END), 0))"
        " + N'|'\n  + " + _num("SUM(ISNULL(a.iDAmount, 0) + ISNULL(a.iCAmount, 0))")
    )
    frm = (
        f"FROM {detail} AS a JOIN dbo.Ap_CloseBill AS h\n"
        "  ON a.cVouchType = h.cVouchType AND a.cVouchID = h.cVouchID AND a.cFlag = h.cFlag\n" + _where(kind, doc)
    )
    return "SET NOCOUNT ON;\nSELECT N'ROW:' + " + expr + " AS v\n" + frm + ";\n"


def gone_sql(doc: str) -> str:
    """删除后表头、表体都不应再有：两表行数之和。"""
    safe = safe_id(doc)
    return (
        "SET NOCOUNT ON;\nSELECT N'ROW:' + CONVERT(varchar(12), (SELECT COUNT(*) FROM dbo.Ap_CloseBill WHERE iID = "
        f"{safe}) + (SELECT COUNT(*) FROM dbo.Ap_CloseBills WHERE iID = {safe})) AS v;\n"
    )


def pick_sql(kind: str) -> str:
    """账套里最新的一张手工（非期初）退款单：iID|单号|该类型全部单据数。没有这类单据返回空。"""
    flag, vouch, _detail, _card, _twin = _spec(kind)
    count = f"(SELECT COUNT(*) FROM dbo.Ap_CloseBill AS x WHERE x.cFlag = N'{flag}' AND x.cVouchType = N'{vouch}')"
    return (
        "SET NOCOUNT ON;\nSELECT TOP (1) N'ROW:' + CONVERT(varchar(20), h.iID) + N'|' + LTRIM(RTRIM(h.cVouchID)) + N'|'\n"
        f"  + CONVERT(varchar(12), {count}) AS v\nFROM dbo.Ap_CloseBill AS h\n"
        f"WHERE h.cFlag = N'{flag}' AND h.cVouchType = N'{vouch}' AND ISNULL(h.bStartFlag, 0) = 0\n"
        "ORDER BY h.iID DESC;\n"
    )


def arap_refund_samples() -> list[tuple[str, str]]:
    rows = []
    for kind, (flag, vouch, _detail, _card, _twin) in REFUND.items():
        tag = "rf-" + flag.lower() + vouch
        rows += [(tag + "-head", head_sql(kind, "100")), (tag + "-detail", detail_sql(kind, "100")),
                 (tag + "-pick", pick_sql(kind))]
    return rows + [("rf-gone", gone_sql("100"))]
