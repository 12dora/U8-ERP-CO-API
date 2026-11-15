namespace U8Co
{
    // 采购发票应付审核、销售发票应收审核及弃审（vouchers/verify 的 arap_verify / arap_unverify，ArapAudit 用 ForKey 取）。
    // AP050104 / AR050104 是 U8 界面（UFAPSuite Sign_PurBill / Sign_SaleBill）审核前查的功能 id；
    // 弃审 AP050105 / AR050105（按 U8 授权目录核对）。数据权限按表头（同读取规则的表头部分），ArapAuditSql 的带锁读取带出这些列。
    internal static partial class PermRegistry
    {
        static PermRule[] ArapAuditRules()
        {
            PermObj[] pu = new PermObj[]
            {
                PermObj.H(PermObj.Vendor, "cVenCode"), PermObj.H(PermObj.Department, "cDepCode")
            };
            PermObj[] sa = new PermObj[]
            {
                PermObj.H(PermObj.Customer, "cCusCode"), PermObj.H(PermObj.Department, "cDepCode")
            };
            return new PermRule[]
            {
                R("write:purchase_invoice:arap_verify", "采购发票应付审核", A("AP050104"), pu),
                R("write:purchase_invoice:arap_unverify", "采购发票应付弃审", A("AP050105"), pu),
                R("write:sale_invoice:arap_verify", "销售发票应收审核", A("AR050104"), sa),
                R("write:sale_invoice:arap_unverify", "销售发票应收弃审", A("AR050105"), sa)
            };
        }
    }
}
