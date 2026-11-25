"""各类单据（原 22 种加两种报检单）各取最新主键，没有行就留空。"""

from __future__ import annotations

# key, table, id column, extra where
LOAD_SOURCES = (
    ("load_sale_order", "dbo.SO_SOMain", "ID", ""),
    ("load_dispatch", "dbo.DispatchList", "DLID", ""),
    ("load_purchase_order", "dbo.PO_Pomain", "POID", ""),
    ("load_arrival", "dbo.PU_ArrivalVouch", "ID", ""),
    ("load_purchase_in", "dbo.RdRecord01", "ID", ""),
    ("load_other_in", "dbo.RdRecord08", "ID", ""),
    ("load_other_out", "dbo.RdRecord09", "ID", ""),
    ("load_product_in", "dbo.RdRecord10", "ID", ""),
    ("load_material_out", "dbo.RdRecord11", "ID", ""),
    ("load_sale_out", "dbo.RdRecord32", "ID", ""),
    ("load_qm_incoming_inspect", "dbo.QMINSPECTVOUCHER", "ID", "CVOUCHTYPE = N'QM01'"),
    ("load_qm_product_inspect", "dbo.QMINSPECTVOUCHER", "ID", "CVOUCHTYPE = N'QM02'"),
    ("load_qm_incoming_check", "dbo.QMCHECKVOUCHER", "ID", "CVOUCHTYPE = N'QM03'"),
    ("load_qm_product_check", "dbo.QMCHECKVOUCHER", "ID", "CVOUCHTYPE = N'QM04'"),
    ("load_qm_incoming_reject", "dbo.QMREJECTVOUCHER", "ID", "CVOUCHTYPE = N'QM05'"),
    ("load_qm_product_reject", "dbo.QMREJECTVOUCHER", "ID", "CVOUCHTYPE = N'QM06'"),
    # 其他报检单、其他检验单（只有有这类单据的账套才有，没有时 N/A）。
    ("load_qm_other_inspect", "dbo.QMINSPECTVOUCHER", "ID", "CVOUCHTYPE = N'QM11'"),
    ("load_qm_other_check", "dbo.QMCHECKVOUCHER", "ID", "CVOUCHTYPE = N'QM15'"),
    ("load_transfer", "dbo.TransVouch", "ID", ""),
    ("load_sale_invoice", "dbo.SaleBillVouch", "SBVID", ""),
    ("load_purchase_invoice", "dbo.PurBillVouch", "PBVID", ""),
    ("load_production_order", "dbo.mom_order", "MoId", ""),
    ("load_ar_receipt", "dbo.Ap_CloseBill", "iID", "cFlag = N'AR' AND cVouchType = N'48'"),
    ("load_ap_payment", "dbo.Ap_CloseBill", "iID", "cFlag = N'AP' AND cVouchType = N'49'"),
    ("load_ar_bill", "dbo.Ap_Vouch", "Auto_ID", "cFlag = N'AR' AND cVouchType = N'R0'"),
    ("load_ap_bill", "dbo.Ap_Vouch", "Auto_ID", "cFlag = N'AP' AND cVouchType = N'P0'"),
)


def newest_sql(table: str, col: str, where: str) -> str:
    filt = where or "1 = 1"
    return (
        "SET NOCOUNT ON;\n"
        f"SELECT TOP (1) N'ROW:' + CONVERT(varchar(20), {col}) AS v\n"
        f"FROM {table}\n"
        f"WHERE {filt}\n"
        f"ORDER BY {col} DESC;\n"
    )
