using System.Collections.Generic;

namespace U8Co
{
    // 产成品入库日报 production_daily：按日（或按月）、按产品（或存货分类）汇总已审核产成品入库的数量和单据数。
    // 数量是存货主计量单位下的数量（unit 取计量单位名称，换算成吨等由调用方做）；按分类汇总时不给单位（同类存货的单位可能不同）。
    // 响应 items、count（返回的行数）、truncated（超过 limit 被截掉）、days（窗口内有入库的日期个数，不受 limit 影响）。
    internal static class ReportsMfgDaily
    {
        internal static ApiResult Run(WorkContext ctx, ReportArgs a)
        {
            MfgArgs m = ReportsMfgReq.Parse(a.Name, ctx.Item.Body);
            string to = ReportsStockSql.DateOr(ctx, m.DateTo);
            List<object> ps = new List<object>();
            string sql = ReportsMfgDailySql.Build(m, to, ps, ctx);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), m.Limit + 1);
            bool byClass = m.By == "class";
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < m.Limit; i++)
            {
                items.Add(Item(rows[i], byClass));
            }
            List<object> dayPs = new List<object>();
            string daySql = ReportsMfgDailySql.Days(m, to, dayPs, ctx);
            Dictionary<string, object> one = Rows.One(ctx.Conn, daySql, dayPs.ToArray());
            Dictionary<string, object> body = Reports.Body();
            body["items"] = items;
            body["count"] = items.Count;
            body["truncated"] = rows.Count > m.Limit;
            body["days"] = GlSql.Int(one, "days");
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row, bool byClass)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["date"] = GlSql.Col(row, "d");
            if (byClass)
            {
                item["class_code"] = Reports.Text(row, "class_code");
                item["class_name"] = Reports.Text(row, "class_name");
            }
            else
            {
                item["inv_code"] = GlSql.Col(row, "inv_code");
                item["inv_name"] = Reports.Text(row, "inv_name");
                item["unit"] = Reports.Text(row, "unit");
            }
            item["qty"] = Reports.Qty(row, "qty");
            item["docs"] = GlSql.Int(row, "docs");
            return item;
        }
    }
}
