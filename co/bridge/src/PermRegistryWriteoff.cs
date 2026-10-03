namespace U8Co
{
    // 应收 / 应付核销（arap/writeoff，ArapWriteoff 用 ForKey(WriteoffKey(…)) 取）。功能 id 已按 U8 授权目录核对：
    // 手工核销是「核销处理 → 手工核销」AR050201 / AP050201；另收「选择收款 / 选择付款」AR0503 / AP0503——U8 的选择收款
    // 在一个界面里同时生成收款单并核销，持有它的操作员在 U8 里本来就能核销。
    // 数据权限按收付款单表头和每张被核销单据的表头（往来单位 cDwCode 必控，部门、业务员可空；发票的列在 WriteoffKind 里取了同名别名）。
    // 自动核销（arap/writeoff/auto，ArapAutoWriteoff 用 ForKey(WriteoffAutoKey(…)) 取）：「自动核销」AR050202 / AP050202，数据权限同手工核销。
    // 取消核销（arap/writeoff/cancel）：U8「其他处理 → 取消操作」AR0807 / AP0807（更正；原先的 AR050203 / AP050203 是
    // 「收款单删除 / 付款单删除」，AR0502 / AP0502 是「收款单录入 / 付款单录入」）。数据权限同核销。
    internal static partial class PermRegistry
    {
        public static string WriteoffKey(string flag)
        {
            return "write:arap:writeoff:" + (flag == "AP" ? "ap" : "ar");
        }

        public static string WriteoffAutoKey(string flag)
        {
            return "write:arap:writeoff_auto:" + (flag == "AP" ? "ap" : "ar");
        }

        public static string WriteoffCancelKey(string flag)
        {
            return "write:arap:writeoff_cancel:" + (flag == "AP" ? "ap" : "ar");
        }

        static PermRule[] WriteoffRules()
        {
            return new PermRule[]
            {
                R(WriteoffKey("AR"), "应收核销", A("AR050201", "AR0503"), WriteoffObjs(PermObj.Customer)),
                R(WriteoffKey("AP"), "应付核销", A("AP050201", "AP0503"), WriteoffObjs(PermObj.Vendor)),
                R(WriteoffAutoKey("AR"), "应收自动核销", A("AR050202"), WriteoffObjs(PermObj.Customer)),
                R(WriteoffAutoKey("AP"), "应付自动核销", A("AP050202"), WriteoffObjs(PermObj.Vendor)),
                R(WriteoffCancelKey("AR"), "应收取消核销", A("AR0807"), WriteoffObjs(PermObj.Customer)),
                R(WriteoffCancelKey("AP"), "应付取消核销", A("AP0807"), WriteoffObjs(PermObj.Vendor))
            };
        }

        // 核销记录查询（reports/arap_writeoffs，只读）：首选 U8「应收核销明细表 / 应付核销明细表」AR060107 / AP060107（按
        // U8 授权目录核对）；有手工核销（含「选择收款 / 选择付款」AR0503 / AP0503）或取消操作权限的操作员也放行
        // （查询是为了核对或取消）。原先的「我的账表」AR0601 已去掉。
        // 数据权限按往来明细的往来单位 cDwCode（同 arap_balance）。
        static PermRule[] WriteoffListRules()
        {
            return new PermRule[]
            {
                R("report:" + ReportsArapWriteoffReq.Name + ":ar", "应收核销记录",
                    A("AR060107", "AR050201", "AR0503", "AR0807"), PermObj.H(PermObj.Customer, "cDwCode")),
                R("report:" + ReportsArapWriteoffReq.Name + ":ap", "应付核销记录",
                    A("AP060107", "AP050201", "AP0503", "AP0807"), PermObj.H(PermObj.Vendor, "cDwCode"))
            };
        }

        static PermObj[] WriteoffObjs(string partner)
        {
            return new PermObj[]
            {
                PermObj.H(partner, "cDwCode"), PermObj.Opt(PermObj.Department, "cDeptCode"),
                PermObj.Opt(PermObj.Person, "cPerson")
            };
        }
    }
}
