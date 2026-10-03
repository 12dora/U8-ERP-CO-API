using System.Collections.Generic;
using System.Globalization;
using System.Text;

namespace U8Co
{
    // 货位存量 position_stock（InvPositionSum，按 Autoid 翻页）与批次存量 batch_stock（CurrentStock 按仓库、存货、批次汇总）。
    // 货位存量只含已指定货位的数量：入库后还没指定货位的部分只在现存量里（两者可能不等）。
    internal static class ReportsStockPos
    {
        // 参数：limit+1, after。
        const string PosSql = "SELECT TOP (?) p.Autoid id, p.cWhCode wh, w.cWhName wh_name, p.cPosCode pos, ps.cPosName pos_name,"
            + " p.cInvCode inv, i.cInvName inv_name, i.cInvStd inv_std, p.cBatch batch, p.cFree1 free1, p.cFree2 free2,"
            + " p.cFree3 free3, p.cFree4 free4, p.cFree5 free5, p.cFree6 free6, p.cFree7 free7, p.cFree8 free8,"
            + " p.cFree9 free9, p.cFree10 free10, CONVERT(decimal(28,6), p.iQuantity) qty, CONVERT(decimal(28,6), p.inum) qty_aux,"
            + " CONVERT(varchar(10), p.dMadeDate, 23) made, CONVERT(varchar(10), p.dVDate, 23) valid,"
            + " CONVERT(varchar(10), p.dExpirationdate, 23) expires"
            + " FROM InvPositionSum p LEFT JOIN Warehouse w ON w.cWhCode=p.cWhCode LEFT JOIN Position ps ON ps.cPosCode=p.cPosCode"
            + " LEFT JOIN Inventory i ON i.cInvCode=p.cInvCode WHERE p.Autoid>?";

        // 只要有批号的行；日期取各行最早的（空值不参与，避免聚合空值的警告）。{FILTER} 是仓库、存货、批次和数据权限。参数：过滤… | limit+1 | 过期日、游标。
        const string BatchSql = ";WITH b AS (SELECT cs.cWhCode wh, cs.cInvCode inv, cs.cBatch batch,"
            + " SUM(ISNULL(cs.iQuantity, 0)) qty, SUM(ISNULL(cs.iNum, 0)) qty_aux, SUM(ISNULL(cs.fStopQuantity, 0)) qty_frozen,"
            + " NULLIF(MIN(ISNULL(cs.dMdate, '99991231')), '99991231') made,"
            + " NULLIF(MIN(ISNULL(cs.dVDate, '99991231')), '99991231') valid,"
            + " NULLIF(MIN(ISNULL(cs.dExpirationdate, '99991231')), '99991231') expires, COUNT(*) n"
            + " FROM CurrentStock cs WHERE NULLIF(LTRIM(RTRIM(ISNULL(cs.cBatch, N''))), N'') IS NOT NULL{FILTER}"
            + " GROUP BY cs.cWhCode, cs.cInvCode, cs.cBatch)"
            + " SELECT TOP (?) b.wh, w.cWhName wh_name, b.inv, i.cInvName inv_name, i.cInvStd inv_std, b.batch,"
            + " CONVERT(decimal(28,6), b.qty) qty, CONVERT(decimal(28,6), b.qty_aux) qty_aux,"
            + " CONVERT(decimal(28,6), b.qty_frozen) qty_frozen, CONVERT(varchar(10), b.made, 23) made,"
            + " CONVERT(varchar(10), b.valid, 23) valid, CONVERT(varchar(10), b.expires, 23) expires, b.n"
            + " FROM b LEFT JOIN Warehouse w ON w.cWhCode=b.wh LEFT JOIN Inventory i ON i.cInvCode=b.inv WHERE 1=1";
        const string BatchAfter = " AND (b.wh>? OR (b.wh=? AND (b.inv>? OR (b.inv=? AND b.batch>?))))";

        public static ApiResult Positions(WorkContext ctx, ReportArgs a)
        {
            StockReportArgs s = ReportsStockReq.Parse(a, ctx.Item.Body);
            List<object> ps = new List<object>(new object[] { a.Limit + 1, ReportsStockSql.AfterId(a.After) });
            StringBuilder sql = new StringBuilder(PosSql);
            ReportsStockSql.Equal(sql, ps, "p.cWhCode", s.Wh);
            ReportsStockSql.Equal(sql, ps, "p.cInvCode", s.Inv);
            ReportsStockSql.Equal(sql, ps, "p.cBatch", s.Batch);
            if (s.Position.Length > 0)
            {
                sql.Append(" AND p.cPosCode LIKE ?");
                ps.Add(s.Position + "%");
            }
            if (s.NonZero)
            {
                sql.Append(" AND ISNULL(p.iQuantity, 0)<>0");
            }
            // 数据权限：仓库、存货。
            PermHook.Where(sql, ps, ctx, "p");
            sql.Append(" ORDER BY p.Autoid");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(PosItem(rows[i]));
            }
            string next = rows.Count > a.Limit ? Reports.Cursor(GlSql.Col(rows[a.Limit - 1], "id")) : null;
            return ApiResult.Ok(ReportsStockSql.Page(items, next));
        }

