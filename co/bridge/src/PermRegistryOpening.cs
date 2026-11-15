namespace U8Co
{
    // 期初记账 / 取消记账（openings/post，OpeningPost 用 ForKey(OpeningPostReq.RuleKey(…)) 取）。
    // 采购：U8 菜单「采购期初记账」的功能 id PU0206；取消记账在同一窗体，按同一个 id 控制。
    // 存货核算：「期初余额」窗体 IA_BeginningBalance / IA_BeginBalance_ST 的记账（Keep）、恢复（recover）按钮的功能 id
    // 都是 ASM3102（UFMeta AA_FormButtonAuths 核对过）；取数按钮 ASM310103 不另查（桥的记账含取数）。
    // 只查功能权限，没有数据权限对象（改的是整个模块的标志）。
    // 应收 / 应付期初单据（openings/arap，OpeningsArap 用 ForKey(OpeningsArapReq.RuleKey(…)) 取）：
    // 「期初单据录入」窗体 AR_FrmQcVouch / AP_FrmQcVouch 的功能 id AR0306 / AP0306，新增、删除、审核、弃审同一个 id；
    // 与采购期初记账一样只查功能权限。
    internal static partial class PermRegistry
    {
        static PermRule[] OpeningPostRules()
        {
            return new PermRule[]
            {
                R(OpeningPostReq.PuRule, "采购期初记账", A("PU0206")),
                R(OpeningPostReq.IaRule, "存货核算期初记账", A("ASM3102")),
                R(OpeningsArapReq.ArRule, "应收期初单据录入", A("AR0306")),
                R(OpeningsArapReq.ApRule, "应付期初单据录入", A("AP0306"))
            };
        }
    }
}
