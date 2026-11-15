namespace U8Co
{
    // 应收 / 应付并账（arap/merge，ArapMerge 用 ForKey(MergeKey(…)) 取）。
    // 功能权限已按 U8 授权目录核对：并账在 U8 里是「转账 → 应收冲应收 / 应付冲应付」AR050504 / AP050504，
    // 上级「转账」AR0505 / AP0505 同样放行。
    // 数据权限同核销：各单据表头（往来单位 cDwCode 必控，部门、业务员可空），另加并出、并入两个往来单位。
    internal static partial class PermRegistry
    {
        public static string MergeKey(string flag)
        {
            return "write:arap:merge:" + (flag == "AP" ? "ap" : "ar");
        }

        static PermRule[] MergeRules()
        {
            return new PermRule[]
            {
                R(MergeKey("AR"), "应收并账", A("AR050504", "AR0505"), WriteoffObjs(PermObj.Customer)),
                R(MergeKey("AP"), "应付并账", A("AP050504", "AP0505"), WriteoffObjs(PermObj.Vendor))
            };
        }
    }
}
