namespace U8Co
{
    // 采购结算单（PuSettleRead）的列表行。列顺序同 ListKinds.cs 的说明。结算单没有审核，verified 恒为 0；
    // 表头、表体的 rowversion 是 psufts / psdufts，增量按两者较大的算。APPLY 覆盖缺省的表体 rowversion 汇总，另给行数、
    // 已记账（存货核算已处理结算成本）行数、涉及的发票张数、入库单张数和结算数量、金额合计。
    internal static partial class ListKinds
    {
        const string PsApply = " OUTER APPLY (SELECT MAX(d.psdufts) AS bu, COUNT(*) AS n, "
            + "SUM(CASE WHEN d.bAccount = 1 THEN 1 ELSE 0 END) AS acc, "
            + "COUNT(DISTINCT CASE WHEN d.iBsID <> 0 THEN d.iBsID END) AS bills, "
            + "COUNT(DISTINCT CASE WHEN ISNULL(d.cUpSoType, N'01') = N'01' THEN d.cPIVCode END) AS ins, "
            + "MIN(NULLIF(d.cBillCode, N'')) AS bill_code, MIN(NULLIF(d.cPIVCode, N'')) AS in_code, "
            + "SUM(d.iSVQuantity) AS qty, SUM(d.iSVPrice) AS amount "
            + "FROM PurSettleVouchs d WHERE d.PSVID = h.PSVID) b";
        const string PsExtra = "h.cSettleType AS settle_type, h.cBusType AS bus_type, h.cPTCode AS pt_code, "
            + "h.bFirst AS opening, h.cSVMemo AS memo, b.n AS line_count, b.acc AS accounted_lines, "
            + "b.bills AS invoice_lines, b.ins AS receipt_count, b.bill_code AS first_invoice_code, "
            + "b.in_code AS first_in_code, b.qty AS quantity, b.amount AS amount";

        static string[][] PuSettleRows()
        {
            return new string[][]
            {
                new string[] { PuSettleRead.KindName, "PurSettleVouch", "PSVID", "h.cSVCode", "h.dSVDate",
                    null, "h.cVenCode", null, "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    null, null, null, null, "0", null,
                    "psufts", "PurSettleVouchs", "PSVID", "psdufts", null, PsExtra, PsApply }
            };
        }
    }
}
