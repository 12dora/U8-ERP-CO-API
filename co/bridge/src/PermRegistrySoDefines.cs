namespace U8Co
{
    // 销售订单表头自定义项写入（vouchers/so_defines，VoucherSoDefines.Permit 用 ForKey 取）。功能 id 是销售订单卡片 SA_17voucher 的
    // 「变更」按钮（changeorders）所需的「销售订单变更」SA03010105（改已审核的订单；不用「录入」）。
    // 按 UFMeta AA_FormButtonAuths 与 UFSystem UA_Auth_lang 核对：SA03010113 是「销售订单弃审」，不能用。
    // 数据权限按订单表头（同销售订单锁定）：客户、部门、业务员、销售类型。
    internal static partial class PermRegistry
    {
        internal const string SoChange = "SA03010105";

        static PermRule[] SoDefinesRules()
        {
            return new PermRule[]
            {
                R(VoucherSoDefines.RuleKey, "销售订单表头自定义项写入", A(SoChange),
                    PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Department, "cDepCode"),
                    PermObj.H(PermObj.Person, "cPersonCode"), PermObj.H(PermObj.SaleType, "cSTCode"))
            };
        }
    }
}
