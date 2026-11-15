using System.Collections.Generic;

namespace U8Co
{
    // 在手订单 open_orders：未执行完的销售订单行按客户、存货或逐单汇总余量和余额（口径见 ReportsSaOpenSql）。
    // 订单是否审核不限（同 order_execution）。overdue_lines 是预发货日期早于登录日期的行数。
    // 合计 totals 覆盖全部符合条件的行，不受 limit 影响。读线程，只用 ctx.Conn、登录日期和权限快照。
    internal static class ReportsSaOpen
    {
        public static ApiResult Run(WorkContext ctx, ReportArgs ignored)
        {
            OpenOrdersArgs a = SaArgs.Open(ctx.Item.Body);
            a.NoAmountOrder = PermMask.WillMaskAmounts(ctx);
            string today = ReportsStockSql.DateOr(ctx, "");
            List<object> filterArgs = new List<object>();
            string filter = ReportsSaOpenSql.Filter(ctx, a, filterArgs);
            List<object> ps = new List<object>();
            ps.Add(a.Limit + 1);
            ps.Add(today);
            ps.AddRange(filterArgs);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, ReportsSaOpenSql.Items(a, filter), ps.ToArray(), a.Limit + 1);
            bool truncated = SaCalc.Cut(rows, a.Limit);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count; i++)
            {
                items.Add(Item(rows[i], a.GroupBy));
            }
            Dictionary<string, object> body = SaCalc.Body(items, truncated);
            body["basis"] = a.Basis;
            body["group_by"] = a.GroupBy;
            body["order"] = a.GroupBy == "order" ? "due_date" : (a.NoAmountOrder ? "code" : "open_amount");
            body["as_of"] = today;
            body["totals"] = Totals(Rows.One(ctx.Conn, ReportsSaOpenSql.Sum(a, filter), filterArgs.ToArray()));
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row, string groupBy)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            if (groupBy == "customer")
            {
                item["customer_code"] = GlSql.Col(row, "customer_code");
                item["customer_name"] = Reports.Text(row, "customer_name");
            }
            else if (groupBy == "inventory")
            {
                Inventory(item, row);
            }
            else
            {
                item["so_code"] = GlSql.Col(row, "so_code");
                item["so_date"] = Reports.Text(row, "so_date");
                item["customer_name"] = Reports.Text(row, "customer_name");
                Inventory(item, row);
                item["due_date"] = Reports.Text(row, "due_date");
            }
            item["lines"] = GlSql.Int(row, "lines");
            item["open_qty"] = Reports.Qty(row, "open_qty");
            item["open_amount"] = SaCalc.Money(SaCalc.Dec(row, "open_amount"));
            item["overdue_lines"] = GlSql.Int(row, "overdue_lines");
            item["earliest_due"] = Reports.Text(row, "earliest_due");
            return item;
        }

        static void Inventory(Dictionary<string, object> item, Dictionary<string, object> row)
        {
            item["inv_code"] = GlSql.Col(row, "inv_code");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["unit"] = Reports.Text(row, "unit");
        }

        static Dictionary<string, object> Totals(Dictionary<string, object> row)
        {
            Dictionary<string, object> totals = new Dictionary<string, object>();
            totals["lines"] = GlSql.Int(row, "lines");
            totals["open_amount"] = SaCalc.Money(SaCalc.Dec(row, "open_amount"));
            totals["customers"] = GlSql.Int(row, "customers");
            return totals;
        }
    }
}
