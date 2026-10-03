namespace U8Co
{
    // 经营管理报表（总账口径）：reports/mgmt/pnl、mgmt/meta、mgmt/cash_stock，只读。
    // 功能 id 沿用同口径的总账报表：损益和资金存货同科目余额表 report:gl_balance（余额表 GL030301、明细账 GL0305、GL030101），
    // 受控对象是科目（总账选项「明细账查询权限控制到科目」打开时过滤，见 PermContext.Controls）；
    // 数据水位同月末结账状态 report:close_status（结账 GL1512、反结账 GL1520，另收余额表、明细账的查询），不按记录过滤，
    // 所以 水位里不给金额（ReportsMgmtMeta）。
    // cash_stock 另按段过滤数据权限（ReportsMgmtCashSql.Perm 只挂本段用到的对象，不整条规则一起挂）：科目（资金、票据科目）、
    // 客户（未处理应收票据，cDwCode 为空取 cEndorser）、存货与仓库（存货结存、产成品入库；按存货核算时结存行仓库为空，放行）、
    // 供应商（采购）。不另查采购、库存的功能权限。mgmt/pnl 按部门时，部门不在授权内的分录在聚合前去掉、部门名称按部门数据权限
    // 显示（ReportsMgmtPnlSql.DeptRows）；按项目时同样在聚合前按项目数据权限过滤（ReportsMgmtPnlSql.ItemRows），都不登记在规则里。
    internal static partial class PermRegistry
    {
        static PermRule[] MgmtGlRules()
        {
            return new PermRule[]
            {
                R(ReportsMgmtGlReq.PnlRule, "经营管理损益查询", A("GL030301", "GL0305", "GL030101"), PermObj.H(PermObj.Account, "ccode")),
                R(ReportsMgmtGlReq.MetaRule, "经营管理数据水位", A("GL1512", "GL1520", "GL030301", "GL0305")),
                R(ReportsMgmtGlReq.CashRule, "经营管理资金存货查询", A("GL030301", "GL0305", "GL030101"),
                    PermObj.H(PermObj.Account, "ccode"), PermObj.H(PermObj.Customer, "cCusCode"),
                    PermObj.H(PermObj.Inventory, "cInvCode"), PermObj.Opt(PermObj.Warehouse, "cWhCode"),
                    PermObj.H(PermObj.Vendor, "cVenCode"))
            };
        }
    }
}
