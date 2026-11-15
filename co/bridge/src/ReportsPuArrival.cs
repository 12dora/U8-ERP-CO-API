using System.Collections.Generic;

namespace U8Co
{
    // 到货及时率 arrival_otif：已审核采购订单行，计划到货日（PO_Podetails.dArriveDate）落在 [date_from, date_to] 的，
    // 对照实际首次到货日统计准时、延迟、逾期未到，并列出逾期未到货的订单行（最早计划的在前）。as_of 取登录日期。
    // 首次到货 = 已审核到货单和已审核采购入库单（直接入库不开到货单）里最早的日期，与计划日比较（只比日期）：
    // 不晚于计划为准时 on_time，晚于为延迟 late。数据里多数订单行直接入库，只看到货单会漏掉绝大多数行。
    // 每个订单行恰好落在三类之一：arrived_lines（有已审核到货单或入库单）、open_overdue（没有，计划日早于 as_of）、
    // not_due（没有，计划日不早于 as_of），所以 lines = arrived_lines + open_overdue + not_due；只看已审核单据，部分到货的行算已到货。
    // 已关闭且从未到货的行视为作废，不进统计。逾期明细的 arrived_qty 是订单行上记录的到货累计（含未审核单据）。
    // group_by=none 时 groups 为空，只有 totals。数据权限：功能 report:arrival_otif，供应商、存货都必须放行。
    internal static class ReportsPuArrival
    {
        // 最多返回的分组数；多查一组判断 truncated。
        const int MaxGroups = 1000;
        static readonly string[] Counts = new string[]
        {
            "lines", "arrived_lines", "on_time", "late", "open_overdue", "not_due"
        };

        public static ApiResult Run(WorkContext ctx, ReportArgs ignored)
        {
            PuArgs a = ReportsPuReq.ParseArrival(ctx.Item.Body);
            string asOf = ReportsStockSql.DateOr(ctx, "");
            List<object> groups = a.Group == "none" ? new List<object>() : Groups(ctx, a, asOf);
            List<object> tp = new List<object>();
            string totalSql = ReportsPuSql.WithPerm(ReportsPuArrivalSql.TotalsSql(a, asOf, tp), ctx, tp);
            Dictionary<string, object> totals = Counted(Rows.One(ctx.Conn, totalSql, tp.ToArray()));
            Dictionary<string, object> body = Reports.Body();
            body["as_of"] = asOf;
            body["date_from"] = a.DateFrom;
            body["date_to"] = a.DateTo.Length > 0 ? a.DateTo : null;
            body["group_by"] = a.Group;
            body["arrival_basis"] = "arrival_or_receipt";
            body["truncated"] = groups.Count > MaxGroups;
            if (groups.Count > MaxGroups)
            {
                groups.RemoveAt(MaxGroups);
            }
            body["groups"] = groups;
            body["totals"] = totals;
            body["overdue"] = Overdue(ctx, a, asOf);
            body["overdue_total"] = totals["open_overdue"];
            return ApiResult.Ok(body);
        }

        static List<object> Groups(WorkContext ctx, PuArgs a, string asOf)
        {
            List<object> ps = new List<object>();
            string sql = ReportsPuSql.WithPerm(ReportsPuArrivalSql.GroupSql(a, asOf, ps), ctx, ps);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), MaxGroups + 1);
            List<object> groups = new List<object>();
            for (int i = 0; i < rows.Count; i++)
            {
                groups.Add(Group(rows[i], a.Group == "vendor"));
            }
            return groups;
        }

        static List<object> Overdue(WorkContext ctx, PuArgs a, string asOf)
        {
            List<object> ps = new List<object>();
            string sql = ReportsPuSql.WithPerm(ReportsPuArrivalSql.OverdueSql(a, asOf, ps), ctx, ps);
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql, ps.ToArray(), a.OverdueLimit);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count; i++)
            {
                items.Add(OverdueItem(rows[i]));
            }
            return items;
        }

        // 分组一行：键 + 各计数。
        internal static Dictionary<string, object> Group(Dictionary<string, object> row, bool vendor)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            if (vendor)
            {
                item["vendor_code"] = Reports.Text(row, "vendor_code");
                item["vendor_name"] = Reports.Text(row, "vendor_name");
            }
            else
            {
                item["inv_code"] = GlSql.Col(row, "inv_code");
                item["inv_name"] = Reports.Text(row, "inv_name");
                item["unit"] = Reports.Text(row, "unit");
            }
            foreach (KeyValuePair<string, object> pair in Counted(row))
            {
                item[pair.Key] = pair.Value;
            }
            return item;
        }

        internal static Dictionary<string, object> Counted(Dictionary<string, object> row)
        {
            Dictionary<string, object> counts = new Dictionary<string, object>();
            for (int i = 0; i < Counts.Length; i++)
            {
                counts[Counts[i]] = GlSql.Int(row, Counts[i]);
            }
            return counts;
        }

        internal static Dictionary<string, object> OverdueItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["po_code"] = GlSql.Col(row, "po_code");
            item["po_date"] = Reports.Text(row, "po_date");
            item["vendor_name"] = Reports.Text(row, "vendor_name");
            item["inv_code"] = GlSql.Col(row, "inv_code");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["unit"] = Reports.Text(row, "unit");
            item["qty"] = Reports.Qty(row, "qty");
            item["arrived_qty"] = Reports.Qty(row, "arrived_qty");
            item["planned_date"] = Reports.Text(row, "planned_date");
            item["days_overdue"] = GlSql.Int(row, "days_overdue");
            return item;
        }
    }
}
