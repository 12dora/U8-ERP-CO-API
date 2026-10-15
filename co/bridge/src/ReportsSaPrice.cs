using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 销售价格历史 sales_price_history：销售发票表体逐行列出日期、数量、金额和折算出的单价，新的在前。
    // 口径同经营管理销售分析（ReportsMgmtSalesSql）：非期初（bFirst）发票；缺省只含销售复核人 cChecker 非空的，
    // include_unverified 时不限。金额取本币：amount 无税 iNatMoney，amount_tax 价税合计 iNatSum；
    // currency 是发票币种（外币发票的金额也已折成本币）。单价 = 金额 ÷ 数量（六位小数），数量为 0 时为 null；红字发票数量金额都是负数。
    // 数据权限（客户、存货）挂在派生表 q 的同名列上。读线程，只用 ctx.Conn、登录日期和权限快照。
    internal static class ReportsSaPrice
    {
        // {INNER} 是发票行的过滤条件（客户、存货、日期、审核），{PERM} 是数据权限。
        // 参数：limit+1 | {INNER} 的参数 | {PERM} 的参数。
        const string Main = "SELECT TOP (?) q.dt, q.code, q.cCusCode, c.cCusName AS customer_name, q.cInvCode, i.cInvName AS inv_name,"
            + " u.cComUnitName AS unit, q.qty, q.amt, q.amt_tax, q.cur"
            + " FROM (SELECT CONVERT(varchar(10), h.dDate, 23) AS dt, h.dDate AS d0, h.cSBVCode AS code, h.cCusCode AS cCusCode,"
            + " b.cInvCode AS cInvCode, b.AutoID AS lid, ISNULL(b.iQuantity, 0) AS qty, ISNULL(b.iNatMoney, 0) AS amt,"
            + " ISNULL(b.iNatSum, 0) AS amt_tax, h.cexch_name AS cur"
            + " FROM SaleBillVouch h JOIN SaleBillVouchs b ON b.SBVID=h.SBVID WHERE ISNULL(h.bFirst, 0)=0{INNER}) q"
            + " LEFT JOIN Customer c ON c.cCusCode=q.cCusCode LEFT JOIN Inventory i ON i.cInvCode=q.cInvCode"
            + " LEFT JOIN ComputationUnit u ON u.cComunitCode=i.cComUnitCode WHERE 1=1{PERM}"
            + " ORDER BY q.d0 DESC, q.code DESC, q.lid DESC";

        const string Verified = " AND NULLIF(LTRIM(RTRIM(h.cChecker)), N'') IS NOT NULL";

        public static ApiResult Run(WorkContext ctx, ReportArgs ignored)
        {
            PriceHistoryArgs a = SaArgs.Price(ctx.Item.Body);
            string to = ReportsStockSql.DateOr(ctx, a.DateTo);
            if (string.CompareOrdinal(a.DateFrom, to) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to（缺省为登录日期）", "date_from");
            }
            List<object> ps = new List<object>();
            ps.Add(a.Limit + 1);
            string sql = Sql(ctx, a, to, ps);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), a.Limit + 1);
            bool truncated = SaCalc.Cut(rows, a.Limit);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count; i++)
            {
                items.Add(Item(rows[i]));
            }
            Dictionary<string, object> body = SaCalc.Body(items, truncated);
            body["date_from"] = a.DateFrom;
            body["date_to"] = to;
            return ApiResult.Ok(body);
        }

        // 整句和参数。ps 里已有 limit+1，接着是发票行的条件，最后是数据权限。
        static string Sql(WorkContext ctx, PriceHistoryArgs a, string to, List<object> ps)
        {
            StringBuilder inner = new StringBuilder();
            SaCalc.In(inner, ps, "h.cCusCode", a.Customers);
            SaCalc.In(inner, ps, "b.cInvCode", a.Invs);
            SaCalc.Between(inner, ps, "h.dDate", a.DateFrom, to);
            if (!a.Unverified)
            {
                inner.Append(Verified);
            }
            StringBuilder perm = new StringBuilder();
            PermHook.Where(perm, ps, ctx, "q");
            return Main.Replace("{INNER}", inner.ToString()).Replace("{PERM}", perm.ToString());
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row)
        {
            decimal qty = SaCalc.Dec(row, "qty");
            decimal amount = SaCalc.Dec(row, "amt");
            decimal tax = SaCalc.Dec(row, "amt_tax");
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["date"] = GlSql.Col(row, "dt");
            item["code"] = GlSql.Col(row, "code");
            item["customer_code"] = GlSql.Col(row, "cCusCode");
            item["customer_name"] = Reports.Text(row, "customer_name");
            item["inv_code"] = GlSql.Col(row, "cInvCode");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["unit"] = Reports.Text(row, "unit");
            item["qty"] = Reports.Qty(row, "qty");
            item["amount"] = SaCalc.Money(amount);
            item["amount_tax"] = SaCalc.Money(tax);
            item["unit_price"] = SaCalc.Ratio(amount, qty);
            item["unit_price_tax"] = SaCalc.Ratio(tax, qty);
            item["currency"] = Reports.Text(row, "cur");
            return item;
        }
    }
}
