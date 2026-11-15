namespace U8Co
{
    // ListKinds 的形态转换单、调拨申请单、盘点单。列顺序同 ListKinds.cs 的说明。
    // 形态转换单表头没有仓库（仓库在行上），只列 cVouchType='15'（同表还有组装 13、拆卸 14）；
    // 调拨申请单的 wh / dep 是调出方，调入方在附加列（同调拨单 TvExtra）；盘点单的审核人是 cAccounter / dveridate。
    // 表体有 rowversion 的（调拨申请 ST_AppTransVouchs.ufts、盘点 CheckVouchs.cbufts）增量按表头、表体较大的算；
    // 形态转换的 AssemVouchs 没有 rowversion，只按表头（账套库核对过）。
    internal static partial class ListKinds
    {
        const string AvExtra = "h.cIRdCode AS in_rd_code, h.cORdCode AS out_rd_code, h.csource AS source, "
            + "h.dnmaketime AS created_at, h.dnmodifytime AS modified_at";
        const string CvExtra = "h.cIRdCode AS in_rd_code, h.cORdCode AS out_rd_code, h.dACDate AS check_date, "
            + "h.csource AS source, h.dnmaketime AS created_at, h.dnmodifytime AS modified_at";
        const string PaExtra = "h.cMemo AS memo, h.csource AS source, h.dnmaketime AS created_at, "
            + "h.dnmodifytime AS modified_at";

        static string[][] StMiscRows()
        {
            return new string[][]
            {
                new string[] { "shape_change", "AssemVouch", "ID", "h.cAVCode", "h.dAVDate",
                    null, null, null, "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cVerifyPerson", "h.dVerifyDate", null, null, null, null,
                    "ufts", null, null, null, "h.cVouchType = N'15'", AvExtra, null },
                new string[] { "transfer_request", "ST_AppTransVouch", "ID", "h.cTVCode", "h.dTVDate",
                    null, null, "h.cOWhCode", "h.cODepCode", "h.cPersonCode", "h.cMaker",
                    "h.cVerifyPerson", "h.dVerifyDate", "h.cCloser", null, null, null,
                    "ufts", "ST_AppTransVouchs", "ID", "ufts", null, TvExtra, null },
                new string[] { "stock_check", "CheckVouch", "ID", "h.cCVCode", "h.dCVDate",
                    null, null, "h.cWhCode", "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cAccounter", "h.dveridate", null, null, null, null,
                    "ufts", "CheckVouchs", "ID", "cbufts", null, CvExtra, null },
                // 货位调整单：表体 AdjustPVouchs 没有 rowversion，增量只按表头 ufts（账套库核对过）。
                new string[] { "position_adjust", "AdjustPVouch", "Id", "h.cVouchCode", "h.dDate",
                    null, null, "h.cWhCode", "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.chandler", "h.dVeriDate", null, null, null, null,
                    "ufts", null, null, null, null, PaExtra, null }
            };
        }
    }
}
