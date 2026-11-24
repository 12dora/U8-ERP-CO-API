namespace U8Co
{
    // 账套自己的缺省值，不写死某一账套的档案编码：本位币、默认采购类型、销售报价是否含税。请求内用 WorkContext 上的缓存
    // （HomeCurrency、DefaultPurchaseType、PurchaseTypeOr、SalePriceTaxed），一个请求只查一次。
    internal static class AccDefaults
    {
        // 档案缺省值表（ArcKind.Defaults）里代表「本位币」的占位值，ArcTpl.Defaults 换成 Home(conn)。
        internal const string HomeToken = "{home_currency}";
        const string HomeSql = "select top 1 cexch_name from foreigncurrency where iotherused=-1";
        const string PtSql = "select top 1 cPTCode from PurchaseType where bDefault=1 order by cPTCode";
        const string SalePriceSql = "select top 1 ltrim(rtrim(isnull(nullif(ltrim(rtrim(cValue)), N''), cDefault))) from AccInformation "
            + "where cSysID=N'SA' and cName=N'bSalePrice'";

        // 本位币：foreigncurrency.iotherused = -1（u8-notes.md「币种」）。U8 建账必设本位币；读不到时退回人民币，只防坏账套。
        public static string Home(object conn)
        {
            string name = Values.Text(Rows.Scalar(conn, HomeSql, new object[0])).Trim();
            return name.Length == 0 ? ArapReq.Rmb : name;
        }

        // 默认采购类型（PurchaseType.bDefault = 1）；没有返回空串，交给 U8 判断。
        public static string PurchaseType(object conn)
        {
            return Values.Text(Rows.Scalar(conn, PtSql, new object[0])).Trim();
        }

        // 销售选项「销售报价是否含税」（AccInformation SA.bSalePrice，True / False）：行上 bsaleprice 写 "1" / "0"；
        // 没有这一项或值认不出返回空串（不写，SaleTaxFlag）。
        public static string SalePriceTaxed(object conn)
        {
            string value = Values.Text(Rows.Scalar(conn, SalePriceSql, new object[0])).Trim();
            if (SaleTaxFlag.IsOn(value))
            {
                return "1";
            }
            return value == "0" || string.Equals(value, "false", System.StringComparison.OrdinalIgnoreCase) ? "0" : "";
        }
    }
}