        static Dictionary<string, object> PosItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["id"] = GlSql.Int(row, "id");
            item["wh_code"] = Reports.Text(row, "wh");
            item["wh_name"] = Reports.Text(row, "wh_name");
            item["position"] = Reports.Text(row, "pos");
            item["position_name"] = Reports.Text(row, "pos_name");
            item["inv_code"] = Reports.Text(row, "inv");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["inv_std"] = Reports.Text(row, "inv_std");
            item["batch"] = Reports.Text(row, "batch");
            for (int k = 1; k <= 10; k++)
            {
                string key = "free" + k.ToString(CultureInfo.InvariantCulture);
                item[key] = Reports.Text(row, key);
            }
            item["qty"] = Reports.Qty(row, "qty");
            item["qty_aux"] = ReportsStockSql.OptQty(row, "qty_aux");
            Dates(item, row);
            return item;
        }

        static void Dates(Dictionary<string, object> item, Dictionary<string, object> row)
        {
            item["made_date"] = Reports.Text(row, "made");
            item["valid_until"] = Reports.Text(row, "valid");
            item["expires"] = Reports.Text(row, "expires");
        }

        public static ApiResult Batches(WorkContext ctx, ReportArgs a)
        {
            StockReportArgs s = ReportsStockReq.Parse(a, ctx.Item.Body);
            List<object> ps = new List<object>();
            StringBuilder filter = new StringBuilder();
            ReportsStockSql.Equal(filter, ps, "cs.cWhCode", s.Wh);
            ReportsStockSql.Equal(filter, ps, "cs.cInvCode", s.Inv);
            ReportsStockSql.Equal(filter, ps, "cs.cBatch", s.Batch);
            // 数据权限：仓库、存货，按 CurrentStock 的原始行过滤。
            PermHook.Where(filter, ps, ctx, "cs");
            ps.Add(a.Limit + 1);
            StringBuilder sql = new StringBuilder(BatchSql.Replace("{FILTER}", filter.ToString()));
            if (s.NonZero)
            {
                sql.Append(" AND ROUND(b.qty, 6)<>0");
            }
            if (s.ExpiringBefore.Length > 0)
            {
                sql.Append(" AND b.valid<DATEADD(day, 1, CONVERT(date, ?, 23))");
                ps.Add(s.ExpiringBefore);
            }
            string[] after = Reports.Uncursor(a.After, 3);
            if (after != null)
            {
                sql.Append(BatchAfter);
                ps.AddRange(new object[] { after[0], after[0], after[1], after[1], after[2] });
            }
            sql.Append(" ORDER BY b.wh, b.inv, b.batch");
            List<Dictionary<string, object>> rows = Rows.Query(ctx.Conn, sql.ToString(), ps.ToArray(), a.Limit + 1);
            List<object> items = new List<object>();
            for (int i = 0; i < rows.Count && i < a.Limit; i++)
            {
                items.Add(BatchItem(rows[i]));
            }
            string next = rows.Count > a.Limit ? BatchNext(rows[a.Limit - 1]) : null;
            Dictionary<string, object> body = ReportsStockSql.Page(items, next);
            body["expiring_before"] = s.ExpiringBefore.Length == 0 ? null : s.ExpiringBefore;
            return ApiResult.Ok(body);
        }

        static Dictionary<string, object> BatchItem(Dictionary<string, object> row)
        {
            Dictionary<string, object> item = new Dictionary<string, object>();
            item["wh_code"] = Reports.Text(row, "wh");
            item["wh_name"] = Reports.Text(row, "wh_name");
            item["inv_code"] = Reports.Text(row, "inv");
            item["inv_name"] = Reports.Text(row, "inv_name");
            item["inv_std"] = Reports.Text(row, "inv_std");
            item["batch"] = Reports.Text(row, "batch");
            item["qty"] = Reports.Qty(row, "qty");
            item["qty_aux"] = Reports.Qty(row, "qty_aux");
            item["qty_frozen"] = Reports.Qty(row, "qty_frozen");
            Dates(item, row);
            item["rows"] = GlSql.Int(row, "n");
            return item;
        }

        static string BatchNext(Dictionary<string, object> row)
        {
            return Reports.Cursor(GlSql.Col(row, "wh"), GlSql.Col(row, "inv"), GlSql.Col(row, "batch"));
        }
    }
}
