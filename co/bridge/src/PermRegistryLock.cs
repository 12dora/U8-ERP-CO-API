namespace U8Co
{
    // 销售订单锁定、解锁（vouchers/lock，写路由，VoucherLock 用 ForKey 取）。id 取自 UFMeta AA_FormButtonAuths：
    // 销售订单卡片 SA_17voucher 的 lock / unlock 是 SA03010108 / SA03010109。数据权限按表头对象（同读取规则的表头部分）。
    // 采购订单锁定暂不支持（DoLock 实测一律拒绝），不登记 PU0311 / PU0312。
    internal static partial class PermRegistry
    {
        static PermRule[] LockRules()
        {
            PermObj[] sa = new PermObj[]
            {
                PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Department, "cDepCode"),
                PermObj.H(PermObj.Person, "cPersonCode"), PermObj.H(PermObj.SaleType, "cSTCode")
            };
            return new PermRule[]
            {
                R(VoucherLock.SaLockRule, "销售订单锁定", A("SA03010108"), sa),
                R(VoucherLock.SaUnlockRule, "销售订单解锁", A("SA03010109"), sa)
            };
        }
    }
}
