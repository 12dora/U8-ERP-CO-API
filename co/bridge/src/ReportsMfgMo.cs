using System.Collections.Generic;

namespace U8Co
{
    // 生产订单完工状况 mo_progress（每个订单行一行：数量、合格入库数量、状态）和生产订单用料分析 mo_consumption
    // （区间内已审核的订单按母件存货合计，再按子件列出标准用量 bom_qty 和已领数量 issued_qty）。
    // 订单按制单日期（mom_order.CreateDate）取区间。单耗、完工率等比值由调用方用这些数量算，这里只给数量。
    // 响应 items、count（返回的行数）、truncated（超过 limit 被截掉）。
    internal static class ReportsMfgMo
    {
        internal static ApiResult Progress(WorkContext ctx, ReportArgs a)
        {
            MfgArgs m = ReportsMfgReq.Parse(a.Name, ctx.Item.Body);
            List<object> ps = new List<object>();
            string sql = ReportsMfgMoSql.Progress(m, ReportsStockSql.DateOr(ctx, m.DateTo), ps, ctx);
            return Page(ctx, sql, ps, m.Limit, false);
        }

        internal static ApiResult Consumption(WorkContext ctx, ReportArgs a)
        {
            MfgArgs m = ReportsMfgReq.Parse(a.Name, ctx.Item.Body);
            List<object> ps = new List<object>();
            string sql = ReportsMfgMoSql.Usage(m, ReportsStockSql.DateOr(ctx, m.DateTo), ps, ctx);
            return Page(ctx, sql, ps, m.Limit, true);
        }

        static ApiResult Page(WorkContext ctx, string sql, List<object> ps, int limit, bool usage)
        {
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < limit; i++)
            {
                items.Add(usage ? UsageItem(rows[i]) : ProgressItem(rows[i]));
            }
            Dictionary<string, object> body = Reports.Body();
            body["items"] = items;
            body["count"] = items.Count;
            body["truncated"] = rows.Count > limit;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> ProgressItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            int status = GlSql.Int(row, "status");
            item["mo_code"] = GlSql.Col(row, "mo_code");
            item["mo_line"] = GlSql.Int(row, "mo_line");
            item["create_date"] = Reports.Text(row, "create_date");
            item["start_date"] = Reports.Text(row, "start_date");
            item["due_date"] = Reports.Text(row, "due_date");
            item["inv_code"] = GlSql.Col(row, "inv_code");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["unit"] = Reports.Text(row, "unit");
            item["dept_name"] = Reports.Text(row, "dept_name");
            item["qty"] = Reports.Qty(row, "qty");
            item["qualified_in"] = Reports.Qty(row, "qualified_in");
            item["closed"] = status == ClosedStatus;
            item["status"] = StatusText(status);
            return item;
        }

        static Dictionary<string, object> UsageItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["parent_code"] = GlSql.Col(row, "parent_code");
            item["parent_name"] = Reports.Text(row, "parent_name");
            item["parent_unit"] = Reports.Text(row, "parent_unit");
            item["mo_count"] = GlSql.Int(row, "mo_count");
            item["mo_qty"] = Reports.Qty(row, "mo_qty");
            item["qualified_in"] = Reports.Qty(row, "qualified_in");
            item["comp_code"] = GlSql.Col(row, "comp_code");
            item["comp_name"] = Reports.Text(row, "comp_name");
            item["comp_unit"] = Reports.Text(row, "comp_unit");
            item["bom_qty"] = Reports.Qty(row, "bom_qty");
            item["issued_qty"] = Reports.Qty(row, "issued_qty");
            return item;
        }

        // 订单行 Status：4 已关闭；3 已审核（生产中）；1、2 未审核（计划）。U8 订单行上没有单独的「锁定」状态，不输出 locked。
        internal const int ClosedStatus = 4;

        internal static string StatusText(int status)
        {
            if (status == ClosedStatus)
            {
                return "closed";
            }
            return status == 3 ? "open" : "planned";
        }
    }
}
