namespace U8Co
{
    // 应收 / 应付制单、取消制单（arap/voucher、arap/voucher/delete，ArapVoucher / ArapVoucherDrop 用 ForKey(ArapVoucherKey(…)) 取）。
    // 功能权限：UFMeta 的 AA_FormButtonAuths 里「制单处理」窗体 AR_Preparation / AP_Preparation 的「制单」按钮是 AR0508 / AP0508
    // （U8 授权目录里叫「生成凭证」，核对）；取消制单在「凭证查询」里删除，没有单独的按钮授权，按同一个 AR0508 / AP0508 控制。
    // 数据权限同核销：单据表头（或往来明细）的往来单位 cDwCode 必控，部门、业务员可空。
    internal static partial class PermRegistry
    {
        public static string ArapVoucherKey(string flag, bool drop)
        {
            return "write:arap:" + (drop ? "voucher_delete:" : "voucher:") + (flag == "AP" ? "ap" : "ar");
        }

        static PermRule[] ArapVoucherRules()
        {
            return new PermRule[]
            {
                R(ArapVoucherKey("AR", false), "应收制单", A("AR0508"), WriteoffObjs(PermObj.Customer)),
                R(ArapVoucherKey("AP", false), "应付制单", A("AP0508"), WriteoffObjs(PermObj.Vendor)),
                R(ArapVoucherKey("AR", true), "应收取消制单", A("AR0508"), WriteoffObjs(PermObj.Customer)),
                R(ArapVoucherKey("AP", true), "应付取消制单", A("AP0508"), WriteoffObjs(PermObj.Vendor))
            };
        }
    }
}
