namespace U8Co
{
    // 出入库调整单（ia_adjust，IaAdjustRead）与存货调价单（inventory_price_adjust，InvPriceAdjustRead）的列表行，只读。
    // 列顺序同 ListKinds.cs 的说明。
    // 出入库调整单：不按 cVouchType 过滤（入库 20、出库 21 与其他调整类型都列，类型在 vouch_type）；客户供应商编码 cUnitCode
    // 与供应商 cvencode 在附加列。表体没有 rowversion，增量只按表头 ufts；APPLY 汇总表体的行数、已记账行数和金额。
    // verified 即已记账（表体每行都有记账人 cbAccounter），verifier 取表头记账人 cAccounter，为空时取按 AutoID 第一个有记账人的表体行（与读取一致）。
    // 存货调价单：表头有 ufts，审核人 cverifier，审核日期 dverifydate。
    internal static partial class ListKinds
    {
        const string JvApply = " OUTER APPLY (SELECT COUNT(*) AS n, "
            + "SUM(CASE WHEN NULLIF(LTRIM(RTRIM(d.cbAccounter)), N'') IS NOT NULL THEN 1 ELSE 0 END) AS posted, "
            + "SUM(d.iJVPrice) AS amount "
            + "FROM JustInVouchs d WHERE d.cJVCode = h.cJVCode) b";
        // 同 IaAdjustRead.State：表头记账人为空时取按 AutoID 第一个有记账人的表体行。
        const string JvVerifier = "ISNULL(NULLIF(LTRIM(RTRIM(h.cAccounter)), N''), (SELECT TOP 1 LTRIM(RTRIM(d3.cbAccounter)) "
            + "FROM JustInVouchs d3 WHERE d3.cJVCode = h.cJVCode AND NULLIF(LTRIM(RTRIM(d3.cbAccounter)), N'') IS NOT NULL "
            + "ORDER BY d3.AutoID))";
        const string JvPosted = "CASE WHEN b.n > 0 AND b.posted = b.n THEN 1 ELSE 0 END";
        const string JvExtra = "h.cVouchType AS vouch_type, h.bRdFlag AS rd_flag, h.cRdCode AS rd_code, h.cAuto AS auto, "
            + "h.cbustype AS bus_type, h.cUnitCode AS unit_code, h.cvencode AS vendor_code, h.cHandler AS handler, "
            + "h.cJVMemo AS memo, b.n AS line_count, b.posted AS posted_lines, b.amount AS amount, "
            + "h.dnmaketime AS created_at, h.dModifiedTime AS modified_at";
        const string IpExtra = "h.cmainmemo AS memo";

        static string[][] IaSaRows()
        {
            return new string[][]
            {
                new string[] { IaAdjustRead.KindName, "JustInVouch", "id", "h.cJVCode", "h.dJVDate",
                    null, null, "h.cWhCode", "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    JvVerifier, null, null, null, JvPosted, null,
                    "ufts", null, null, null, null, JvExtra, JvApply },
                new string[] { InvPriceAdjustRead.KindName, "SA_InvPriceJustMain", "id", "h.ccode", "h.ddate",
                    null, null, null, "h.cdepcode", "h.cpersoncode", "h.cmaker",
                    "h.cverifier", "h.dverifydate", null, null, null, null,
                    "ufts", null, null, null, null, IpExtra, null }
            };
        }
    }
}
