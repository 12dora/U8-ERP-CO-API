using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 价格表 price_list：kind=customer 客户价格表（SA_CusUPrice），inventory 存货价格表（SA_InvUPrice），
    // vendor 供应商存货价格表（Ven_Inv_Price，采购）。缺省只列 as_of（缺省登录日期）当天有效、未失效的行；
    // all_dates 为 true 时全部列出。按表主键翻页。列的含义按 U8 表结构推断（未经实测）。
    // 每种来源先包成同名列的派生表 p（cCusCode、cVenCode、cInvCode），数据权限条件对三种都适用。
    internal static class ReportsPrice
    {
        // 客户价格表：客户调价单卡片的「查询客户价格」（SA0312030101）；存货价格表：存货调价单的「查询存货价格」（SA0312020101）；
        // 供应商存货价格表：列表 pu_veninvpricelst 的打印 / 输出（PU060105 / PU060106）。
        // 上级菜单 SA031203 / SA031202 / PU0601 不进授权表（核对），不登记。
        internal static readonly string[] CustomerAuths = new string[] { "SA0312030101" };
        internal static readonly string[] InventoryAuths = new string[] { "SA0312020101" };
        internal static readonly string[] VendorAuths = new string[] { "PU060105", "PU060106" };

        static readonly Dictionary<string, string> Sources = BuildSources();
        // 功能权限：路由规则收三种的并集，这里再按 kind 查一次。
        static readonly Dictionary<string, string[]> Auths = BuildAuths();

        const string Head = "SELECT TOP (?) p.*, i.cInvName inv_name, i.cInvStd inv_std,"
            + " CONVERT(varchar(10), p.d1, 23) start_date, CONVERT(varchar(10), p.d2, 23) end_date FROM ";
        const string Tail = " LEFT JOIN Inventory i ON i.cInvCode=p.cInvCode WHERE p.id>?";
        const string DateSql = " AND ISNULL(p.bad, 0)=0 AND p.d1<DATEADD(day, 1, CONVERT(date, ?, 23))"
            + " AND (p.d2 IS NULL OR p.d2>=CONVERT(date, ?, 23))";
        // 客户价格表可以按客户分类定价（cCusCode 为空、cCusCCode 为分类）：按客户查时连同它所属分类的行。
        const string CustomerSql = " AND (p.cCusCode=? OR (NULLIF(p.cCusCode, N'') IS NULL"
            + " AND p.cus_class=(SELECT c.cCCCode FROM Customer c WHERE c.cCusCode=?)))";

        static Dictionary<string, string> BuildSources()
        {
            Dictionary<string, string> map = new Dictionary<string, string>(StringComparer.Ordinal);
            map.Add("customer", "(SELECT x.AutoID id, x.cCusCode, x.cCusCCode cus_class, CONVERT(nvarchar(20), NULL) cVenCode,"
                + " x.cInvCode, x.dStartDate d1, x.dEndDate d2, ISNULL(x.binvalid, 0) bad, x.cexch_name cur,"
                + " x.fminquantity min_q, x.bsaleprice tax_inc, x.iInvSCost quote, x.iCusDisRate disc, x.iInvNowCost price,"
                + " x.fcusminprice min_p, x.bsales promo, x.cMemo memo FROM SA_CusUPrice x) p");
            map.Add("inventory", "(SELECT x.AutoID id, CONVERT(nvarchar(20), NULL) cCusCode, CONVERT(nvarchar(20), NULL) cVenCode,"
                + " x.cInvCode, x.dStartDate d1, x.dEndDate d2, ISNULL(x.binvalid, 0) bad, x.cexch_name cur,"
                + " x.fminquantity min_q, x.bsaleprice tax_inc, x.iUPrice1 p1, x.ISalePrice1 t1, x.iUPrice2 p2, x.ISalePrice2 t2,"
                + " x.iUPrice3 p3, x.ISalePrice3 t3, x.IUPrice4 p4, x.ISalePrice4 t4, x.IUPrice5 p5, x.ISalePrice5 t5,"
                + " x.IUPrice6 p6, x.ISalePrice6 t6, x.IUPrice7 p7, x.ISalePrice7 t7, x.IUPrice8 p8, x.ISalePrice8 t8,"
                + " x.IUPrice9 p9, x.ISalePrice9 t9, x.IUPrice10 p10, x.ISalePrice10 t10, x.bsales promo, x.cMemo memo"
                + " FROM SA_InvUPrice x) p");
            map.Add("vendor", "(SELECT x.Autoid id, CONVERT(nvarchar(20), NULL) cCusCode, x.cVenCode, x.cInvCode,"
                + " x.dEnableDate d1, x.dDisableDate d2, 0 bad, x.cExch_Name cur, x.iLowerLimit min_q, x.iUpperLimit max_q,"
                + " x.btaxcost tax_inc, x.iUnitPrice price, x.iTaxUnitPrice tax_price, x.iTaxRate tax_rate,"
                + " x.bPromotion promo, x.iSupplyType supply, x.cMemo memo FROM Ven_Inv_Price x) p");
            return map;
        }

        static Dictionary<string, string[]> BuildAuths()
        {
            Dictionary<string, string[]> map = new Dictionary<string, string[]>(StringComparer.Ordinal);
            map.Add("customer", CustomerAuths);
            map.Add("inventory", InventoryAuths);
            map.Add("vendor", VendorAuths);
            return map;
        }

        public static ApiResult Prices(WorkContext ctx, ReportArgs a)
        {
            StockReportArgs s = ReportsStockReq.Parse(a, ctx.Item.Body);
            RequireKind(ctx, s.Kind);
            string asOf = s.AllDates ? null : ReportsStockSql.DateOr(ctx, s.AsOf);
            List<object> ps = new List<object>(new object[] { a.Limit + 1, ReportsStockSql.AfterId(a.After) });
            StringBuilder sql = new StringBuilder(Head).Append(Sources[s.Kind]).Append(Tail);
            if (asOf != null)
            {
                sql.Append(DateSql);
                ps.Add(asOf);
                ps.Add(asOf);
            }
            if (s.Customer.Length > 0)
            {
                sql.Append(CustomerSql);
                ps.Add(s.Customer);
                ps.Add(s.Customer);
            }
            ReportsStockSql.Equal(sql, ps, "p.cVenCode", s.Vendor);
            ReportsStockSql.Equal(sql, ps, "p.cInvCode", s.Inv);
            // 数据权限：客户、供应商（可空）、存货。
            PermHook.Where(sql, ps, ctx, "p");
            sql.Append(" ORDER BY p.id");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(Item(s.Kind, rows[i]));
            }
            string next = rows.Count > a.Limit ? Reports.Cursor(GlSql.Col(rows[a.Limit - 1], "id")) : null;
            Dictionary<string, object> body = ReportsStockSql.Page(items, next);
            body["kind"] = s.Kind;
            body["as_of"] = asOf;
            return ApiResult.Ok(body);
        }

        static void RequireKind(WorkContext ctx, string kind)
        {
            PermContext p = PermCheck.Of(ctx);
            if (!p.HasAny(Auths[kind]))
            {
                throw new BridgeException(403, "no_permission", "没有" + Title(kind) + "查询权限");
            }
        }

        static string Title(string kind)
        {
            if (kind == "customer")
            {
                return "客户价格表";
            }
            return kind == "inventory" ? "存货价格表" : "供应商存货价格表";
        }

        static Dictionary<string, object> Item(string kind, Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["id"] = GlSql.Int(row, "id");
            item["inv_code"] = Reports.Text(row, "cInvCode");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["inv_std"] = Reports.Text(row, "inv_std");
            item["currency"] = Reports.Text(row, "cur");
            item["start_date"] = Reports.Text(row, "start_date");
            item["end_date"] = Reports.Text(row, "end_date");
            item["min_qty"] = ReportsStockSql.OptQty(row, "min_q");
            item["tax_included"] = GlSql.Bit(row, "tax_inc");
            item["promotion"] = GlSql.Bit(row, "promo");
            item["memo"] = Reports.Text(row, "memo");
            if (kind == "customer")
            {
                Customer(item, row);
            }
            else if (kind == "inventory")
            {
                item["invalid"] = GlSql.Bit(row, "bad");
                item["levels"] = Levels(row);
            }
            else
            {
                Vendor(item, row);
            }
            return item;
        }

        static void Customer(Dictionary<string, object> item, Dictionary<string, object> row)
        {
            item["customer"] = Reports.Text(row, "cCusCode");
            item["customer_class"] = Reports.Text(row, "cus_class");
            item["invalid"] = GlSql.Bit(row, "bad");
            item["quote"] = ReportsStockSql.OptQty(row, "quote");
            item["discount_rate"] = ReportsStockSql.OptQty(row, "disc");
            item["price"] = ReportsStockSql.OptQty(row, "price");
            item["min_price"] = ReportsStockSql.OptQty(row, "min_p");
        }

        static void Vendor(Dictionary<string, object> item, Dictionary<string, object> row)
        {
            item["vendor"] = Reports.Text(row, "cVenCode");
            item["max_qty"] = ReportsStockSql.OptQty(row, "max_q");
            item["price"] = ReportsStockSql.OptQty(row, "price");
            item["tax_price"] = ReportsStockSql.OptQty(row, "tax_price");
            item["tax_rate"] = ReportsStockSql.OptQty(row, "tax_rate");
            item["supply_type"] = GlSql.Int(row, "supply");
        }

        // 存货价格表的 10 级价格：level、price（无税，iUPrice n）、tax_price（含税，ISalePrice n），两者都空的级别不列。
        static List<object> Levels(Dictionary<string, object> row)
        {
            List<object> list = new List<object>();
            for (int k = 1; k <= 10; k++)
            {
                string n = k.ToString(CultureInfo.InvariantCulture);
                object price = ReportsStockSql.OptQty(row, "p" + n);
                object tax = ReportsStockSql.OptQty(row, "t" + n);
                if (price == null && tax == null)
                {
                    continue;
                }
                Dictionary<string, object> level = new Dictionary<string, object>();
                level["level"] = k;
                level["price"] = price;
                level["tax_price"] = tax;
                list.Add(level);
            }
            return list;
        }
    }
}
