namespace U8Co
{
    // 应收 / 应付票据的读取（vouchers/list、vouchers/search 的 type=ar_note / ap_note，notes/get）：规则键 voucher:<type>。
    // 功能 id 取票据管理列表（AR_BillManagement / AP_BillManagement）「Query」按钮的 AR2231 / AP2231（「票据列表查询」，按 U8 授权目录核对）。
    // 往来单位 cDwCode 在测试账套上几乎都为空（单位名称只在 cDWName），按可空列；部门、业务员、项目同应收应付单据，按可空列。
    internal static partial class PermRegistry
    {
        static PermRule[] NoteReadRules()
        {
            return new PermRule[]
            {
                NoteRule(NotesReadReq.ArType, "应收票据", PermObj.Customer, A("AR2231")),
                NoteRule(NotesReadReq.ApType, "应付票据", PermObj.Vendor, A("AP2231"))
            };
        }

        static PermRule NoteRule(string type, string title, string partner, string[] auths)
        {
            return V(type, title, auths, PermObj.Opt(partner, "cDwCode"), PermObj.Opt(PermObj.Department, "cDeptCode"),
                PermObj.Opt(PermObj.Person, "cPerson"), OptItem("cItem_Class", "cItemCode"));
        }
    }
}
