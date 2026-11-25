"""写预演用例的 SQL：预演前后按单据类型数表头（行数和最大主键）、停用客户、按摘要数总账凭证、应付自动核销的挑单。

标识符都是这里的常量（与桥 Kinds.cs 的表名、主键一致）；拼进 SQL 的值先过白名单。不引用 steps。
DRY_AP_PACKS 进挑单（接线在 wiring 汇总模块）。
"""

from __future__ import annotations

import re

_YEAR = re.compile(r"^20[0-9]{2}\Z")
_DAY = re.compile(r"^20[0-9]{2}-[0-9]{2}-[0-9]{2}\Z")
_ID = re.compile(r"^(0|[1-9][0-9]{0,18})\Z")
DIGEST = re.compile(r"^CO预演[0-9]{14}[0-9a-f]{4}\Z")

# 单据类型 → (表头表, 主键, 同表别的类型要排除时的条件)。同表的两种类型多数用条件分开，免得并行时别的用例新建另一种而数错；
# 发货单 / 退货单（DispatchList）、到货单 / 采购退货单（PU_ArrivalVouch）没有条件，靠 tags_data.CROSS_NEW 让它们的新建互斥。
HEADS = {
    "sale_order": ("SO_SOMain", "ID", ""),
    "dispatch": ("DispatchList", "DLID", ""),
    "sale_return": ("DispatchList", "DLID", ""),
    "sale_invoice": ("SaleBillVouch", "SBVID", ""),
    "purchase_order": ("PO_Pomain", "POID", ""),
    "arrival": ("PU_ArrivalVouch", "ID", ""),
    "purchase_return": ("PU_ArrivalVouch", "ID", ""),
    "purchase_invoice": ("PurBillVouch", "PBVID", ""),
    "purchase_requisition": ("PU_AppVouch", "ID", ""),
    "purchase_in": ("RdRecord01", "ID", ""),
    "other_in": ("RdRecord08", "ID", ""),
    "other_out": ("RdRecord09", "ID", ""),
    "product_in": ("rdrecord10", "ID", ""),
    "material_out": ("rdrecord11", "ID", ""),
    "sale_out": ("rdrecord32", "ID", ""),
    "transfer": ("TransVouch", "ID", ""),
    "shape_change": ("AssemVouch", "ID", "cVouchType = N'15'"),
    "transfer_request": ("ST_AppTransVouch", "ID", ""),
    "stock_check": ("CheckVouch", "ID", ""),
    "production_order": ("mom_order", "MoId", ""),
    "bom": ("bom_bom", "BomId", ""),
    "ar_receipt": ("Ap_CloseBill", "iID", "cFlag = N'AR' AND cVouchType = N'48'"),
    "ap_payment": ("Ap_CloseBill", "iID", "cFlag = N'AP' AND cVouchType = N'49'"),
    "ar_bill": ("Ap_Vouch", "Auto_ID", "cFlag = N'AR'"),
    "ap_bill": ("Ap_Vouch", "Auto_ID", "cFlag = N'AP'"),
    "qm_incoming_inspect": ("QMINSPECTVOUCHER", "ID", "CVOUCHTYPE = N'QM01'"),
    "qm_product_inspect": ("QMINSPECTVOUCHER", "ID", "CVOUCHTYPE = N'QM02'"),
    "qm_incoming_check": ("QMCHECKVOUCHER", "ID", "CVOUCHTYPE = N'QM03'"),
    "qm_product_check": ("QMCHECKVOUCHER", "ID", "CVOUCHTYPE = N'QM04'"),
}


def count_sql(kind: str) -> str:
    """该类型表头的「行数|最大主键」。预演前后各查一次，必须相同。"""
    if kind not in HEADS:
        raise ValueError("没有登记表头的单据类型：" + kind)
    table, key, cond = HEADS[kind]
    where = f"\nWHERE {cond}" if cond else ""
    return (
        "SET NOCOUNT ON;\n"
        f"SELECT N'ROW:' + CONVERT(varchar(20), COUNT_BIG(*)) + N'|' + CONVERT(varchar(20), ISNULL(MAX(h.{key}), 0)) AS v\n"
        f"FROM dbo.{table} AS h{where};\n"
    )


def ids_after_sql(kind: str, max_id: str) -> str:
    """该类型表头主键大于 max_id 的行（至多 20 个，从大到小）：预演之前记下的最大主键之后多出来的单据。"""
    if kind not in HEADS or _ID.fullmatch(max_id or "") is None:
        raise ValueError("类型或主键不合法")
    table, key, cond = HEADS[kind]
    extra = f" AND {cond}" if cond else ""
    return (
        "SET NOCOUNT ON;\n"
        f"SELECT TOP (20) N'ROW:' + CONVERT(varchar(20), h.{key}) AS v\nFROM dbo.{table} AS h\n"
        f"WHERE h.{key} > {max_id}{extra}\nORDER BY h.{key} DESC;\n"
    )


def disabled_customer_sql(day: str) -> str:
    """登录日期已停用（dEndDate <= 登录日期）的一个客户编码；编码只取 bag 字符集里的。"""
    if _DAY.fullmatch(day or "") is None:
        raise ValueError("日期不合法")
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (1) N'ROW:' + c.cCusCode AS v\nFROM dbo.Customer AS c\n"
        f"WHERE c.dEndDate IS NOT NULL AND c.dEndDate <= '{day}'\n"
        "  AND LEN(c.cCusCode) <= 20 AND c.cCusCode NOT LIKE N'%[^0-9A-Za-z._-]%'\n"
        "ORDER BY c.cCusCode;\n"
    )


