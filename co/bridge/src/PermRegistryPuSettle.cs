namespace U8Co
{
    // 采购结算单写路由（PuSettleGen / PuSettleDel 用 ForKey 取）：write:purchase_settle:generate|delete；
    // 手工结算（PuSettleManGate）：write:purchase_settle:create，功能 id「手工结算」PU040302。
    // 功能 id 按 U8 授权目录（UA_Auth）：「自动结算」PU040301、「删除结算单」PU040315；未在 U8 客户端逐个授权点验。
    // 数据权限按每行判断（MoDelete.CheckRows），对象同读取规则：供应商、存货必有，部门、业务员、采购类型、仓库可空；
    // 行由桥拼（PuSettleGate.PermRows：表头的供应商、部门、业务员、采购类型 + 每行的存货、仓库）。
    internal static partial class PermRegistry
    {
        static PermRule[] PuSettleWriteRules()
        {
            return new PermRule[]
            {
                SettleWrite(PuSettleReq.GenerateRule, "采购自动结算", "PU040301"),
                SettleWrite(PuSettleReq.DeleteRule, "删除采购结算单", "PU040315"),
                SettleWrite(PuSettleManReq.CreateRule, "采购手工结算", "PU040302")
            };
        }

        static PermRule SettleWrite(string key, string title, string auth)
        {
            return R(key, title, A(auth), PermObj.H(PermObj.Vendor, "cVenCode"),
                PermObj.Opt(PermObj.Department, "cDepCode"), PermObj.Opt(PermObj.Person, "cPersonCode"),
                PermObj.Opt(PermObj.PurchaseType, "cPTCode"), PermObj.H(PermObj.Inventory, "cInvCode"),
                PermObj.Opt(PermObj.Warehouse, "cWhCode"));
        }
    }
}
