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
                R("write:ia:period_end", "存货核算期末处理", A("IA2006"))
            };
        }
    }
}
