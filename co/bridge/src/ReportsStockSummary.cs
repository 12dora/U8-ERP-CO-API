using System.Collections.Generic;
using System.Text;

namespace U8Co
{
    // 收发存汇总表 stock_summary：按存货（by_wh 为 true 时再按仓库）汇总日期区间的期初、入库、出库、结存数量。
    // 来源与库存台账相同（ReportsStockSql.Moves），缺省只含已审核单据；截至今天的结存等于现存量。
    // 只有数量：金额在存货核算（IA）里，出库成本要记账后才有，这里不给。
    internal static class ReportsStockSummary
    {
        // {WH} 是仓库列或空串常量，{WHG} 是仓库的分组列（常量不能进 GROUP BY）。
        // 参数：from, from, from, to, 过滤… | limit+1 | nonzero、游标。
        const string SumSql = ";WITH s AS (SELECT m.cInvCode inv, {WH} wh,"
            + " SUM(CASE WHEN m.ddate<CONVERT(date, ?, 23) THEN " + ReportsStockSql.Signed + " ELSE 0 END) op,"
            + " SUM(CASE WHEN m.ddate>=CONVERT(date, ?, 23) AND m.rd=1 THEN m.qty ELSE 0 END) inq,"
            + " SUM(CASE WHEN m.ddate>=CONVERT(date, ?, 23) AND m.rd<>1 THEN m.qty ELSE 0 END) outq"
            + " FROM {M} WHERE m.ddate<DATEADD(day, 1, CONVERT(date, ?, 23)){FILTER} GROUP BY m.cInvCode{WHG})"
            + " SELECT TOP (?) s.inv, i.cInvName inv_name, i.cInvStd inv_std, i.cInvCCode inv_class, s.wh, w.cWhName wh_name,"
            + " CONVERT(decimal(28,6), s.op) op, CONVERT(decimal(28,6), s.inq) inq, CONVERT(decimal(28,6), s.outq) outq,"
            + " CONVERT(decimal(28,6), s.op+s.inq-s.outq) cl FROM s LEFT JOIN Inventory i ON i.cInvCode=s.inv"
            + " LEFT JOIN Warehouse w ON w.cWhCode=s.wh WHERE 1=1";
        const string NonZeroSql = " AND (ROUND(s.op, 6)<>0 OR ROUND(s.inq, 6)<>0 OR ROUND(s.outq, 6)<>0)";
        const string AfterSql = " AND (s.inv>? OR (s.inv=? AND s.wh>?))";

        public static ApiResult Summary(WorkContext ctx, ReportArgs a)
        {
            StockReportArgs s = ReportsStockReq.Parse(a, ctx.Item.Body);
            string to = ReportsStockSql.DateOr(ctx, s.DateTo);
            if (string.CompareOrdinal(s.DateFrom, to) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to（缺省为登录日期）");
            }
            List<object> ps = new List<object>(new object[] { s.DateFrom, s.DateFrom, s.DateFrom, to });
            string filter = Filter(ctx, s, ps);
            ps.Add(a.Limit + 1);
            StringBuilder sql = new StringBuilder(SumSql.Replace("{M}", ReportsStockSql.Moves)
                .Replace("{WH}", s.ByWh ? "m.cWhCode" : "N''").Replace("{WHG}", s.ByWh ? ", m.cWhCode" : "")
                .Replace("{FILTER}", filter));
            if (s.NonZero)
            {
                sql.Append(NonZeroSql);
            }
            string[] after = Reports.Uncursor(a.After, 2);
            if (after != null)
            {
                sql.Append(AfterSql);
                ps.AddRange(new object[] { after[0], after[0], after[1] });
            }
            sql.Append(" ORDER BY s.inv, s.wh");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(Item(rows[i], s.ByWh));
            }
            string next = rows.Count > a.Limit
                ? Reports.Cursor(GlSql.Col(rows[a.Limit - 1], "inv"), GlSql.Col(rows[a.Limit - 1], "wh")) : null;
            Dictionary<string, object> body = ReportsStockSql.Page(items, next);
            body["date_from"] = s.DateFrom;
            body["date_to"] = to;
            body["by_wh"] = s.ByWh;
            body["include_unverified"] = s.Unverified;
            return ApiResult.Ok(body);
        }

        // 存货、存货分类前缀（只进参数），然后是仓库、审核状态和数据权限。
        static string Filter(WorkContext ctx, StockReportArgs s, List<object> ps)
        {
            StringBuilder sb = new StringBuilder();
            ReportsStockSql.Equal(sb, ps, "m.cInvCode", s.Inv);
            if (s.InvClass.Length > 0)
            {
                sb.Append(" AND m.cInvCode IN (SELECT ic.cInvCode FROM Inventory ic WHERE ic.cInvCCode LIKE ?)");
                ps.Add(s.InvClass + "%");
            }
            StockReportArgs moves = new StockReportArgs();
            moves.Wh = s.Wh;
            moves.Unverified = s.Unverified;
            ReportsStockSql.MoveFilter(sb, ps, ctx, moves);
            return sb.ToString();
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row, bool byWh)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["inv_code"] = GlSql.Col(row, "inv");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["inv_std"] = Reports.Text(row, "inv_std");
            item["inv_class"] = Reports.Text(row, "inv_class");
            item["wh_code"] = byWh ? Reports.Text(row, "wh") : null;
            item["wh_name"] = byWh ? Reports.Text(row, "wh_name") : null;
            item["opening"] = Reports.Qty(row, "op");
            item["in_qty"] = Reports.Qty(row, "inq");
            item["out_qty"] = Reports.Qty(row, "outq");
            item["closing"] = Reports.Qty(row, "cl");
            return item;
        }
    }
}
