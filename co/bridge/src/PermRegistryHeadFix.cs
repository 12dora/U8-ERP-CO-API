namespace U8Co
{
    // 历史发货单表头修正（vouchers/head_fix，VoucherHeadFix.Permit 用 ForKey 取）。功能 id 按 U8 授权目录（UA_Auth）核对：
    // 「发货单变更」SA03020113（改已审核的发货单；「录入」SA03020101 只管未审核的单）。
    // 数据权限按发货单表头（同发货单读取规则）：客户、部门、原业务员、销售类型；新业务员另在处理函数里按人员权限判断。
    internal static partial class PermRegistry
    {
        static PermRule[] HeadFixRules()
        {
            return new PermRule[]
            {
                R(VoucherHeadFix.RuleKey, "发货单表头修正", A("SA03020113"),
                    PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Department, "cDepCode"),
                    PermObj.H(PermObj.Person, "cPersonCode"), PermObj.H(PermObj.SaleType, "cSTCode"))
            };
        }
    }
}
