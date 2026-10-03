namespace U8Co
{
    // ListKinds 的请购单行。列顺序同 ListKinds.cs 的说明。建议供应商在表体上，表头没有供应商列。
    internal static partial class ListKinds
    {
        const string AppExtra = "h.cPTCode AS pt_code, h.cBusType AS bus_type, h.IsWfControlled AS wf, "
            + "h.cMakeTime AS created_at, h.cModifyTime AS modified_at";

        static string[][] PuAppRows()
        {
            return new string[][]
            {
                new string[] { "purchase_requisition", "PU_AppVouch", "ID", "h.cCode", "h.dDate",
                    null, null, null, "h.cDepCode", "h.cPersonCode", "h.cMaker",
                    "h.cVerifier", "h.cAuditDate", "h.cCloser", null, null, null,
                    "ufts", "PU_AppVouchs", "ID", "dUfts", null, AppExtra, null }
            };
        }
    }
}
