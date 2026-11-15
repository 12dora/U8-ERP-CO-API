namespace U8Co
{
    // 经营管理销售分析 reports/mgmt/sales、往来账期 reports/mgmt/arap_terms 的读权限（只读报表）。
    // 销售分析的数据来自销售发票，闸门收销售发票的查询 / 列表 id（同 voucher:sale_invoice）；U8 的「销售统计表」类账表 id
    // 未按授权目录核对，暂不登记。记录级数据权限挂在收入、成本两侧明细的同名列上（ReportsMgmtSalesSql 的 q 别名）：
    // 客户、存货必控，业务员、部门可空（存货核算明细上可能没有）。
    // 往来账期按 side 分应收 / 应付两条，功能 id 同往来余额表（总账表 AR060201_01 / AP060201_01）与账龄分析，
    // 数据权限按往来单位 cDwCode（同 arap_balance）。
    internal static partial class PermRegistry
    {
        static PermRule[] MgmtSalesArapRules()
        {
            return new PermRule[]
            {
                R(ReportsMgmtSalesReq.RuleKey, "经营管理销售分析",
                    A("SA03030201", "SA03030301", "SA03030202", "SA03030302", "SA03030101"),
                    PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Inventory, "cInvCode"),
                    PermObj.Opt(PermObj.Person, "cPersonCode"), PermObj.Opt(PermObj.Department, "cDepCode")),
                R("report:" + ReportsMgmtArapReq.Name + ":ar", "经营管理应收账期",
                    A("AR060201_01", "AR060301", ArAgingView), PermObj.H(PermObj.Customer, "cDwCode")),
                R("report:" + ReportsMgmtArapReq.Name + ":ap", "经营管理应付账期",
                    A("AP060201_01", "AP060301", ApAgingView), PermObj.H(PermObj.Vendor, "cDwCode"))
            };
        }
    }
}
