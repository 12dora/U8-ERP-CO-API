namespace U8Co
{
    // 月末结账 / 取消结账（periods/close，PeriodClose 用 ForKey(PeriodCloseReq.RuleKey(…)) 取）。
    // 功能 id（U8 菜单「月末结账」）：采购 PU0207、销售 SA020901、库存 ST0304、存货核算 IA2007、应收 AR0509、应付 AP0509、
    // 总账结账 GL1512、总账反结账 GL1520。其余模块的取消结账在结账窗体里，按结账的 id 控制。
    // 只查功能权限，没有数据权限对象（改的是整个模块某一期的标志）。through 要所涉每个模块的结账权限。
    internal static partial class PermRegistry
    {
        static PermRule[] PeriodCloseRules()
        {
            return new PermRule[]
            {
                R("write:period:pu:close", "采购月末结账", A("PU0207")),
                R("write:period:sa:close", "销售月末结账", A("SA020901")),
                R("write:period:st:close", "库存月末结账", A("ST0304")),
                R("write:period:ia:close", "存货核算月末结账", A("IA2007")),
                R("write:period:ar:close", "应收月末结账", A("AR0509")),
                R("write:period:ap:close", "应付月末结账", A("AP0509")),
                R("write:period:gl:close", "总账结账", A("GL1512")),
                R("write:period:gl:reopen", "总账反结账", A("GL1520"))
            };
        }
    }
}
