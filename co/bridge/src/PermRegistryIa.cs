namespace U8Co
{
    // 存货核算记账、期末处理（ia/post、ia/period_end，IaRun 用 ForKey(IaReq.RuleKey(…)) 取）。
    // 功能 id（UFSystem..UA_Auth_lang 实查）：单据记账 IA2004、恢复记账 IA2005、期末处理 IA2006（取消期末处理在同一窗体，
    // 按期末处理的 id）。月末结账 IA2007 见 PermRegistryPeriod。
    // 只查功能权限，没有数据权限对象（处理的是整个账套某一月的全部单据）。
    internal static partial class PermRegistry
    {
        static PermRule[] IaRules()
        {
            return new PermRule[]
            {
                R("write:ia:post", "存货核算单据记账", A("IA2004")),
                R("write:ia:unpost", "存货核算恢复记账", A("IA2005")),
                R("write:ia:period_end", "存货核算期末处理", A("IA2006")),
                // 存货核算制单（生成凭证）、取消制单（凭证列表里删除）：CHHSSQL.DLL 制单窗体查 IASCPZ（「生成凭证」，
                // 与 OneToOne / AllToOne 同处，并按它查 UA_Task）、删除凭证的 SQL 旁查 IACXPZ（「凭证查询」）。未在 U8 客户端逐个授权点验。
                R(IaVoucherReq.RuleKey(false), "存货核算制单", A("IASCPZ")),
                R(IaVoucherReq.RuleKey(true), "存货核算取消制单", A("IACXPZ"))
            };
        }
    }
}