def enabled_customers_sql(day: str) -> str:
    """登录日期还在用（没有停用日期或停用日期在登录日期之后）的客户编码，至多 200 个，按编码排序。"""
    if _DAY.fullmatch(day or "") is None:
        raise ValueError("日期不合法")
    return (
        "SET NOCOUNT ON;\n"
        "SELECT TOP (200) N'ROW:' + c.cCusCode AS v\nFROM dbo.Customer AS c\n"
        f"WHERE (c.dEndDate IS NULL OR c.dEndDate > '{day}')\n"
        "  AND LEN(c.cCusCode) <= 20 AND c.cCusCode NOT LIKE N'%[^0-9A-Za-z._-]%'\n"
        "ORDER BY c.cCusCode;\n"
    )


def digest_count_sql(year: str, digest: str) -> str:
    """该年度按摘要数总账凭证行（预演新建之后必须是 0）。"""
    if _YEAR.fullmatch(year or "") is None or DIGEST.fullmatch(digest or "") is None:
        raise ValueError("年度或摘要不合法")
    return (
        "SET NOCOUNT ON;\n"
        "SELECT N'ROW:' + CONVERT(varchar(12), COUNT(*)) AS v\n"
        f"FROM dbo.GL_accvouch AS v\nWHERE v.iyear = {year} AND v.cdigest = N'{digest}';\n"
    )


# 应付自动核销：已审核、本币、有未核销余额（≥ 2）的付款单行，和同一供应商下已应付审核、本币的采购发票里行号最小的有余额行
# （余额 ≥ 2）。同 writeoff_auto_sql 的应收挑单，只换成应付一侧的表和列。
_PICK_AP = """SET NOCOUNT ON;
WITH o AS (
  SELECT d.cCoVouchType AS t, d.cCoVouchID AS c, d.cDwCode AS dw, d.iBVid AS line,
    SUM(ISNULL(d.iCAmount_f, 0) - ISNULL(d.iDAmount_f, 0)) AS v
  FROM dbo.Ap_Detail AS d
  WHERE d.cFlag = N'AP' AND d.cCoVouchType IN (N'01', N'02') AND d.iFlag < 3 AND d.iBVid IS NOT NULL
  GROUP BY d.cCoVouchType, d.cCoVouchID, d.cDwCode, d.iBVid
  HAVING SUM(ISNULL(d.iCAmount_f, 0) - ISNULL(d.iDAmount_f, 0)) > 0
)
SELECT TOP (1) N'ROW:' + h.cDwCode + N'|' + CONVERT(varchar(20), h.iID) + N'|' + CONVERT(varchar(20), b.ID) + N'|'
  + CONVERT(varchar(20), s.PBVID) + N'|' + CONVERT(varchar(20), o.line) AS v
FROM o
INNER JOIN dbo.PurBillVouch AS s ON s.cPBVCode = o.c AND s.cPBVBillType = o.t
INNER JOIN dbo.PurBillVouchs AS sb ON sb.ID = o.line AND sb.PBVID = s.PBVID
INNER JOIN dbo.Ap_CloseBill AS h ON h.cDwCode = o.dw AND h.cFlag = N'AP' AND h.cVouchType = N'49'
INNER JOIN dbo.Ap_CloseBills AS b ON b.iID = h.iID
WHERE o.v >= 2 AND b.iRAmt_f >= 2 AND ISNULL(b.bPrePay, 0) = 0
  AND NOT EXISTS (SELECT 1 FROM o AS p WHERE p.t = o.t AND p.c = o.c AND p.dw = o.dw AND p.line < o.line)
  AND LEN(h.cDwCode) <= 12 AND h.cDwCode NOT LIKE N'%[^0-9A-Za-z._-]%'
  AND LTRIM(RTRIM(ISNULL(h.cCheckMan, N''))) <> N'' AND LTRIM(RTRIM(ISNULL(s.cPBVVerifier, N''))) <> N''
  AND ISNULL(h.IsWfControlled, 0) = 0 AND ISNULL(s.IsWfControlled, 0) = 0
  AND h.cexch_name = (SELECT TOP (1) f.cexch_name FROM dbo.foreigncurrency AS f WHERE f.iotherused = -1)
  AND s.cexch_name = h.cexch_name AND ISNULL(h.iExchRate, 0) = 1 AND ISNULL(s.cExchRate, 0) = 1
  AND h.dVouchDate <= CONVERT(date, GETDATE()) AND s.dPBVDate <= CONVERT(date, GETDATE())
  AND NOT EXISTS (SELECT 1 FROM dbo.GL_mend AS m WHERE m.iyear = YEAR(GETDATE()) AND m.iperiod = MONTH(GETDATE())
    AND ISNULL(m.bflag_AP, 0) <> 0)
ORDER BY h.iID DESC, b.ID;
"""


def dry_ap_ap_sql() -> str:
    return _PICK_AP


# 挑单登记（同 returns_misc_sql.CUSTOM_PACKS 的形状）：前导键、按 | 拆开的键、是否必须是正整数 id、SQL。供应商编码不是 id。
AP_AUTO_KEYS = ("dry_ap_dw", "dry_ap_pc", "dry_ap_pcl", "dry_ap_pinv", "dry_ap_pinvl")
DRY_AP_PACKS = (("dry_ap_dw", AP_AUTO_KEYS, (False, True, True, True, True), dry_ap_ap_sql),)


def dry_suite_samples() -> list[tuple[str, str]]:
    rows = [("dry-" + lead.replace("_", "-"), sql()) for lead, _names, _flags, sql in DRY_AP_PACKS]
    rows.append(("dry-count-sale-order", count_sql("sale_order")))
    rows.append(("dry-disabled-customer", disabled_customer_sql("2026-01-31")))
    rows.append(("dry-enabled-customers", enabled_customers_sql("2026-01-31")))
    rows.append(("dry-ids-after", ids_after_sql("other_in", "100")))
    return rows
