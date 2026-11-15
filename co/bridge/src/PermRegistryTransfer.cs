namespace U8Co
{
    // 应收冲应付 / 应付冲应收（arap/transfer，ArapTransfer 用 ForKey(TransferKey(…)) 取）。
    // 功能权限已按 U8 授权目录核对：「转账 → 应收冲应付」AR050502、「应付冲应收」AP050502；上级「转账」AR0505 / AP0505 同样放行。
    // 数据权限：应收一侧的单据按客户规则、应付一侧按供应商规则（往来单位 cDwCode 必控，部门、业务员可空），见 TransferRowRule。
    internal static partial class PermRegistry
    {
        public static string TransferKey(string flag)
        {
            return "write:arap:transfer:" + (flag == "AP" ? "ap" : "ar");
        }

        static PermRule[] TransferRules()
        {
            return new PermRule[]
            {
                R(TransferKey("AR"), "应收冲应付", A("AR050502", "AR0505"), WriteoffObjs(PermObj.Customer)),
                R(TransferKey("AP"), "应付冲应收", A("AP050502", "AP0505"), WriteoffObjs(PermObj.Vendor))
            };
        }

        // 某一侧单据表头的数据权限规则（只用它的受控对象，不查功能权限）：AR 按客户，AP 按供应商。
        public static PermRule TransferRowRule(string side)
        {
            bool ap = side == "AP";
            return new PermRule(TransferKey(side), ap ? "应付冲应收" : "应收冲应付", new string[0],
                WriteoffObjs(ap ? PermObj.Vendor : PermObj.Customer));
        }
    }
}
