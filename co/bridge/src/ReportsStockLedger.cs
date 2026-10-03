using System;
using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 库存台账 stock_ledger：一个存货在日期区间内的每一行收发（ReportsStockSql.Moves），带期初和逐行结存。
    // 缺省只含已审核单据（与现存量一致）；include_unverified 为 true 时连未审核的一起算（期初也含）。
    // 结存 = 期初 + 之前各行的入库 − 出库；翻到第 N 页时，本页之前的结存（carry）按游标之前的全部行现算，不信任调用方。
    internal static class ReportsStockLedger
    {
        const string SumSql = "SELECT CONVERT(decimal(28,6), ISNULL(SUM(" + ReportsStockSql.Signed + "), 0)) q"
            + " FROM {M} WHERE m.cInvCode=?";
        const string BeforeFrom = " AND m.ddate<CONVERT(date, ?, 23)";
        const string UpTo = " AND (CONVERT(date, m.ddate)<CONVERT(date, ?, 23)"
            + " OR (CONVERT(date, m.ddate)=CONVERT(date, ?, 23) AND m.autoid<=?))";
        const string AfterSql = " AND (CONVERT(date, m.ddate)>CONVERT(date, ?, 23)"
            + " OR (CONVERT(date, m.ddate)=CONVERT(date, ?, 23) AND m.autoid>?))";
        // 参数：limit+1, inv, date_from, date_to。
        const string RowSql = "SELECT TOP (?) m.vt, m.id, m.autoid, m.code, CONVERT(varchar(10), m.ddate, 23) ddate, m.rd,"
            + " m.cWhCode wh, w.cWhName wh_name, m.batch, CONVERT(decimal(28,6), m.qty) qty,"
            + " CASE WHEN ISNULL(m.handler, N'')=N'' THEN 0 ELSE 1 END verified, m.rdcode, m.src"
            + " FROM {M} LEFT JOIN Warehouse w ON w.cWhCode=m.cWhCode"
            + " WHERE m.cInvCode=? AND m.ddate>=CONVERT(date, ?, 23) AND m.ddate<DATEADD(day, 1, CONVERT(date, ?, 23))";
        const string InvSql = "SELECT i.cInvName inv_name, i.cInvStd inv_std FROM Inventory i WHERE i.cInvCode=?";

        public static ApiResult Ledger(WorkContext ctx, ReportArgs a)
        {
            StockReportArgs s = ReportsStockReq.Parse(a, ctx.Item.Body);
            string to = ReportsStockSql.DateOr(ctx, s.DateTo);
            if (string.CompareOrdinal(s.DateFrom, to) > 0)
            {
                throw GlReq.Bad("date_from 不能晚于 date_to（缺省为登录日期）");
            }
            Dictionary<string, object> inv = Rows.One(ctx.Conn, InvSql, new object[] { s.Inv });
            if (inv == null)
            {
                throw new BridgeException(404, "not_found", "存货不存在");
            }
            // 数据权限：存货不放行 403；仓库不放行的行不列、也不计入期初和结存。
            PermHook.Code(ctx, PermObj.Inventory, s.Inv);
            decimal opening = Sum(ctx, s, BeforeFrom, new object[] { s.DateFrom });
            decimal carry = opening;
            List<object> ps = new List<object>(new object[] { a.Limit + 1, s.Inv, s.DateFrom, to });
            StringBuilder sql = new StringBuilder(RowSql.Replace("{M}", ReportsStockSql.Moves));
            object[] cursor = Cursor(a.After);
            if (cursor != null)
            {
                carry = Sum(ctx, s, UpTo, cursor);
                sql.Append(AfterSql);
                ps.AddRange(cursor);
            }
            ReportsStockSql.MoveFilter(sql, ps, ctx, s);
            sql.Append(" ORDER BY CONVERT(date, m.ddate), m.autoid");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            decimal balance = carry;
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(Item(rows[i], ref balance));
            }
            bool more = rows.Count > a.Limit;
            Dictionary<string, object> body = ReportsStockSql.Page(items, more ? Next(rows[a.Limit - 1]) : null);
            body["inv"] = s.Inv;
            body["inv_name"] = Reports.Text(inv, "inv_name");
            body["inv_std"] = Reports.Text(inv, "inv_std");
            body["wh"] = s.Wh.Length == 0 ? null : s.Wh;
            body["batch"] = s.Batch.Length == 0 ? null : s.Batch;
            body["date_from"] = s.DateFrom;
            body["date_to"] = to;
            body["include_unverified"] = s.Unverified;
            body["opening"] = opening;
            body["carry"] = carry;
            body["closing"] = more ? (object)null : balance;
            return ApiResult.Ok(body);
        }

        static decimal Sum(WorkContext ctx, StockReportArgs s, string cond, object[] condArgs)
        {
            List<object> ps = new List<object>();
            ps.Add(s.Inv);
            ps.AddRange(condArgs);
            StringBuilder sql = new StringBuilder(SumSql.Replace("{M}", ReportsStockSql.Moves)).Append(cond);
            ReportsStockSql.MoveFilter(sql, ps, ctx, s);
            Dictionary<string, object> row = Rows.One(ctx.Conn, sql.ToString(), ps.ToArray());
            return Reports.Qty(row, "q");
        }

        static Dictionary<string, object> Item(Dictionary<string, object> row, ref decimal balance)
        {
            bool inbound = GlSql.Bit(row, "rd");
            decimal qty = Reports.Qty(row, "qty");
            balance += inbound ? qty : -qty;
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["date"] = GlSql.Col(row, "ddate");
            item["type"] = ReportsStockSql.TypeOf(GlSql.Col(row, "vt"));
            item["id"] = GlSql.Int(row, "id");
            item["line_id"] = GlSql.Int(row, "autoid");
            item["code"] = Reports.Text(row, "code");
            item["wh_code"] = Reports.Text(row, "wh");
            item["wh_name"] = Reports.Text(row, "wh_name");
            item["batch"] = Reports.Text(row, "batch");
            item["rd_code"] = Reports.Text(row, "rdcode");
            item["source"] = Reports.Text(row, "src");
            item["verified"] = GlSql.Bit(row, "verified");
            item["in_qty"] = inbound ? qty : 0m;
            item["out_qty"] = inbound ? 0m : qty;
            item["balance"] = balance;
            return item;
        }

        static string Next(Dictionary<string, object> row)
        {
            return Reports.Cursor(GlSql.Col(row, "ddate"), GlSql.Col(row, "autoid"));
        }

        // 游标：（日期 yyyy-MM-dd、表体 AutoID）。参数顺序与 AfterSql / UpTo 一致：日期、日期、AutoID。
        static object[] Cursor(string after)
        {
            string[] parts = Reports.Uncursor(after, 2);
            if (parts == null)
            {
                return null;
            }
            DateTime day;
            int autoid;
            if (!DateTime.TryParseExact(parts[0], "yyyy-MM-dd", CultureInfo.InvariantCulture, DateTimeStyles.None, out day)
                || !int.TryParse(parts[1], NumberStyles.Integer, CultureInfo.InvariantCulture, out autoid))
            {
                throw GlReq.Bad("after 游标无效");
            }
            return new object[] { parts[0], parts[0], autoid };
        }
    }
}
